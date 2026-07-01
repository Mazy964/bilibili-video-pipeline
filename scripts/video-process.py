#!/usr/bin/env python3
"""Process any video: download + transcribe + archive. Two-phase for efficiency.
Usage:
  video-process.py <URL> --download-only        # Phase 1: download video+audio only
  video-process.py <URL> --transcribe-only       # Phase 2: transcribe all pending audio
  video-process.py <URL>                         # Full pipeline (download + transcribe)
  video-process.py <URL> --parts 1,3-5           # Specific parts
  video-process.py <URL> --no-video              # Skip video download
  video-process.py <URL> --workers 5             # Parallel downloads (default 3)
"""
import sys, subprocess, tempfile, os, re, json, yaml, time, urllib.request, ssl, glob, concurrent.futures, threading

os.environ['PYTHONUNBUFFERED'] = '1'

URL = sys.argv[1] if len(sys.argv) > 1 else ""
if not URL:
    print("Usage: video-process.py <URL> [--download-only|--transcribe-only] ...", file=sys.stderr)
    sys.exit(1)

# Resolve b23.tv short links
if "b23.tv" in URL:
    import urllib.request
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as resp:
            URL = resp.geturl()
            print(f"\U0001F517 短链解析: {URL}", file=sys.stderr)
    except Exception:
        pass

DOWNLOAD_ONLY = "--download-only" in sys.argv
TRANSCRIBE_ONLY = "--transcribe-only" in sys.argv
SKIP_VIDEO = "--no-video" in sys.argv
WHISPER_MODEL = "medium"
PARTS_FILTER = None
WORKERS = 3  # parallel downloads
QUALITY = None  # None=auto, "tech"=large-v3, "normal"=medium
DIARIZE = False  # enable speaker diarization via whisperx
HF_TOKEN = None  # HF token for gated diarization models

def auto_quality(title, uploader):
    """Detect tech/tutorial content for large-v3"""
    tech_keywords = ["教程", "实战", "构建", "开发", "编程", "代码", "部署", "架构",
                     "AI", "Agent", "模型", "训练", "算法", "源码", "配置", "安装",
                     "从零", "深入", "原理", "核心", "设计", "工程", "生产级",
                     "coding", "programming", "tutorial", "deep", "dive"]
    text = f"{title} {uploader}".lower()
    for kw in tech_keywords:
        if kw.lower() in text:
            return "large-v3"
    return "medium"

for i, a in enumerate(sys.argv):
    if a == "--model" and i + 1 < len(sys.argv):
        WHISPER_MODEL = sys.argv[i + 1]
    if a == "--parts" and i + 1 < len(sys.argv):
        parts = set()
        for seg in sys.argv[i + 1].split(','):
            if '-' in seg:
                a, b = seg.split('-', 1)
                parts.update(range(int(a), int(b) + 1))
            else:
                parts.add(int(seg))
        PARTS_FILTER = sorted(parts)
    if a == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])
    if a == "--quality" and i + 1 < len(sys.argv):
        QUALITY = sys.argv[i + 1]  # "tech" or "normal"
    if a == "--diarize":
        DIARIZE = True
    if a == "--hf-token" and i + 1 < len(sys.argv):
        HF_TOKEN = sys.argv[i + 1]

VENV = "/Users/mazy/.hermes/hermes-agent/venv/bin"
PYTHON = f"{VENV}/python3"
YT_DLP = [PYTHON, "-m", "yt_dlp"]
COOKIE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")
ROOT = os.path.expanduser("~/videos")
SSL_CTX = ssl.create_default_context()
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Referer": "https://www.bilibili.com/"}

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def sanitize(s):
    return re.sub(r'[<>:"/\\|?*]', '_', re.sub(r'\s+', '_', s))[:80].strip('_')

# ── Bilibili multi-part detection ───────────────────────────

def bilibili_pages(url):
    m = re.search(r'BV[\w]+', url)
    if not m: return None, None
    bvid = m.group(0)
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(api, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            data = json.loads(resp.read())["data"]
    except: return None, None
    pages = data.get("pages", [])
    title = data["title"]
    uploader = data["owner"]["name"]
    return ([(p["page"], p["part"], p["duration"]) for p in pages], title, uploader, bvid)

# ── Download ────────────────────────────────────────────────

def download_part(bvid, page_num, part_dir, ytdl_args):
    """Download video + audio for one part. Returns audio_path or None."""
    part_url = f"https://www.bilibili.com/video/{bvid}/?p={page_num}"
    os.makedirs(part_dir, exist_ok=True)
    
    # Download video
    if not SKIP_VIDEO:
        video_path = os.path.join(part_dir, "video.mp4")
        if not os.path.exists(video_path):
            print(f"  [视频]", end="", file=sys.stderr)
            r = run(YT_DLP + ytdl_args + [part_url,
                "-f", "bestvideo*+bestaudio/bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--output", video_path, "--no-warnings"], timeout=1200)
            if r.returncode == 0 and os.path.exists(video_path):
                print(f" {os.path.getsize(video_path)//1048576}MB", file=sys.stderr)
            else:
                print(" FAIL", file=sys.stderr)
    
    # Download audio for later transcription
    audio_path = os.path.join(part_dir, "audio.m4a")
    if not os.path.exists(audio_path):
        print(f"  [音频]", end="", file=sys.stderr)
        r = run(YT_DLP + ytdl_args + [part_url,
            "-x", "--audio-format", "m4a", "--audio-quality", "0",
            "--output", audio_path, "--no-warnings"], timeout=1200)
        if r.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            print(f" {os.path.getsize(audio_path)//1024}KB", file=sys.stderr)
        else:
            print(" FAIL", file=sys.stderr)
            return None
    
    # Try subtitles too
    sub_path = os.path.join(part_dir, "subtitle.vtt")
    if not os.path.exists(sub_path):
        with tempfile.TemporaryDirectory() as tmp:
            run(YT_DLP + ytdl_args + [part_url,
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "zh-Hans,zh-CN,zh,en",
                "--skip-download", "--output", f"{tmp}/%(id)s", "--no-warnings"], timeout=120)
            for root, _, files in os.walk(tmp):
                for f in files:
                    if f.endswith('.vtt'):
                        shutil = __import__('shutil')
                        shutil.copy(os.path.join(root, f), sub_path)
                        print(f"  [字幕] {f}", file=sys.stderr)
                        break
    
    return audio_path

# ── Transcribe ──────────────────────────────────────────────

WHISPERX = f"{VENV}/whisperx"
FFMPEG7_LIB = "/opt/homebrew/opt/ffmpeg@7/lib"
if os.path.isdir(FFMPEG7_LIB):
    os.environ["DYLD_LIBRARY_PATH"] = FFMPEG7_LIB + ":" + os.environ.get("DYLD_LIBRARY_PATH", "")

def whisper_transcribe(audio_path):
    code = '''
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from faster_whisper import WhisperModel
import sys
m = WhisperModel("small", device="cpu", compute_type="int8")
segs, info = m.transcribe("''' + audio_path + '''", beam_size=5, language=None)
print("LANG:" + info.language, file=sys.stderr)
for s in segs:
    print("[%06.1fs] %s" % (s.start, s.text.strip()))
'''
    r = run([PYTHON, "-c", code], timeout=1200)
    return r.stdout if r.returncode == 0 else None

def whisperx_diarize(audio_path, model="medium", output_dir=None, token=None):
    """Transcribe with speaker diarization via whisperx CLI."""
    import tempfile
    if output_dir is None:
        output_dir = tempfile.mkdtemp()
    os.makedirs(output_dir, exist_ok=True)
    
    env = os.environ.copy()
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    
    cmd = [WHISPERX, audio_path,
           "--model", model,
           "--device", "cpu",
           "--language", "zh",
           "--diarize",
           "--output_dir", output_dir,
           "--output_format", "txt",
           "--no_align"]  # skip alignment for speed; transcript still good
    
    if token:
        cmd += ["--hf_token", token]
    
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1200)
    
    # Check for output file
    base = os.path.splitext(os.path.basename(audio_path))[0]
    txt_file = os.path.join(output_dir, f"{base}.txt")
    
    if os.path.exists(txt_file):
        with open(txt_file) as f:
            return f.read()
    
    # Fallback: parse from stdout
    lines = []
    for line in r.stderr.split('\n'):
        if line.startswith("Transcript:"):
            lines.append(line[len("Transcript: "):])
    if lines:
        return '\n'.join(lines)
    
    return None if r.returncode != 0 else r.stdout.strip()

def transcribe_part(part_dir, part_title):
    """Transcribe audio for one part. Writes transcript files."""
    # Check if already done
    if os.path.exists(os.path.join(part_dir, "transcript.txt")):
        with open(os.path.join(part_dir, "transcript.txt")) as f:
            text = f.read()
        return text if text.strip() else None
    
    # Try subtitle first
    text = None
    source = None
    sub_path = os.path.join(part_dir, "subtitle.vtt")
    if os.path.exists(sub_path):
        with open(sub_path) as f:
            raw = f.read()
        # Strip VTT tags, keep timestamps as markdown
        clean = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line or line.isdigit() or '-->' in line:
                continue
            # Remove VTT tags like <c> <00:00:01.234>
            line = re.sub(r'<[^>]+>', '', line)
            if line:
                clean.append(line)
        if clean:
            text = '\n'.join(clean)
            source = "subtitle"
    
    # Fallback to whisper / whisperx
    if not text:
        audio_path = os.path.join(part_dir, "audio.m4a")
        if not os.path.exists(audio_path):
            # Find any audio file
            audios = glob.glob(os.path.join(part_dir, "*.m4a")) + glob.glob(os.path.join(part_dir, "*.mp3"))
            if not audios:
                return None
            audio_path = audios[0]
        if DIARIZE:
            print(f"  [转录: {WHISPER_MODEL} + 说话人分离]", end="", file=sys.stderr)
            text = whisperx_diarize(audio_path, model=WHISPER_MODEL, output_dir=part_dir, token=HF_TOKEN)
            source = "whisperx-diarize"
        else:
            print(f"  [转录: {WHISPER_MODEL}]", end="", file=sys.stderr)
            text = whisper_transcribe(audio_path)
            source = "whisper"
    
    if not text:
        return None
    
    # Write plain transcript
    with open(os.path.join(part_dir, "transcript.txt"), "w") as f:
        f.write(text)
    
    # Write markdown with header
    with open(os.path.join(part_dir, "transcript.md"), "w") as f:
        f.write(f"# {part_title}\n\n*来源: {source}*\n\n{text}")
    
    # Write info
    info = {"part_title": part_title, "source": source, "chars": len(text)}
    with open(os.path.join(part_dir, "info.yaml"), "w") as f:
        yaml.dump(info, f, allow_unicode=True)
    
    # Count non-empty lines
    clean_lines = [l for l in text.split('\n') if l.strip()]
    print(f"  [OK] {len(clean_lines)} 行 {info['chars']} 字", file=sys.stderr)
    return text

# ── Main ────────────────────────────────────────────────────

ytdl_args = ["--cookies", COOKIE, "--socket-timeout", "60", "--retries", "10", "--downloader", "aria2c"] if os.path.exists(COOKIE) else ["--socket-timeout", "60", "--retries", "10", "--downloader", "aria2c"]
pages, main_title, uploader, bvid = bilibili_pages(URL)
is_multi = pages and len(pages) > 1

# Auto-select whisper model based on content type
if QUALITY:
    WHISPER_MODEL = "large-v3" if QUALITY == "tech" else "medium"
elif main_title and not DOWNLOAD_ONLY:
    WHISPER_MODEL = auto_quality(main_title, uploader or "")

if is_multi:
    if PARTS_FILTER:
        pages = [(p, t, d) for p, t, d in pages if p in PARTS_FILTER]
    
    main_dir = os.path.join(ROOT, f"{sanitize(uploader)}_{sanitize(main_title)}")
    os.makedirs(main_dir, exist_ok=True)
    
    # Master info
    master_info = {"title": main_title, "uploader": uploader, "total_parts": len(pages), "bvid": bvid,
                   "parts": [{"page": p, "title": t, "duration": d} for p, t, d in pages]}
    with open(os.path.join(main_dir, "info.yaml"), "w") as f:
        yaml.dump(master_info, f, allow_unicode=True)
    
    if TRANSCRIBE_ONLY:
        print(f"\U0001F399️ 转录模式: {main_dir} ({len(pages)}P) · whisper:{WHISPER_MODEL}", file=sys.stderr)
    else:
        mode_str = "下载模式" if DOWNLOAD_ONLY else "全流程"
        print(f"\U0001F4E5 {mode_str}: {main_dir} ({len(pages)}P)", file=sys.stderr)
        if not DOWNLOAD_ONLY:
            print(f"\U0001F9E0 whisper: {WHISPER_MODEL}", file=sys.stderr)
        if WORKERS > 1:
            print(f"\u26A1 {WORKERS} 线程并行下载", file=sys.stderr)
    
    print_lock = threading.Lock()
    
    def download_one(page_num, part_title, duration):
        part_dir = os.path.join(main_dir, f"P{page_num:02d}_{sanitize(part_title)}")
        with print_lock:
            print(f"\n\U0001F4E5 P{page_num:02d}: {part_title} ({duration}s)", file=sys.stderr)
        download_part(bvid, page_num, part_dir, ytdl_args)
        return page_num, part_title, part_dir
    
    # ── Parallel downloads (Phase 1) ──
    if WORKERS > 1 and not TRANSCRIBE_ONLY:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(download_one, p, t, d): (p, t) for p, t, d in pages}
            completed = []
            for fut in concurrent.futures.as_completed(futures):
                completed.append(fut.result())
        completed.sort()
    else:
        completed = []
        for page_num, part_title, duration in pages:
            part_dir = os.path.join(main_dir, f"P{page_num:02d}_{sanitize(part_title)}")
            if not TRANSCRIBE_ONLY:
                with print_lock:
                    print(f"\n\U0001F4F9 P{page_num:02d}: {part_title} ({duration}s)", file=sys.stderr)
                download_part(bvid, page_num, part_dir, ytdl_args)
            completed.append((page_num, part_title, part_dir))
    
    # ── Sequential transcription (Phase 2) ──
    if not DOWNLOAD_ONLY:
        for page_num, part_title, part_dir in completed:
            with print_lock:
                print(f"\n\U0001F399️ P{page_num:02d}: {part_title}", file=sys.stderr)
            transcribe_part(part_dir, part_title)
    
    # Combined transcript
    combined = []
    for page_num, part_title, duration in pages:
        part_dir = os.path.join(main_dir, f"P{page_num:02d}_{sanitize(part_title)}")
        txt_file = os.path.join(part_dir, "transcript.txt")
        if os.path.exists(txt_file):
            with open(txt_file) as f:
                combined.append(f"\n## P{page_num:02d}: {part_title}\n\n{f.read()}")
    
    if combined:
        with open(os.path.join(main_dir, "transcript_combined.txt"), "w") as f:
            f.write('\n'.join(combined))
    
    # Summary placeholder
    summary_file = os.path.join(main_dir, "summary.md")
    if not os.path.exists(summary_file):
        with open(summary_file, "w") as f:
            f.write(f"# {main_title} — 总结\n\n*待生成 ({len(pages)}P)*\n")
    
    done = sum(1 for p, pt, _ in pages if os.path.exists(os.path.join(main_dir, f"P{p:02d}_{sanitize(pt)}", "transcript.txt")))
    print(f"\n\u2705 {done}/{len(pages)}P 完成 \u2192 {main_dir}", file=sys.stderr)
    print(f"DIR: {main_dir}")

else:
    # Single video
    meta_json = run(YT_DLP + ytdl_args + [URL, "--dump-json", "--no-warnings"], timeout=30)
    if meta_json.returncode != 0:
        print("ERROR", file=sys.stderr); sys.exit(1)
    meta = json.loads(meta_json.stdout)
    up = sanitize(meta.get("uploader", meta.get("channel", "unknown")))
    ti = sanitize(meta.get("title", "untitled"))
    outdir = os.path.join(ROOT, f"{up}_{ti}")
    print(f"\U0001F4F9 {meta.get('title','')} | {up}", file=sys.stderr)
    
    if not TRANSCRIBE_ONLY:
        # Download
        os.makedirs(outdir, exist_ok=True)
        if not SKIP_VIDEO:
            video_path = os.path.join(outdir, "video.mp4")
            run(YT_DLP + ytdl_args + [URL, "-f", "bestvideo*+bestaudio/best", "--merge-output-format", "mp4",
                "--output", video_path, "--no-warnings"], timeout=1200)
        audio_path = os.path.join(outdir, "audio.m4a")
        run(YT_DLP + ytdl_args + [URL, "-x", "--audio-format", "m4a", "--audio-quality", "0",
            "--output", audio_path, "--no-warnings"], timeout=1200)
    
    if not DOWNLOAD_ONLY:
        result = transcribe_part(outdir, meta.get("title", ""))
        if result:
            with open(os.path.join(outdir, "summary.md"), "w") as f:
                f.write(f"# {meta.get('title', '')} — 总结\n\n*待生成*\n")
    
    print(f"DIR: {outdir}")
