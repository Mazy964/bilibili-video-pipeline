#!/usr/bin/env python3
"""
transcript-to-obsidian.py - Existing transcripts -> LLM refine -> Obsidian

Usage:
  transcript-to-obsidian.py ~/videos/uploader_title/P07_xxx/     # single part
  transcript-to-obsidian.py ~/videos/uploader_title/             # whole series
  transcript-to-obsidian.py --dry-run ...                        # preview only
"""

import sys, os, json, re, yaml, time, argparse, urllib.request, ssl
from pathlib import Path

OBSIDIAN_VAULT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Computer System"
)
VAULT_NOTES = os.path.join(OBSIDIAN_VAULT, "视频笔记")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

def sanitize(s):
    return re.sub(r'[<>:"/\\|?*]', '_', str(s).strip())[:80]

def get_api_key():
    env = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env):
        with open(env) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2 and parts[0] == "DEEPSEEK_API_KEY":
                    return parts[1].strip().strip('"').strip("'")
    return ""

def call_llm(system, prompt, max_tokens=4096):
    key = get_api_key()
    if not key:
        print("[WARN] No API key", file=sys.stderr)
        return None
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
        "temperature": 0.3, "max_tokens": max_tokens
    }).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=data, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"
    })
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[ERROR] LLM: {e}", file=sys.stderr)
        return None

def write_note(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

def refine_transcript(title, transcript, duration="?", chapters=None):
    system = """你是知识整理专家。将视频转录转为结构化 Obsidian 笔记。
要求：
1. 中文输出，英文术语保留原文
2. 用 [[wikilink]] 链接关键概念
3. 用 > [!quote] 引用原文，保留时间戳
4. 结构：核心概念、内容精要、实战要点、关键引用
5. 提炼 3-5 个核心概念并附定义
6. 输出完整 Markdown，带 YAML frontmatter"""

    chapter_section = ""
    if chapters:
        chapter_section = "\n## 📑 章节\n"
        for ch in chapters:
            chapter_section += f"- [{ch['from']}s-{ch['to']}s] {ch['content']}\n"
        chapter_section += "\n请在「内容精要」中按上述章节分段组织内容。\n"

    prompt = f"""将以下视频转录整理为 Obsidian 笔记：

标题: {title}
时长: {duration}s
{chapter_section}
转录:
{transcript[:8000]}

输出:
---
title: "{title}"
type: video-note
duration: {duration}
tags: [video-note]
created: {time.strftime('%Y-%m-%d')}
---

# {title}

## 🔑 核心概念
- [[概念1]] — 定义
...

## 📝 内容精要
（按主题分段，每段 > [!quote] + 时间戳）

## 🛠️ 实战要点
（可操作步骤）

## 💬 关键引用
（3-5 条原文）
"""
    return call_llm(system, prompt)

def extract_terms(structured_note):
    system = "从笔记提取关键术语。只输出 JSON 数组: [{\"name\":\"术语\",\"definition\":\"一句话定义\"}]，最多5个。不要其他文字。"
    r = call_llm(system, structured_note[:3000], max_tokens=1000)
    if not r:
        return []
    try:
        m = re.search(r'\[.*\]', r, re.DOTALL)
        return json.loads(m.group()) if m else []
    except:
        return []

def get_chapters_from_api(bvid):
    """Fetch video chapters (view_points) from B站 player/wbi/v2 API"""
    if not bvid:
        return None
    try:
        # Get aid + cid
        r = urllib.request.urlopen(
            urllib.request.Request(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"),
            timeout=10
        )
        info = json.loads(r.read())
        if info.get("code") != 0:
            return None
        d = info["data"]
        aid, cid = d["aid"], d["cid"]
        
        # Get view_points from player/wbi/v2
        r2 = urllib.request.urlopen(
            urllib.request.Request(f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}"),
            timeout=10
        )
        player = json.loads(r2.read())
        pts = player.get("data", {}).get("view_points", [])
        return pts if pts else None
    except Exception as e:
        print(f"  ⚠️ 章节获取失败: {e}", file=sys.stderr)
        return None

def process_part(part_dir, vault_dir, dry_run=False):
    part_dir = os.path.expanduser(part_dir)
    tx_path = os.path.join(part_dir, "transcript.txt")
    info_path = os.path.join(part_dir, "info.yaml")
    
    if not os.path.exists(tx_path):
        print(f"  SKIP: no transcript.txt", file=sys.stderr)
        return False
    
    with open(tx_path) as f:
        transcript = f.read()
    
    meta = {}
    if os.path.exists(info_path):
        with open(info_path) as f:
            meta = yaml.safe_load(f) or {}
    
    part_title = meta.get("part_title", os.path.basename(part_dir))
    part_title = re.sub(r'^P\d{2}_', '', part_title)
    duration = meta.get("duration", "?")
    bvid = meta.get("bvid", None)
    
    # Fetch chapters from B站 API
    chapters = get_chapters_from_api(bvid) if bvid else None
    if chapters:
        print(f"  📑 {len(chapters)} 章节", file=sys.stderr)
    
    name = sanitize(part_title)
    if dry_run:
        print(f"  [DRY RUN] {name}", file=sys.stderr)
        return True
    
    print(f"  📝 转录: {len(transcript)} 字符", file=sys.stderr)
    
    # Write full transcript
    ts_md = f"""---
title: "{part_title}"
type: transcript
created: {time.strftime('%Y-%m-%d')}
---

# {part_title} — 完整转录

{transcript}
"""
    write_note(os.path.join(vault_dir, name + "_transcript.md"), ts_md)
    
    # LLM refine
    print(f"  🧠 LLM 精炼...", file=sys.stderr)
    structured = refine_transcript(part_title, transcript, duration, chapters)
    if not structured:
        print(f"  ❌ LLM 失败", file=sys.stderr)
        return False
    
    write_note(os.path.join(vault_dir, name + ".md"), structured)
    print(f"  ✅ 结构化笔记", file=sys.stderr)
    
    # Terms
    print(f"  📚 提取术语...", file=sys.stderr)
    terms = extract_terms(structured)
    for t in terms:
        tp = os.path.join(vault_dir, "terms", sanitize(t["name"]) + ".md")
        write_note(tp, f"""---
aliases: [{t["name"]}]
tags: [concept, video-note]
---

# {t["name"]}

{t.get("definition", "")}
""")
    if terms:
        print(f"  📚 {len(terms)} 术语", file=sys.stderr)
    
    return True

def process_directory(input_dir, dry_run=False):
    input_dir = os.path.expanduser(input_dir)
    
    # Single part?
    if os.path.exists(os.path.join(input_dir, "transcript.txt")):
        parent = sanitize(os.path.basename(os.path.dirname(input_dir)))
        vd = os.path.join(VAULT_NOTES, parent)
        return process_part(input_dir, vd, dry_run)
    
    # Series
    info = os.path.join(input_dir, "info.yaml")
    if not os.path.exists(info):
        print("ERROR: no info.yaml", file=sys.stderr)
        return False
    
    with open(info) as f:
        master = yaml.safe_load(f) or {}
    
    title = master.get("title", os.path.basename(input_dir))
    uploader = master.get("uploader", "unknown")
    parts = master.get("parts", [])
    
    vd = os.path.join(VAULT_NOTES, sanitize(f"{uploader}_{title}"))
    print(f"📂 {title} ({len(parts)}P) → {vd}", file=sys.stderr)
    
    # MOC
    links = []
    for p in parts:
        fn = sanitize("P{:02d}_{}".format(p["page"], p["title"]))
        links.append("- [[" + fn + "]] — " + p["title"])
    moc = f"""---
title: "{title}"
uploader: {uploader}
type: moc
tags: [video-note, moc]
created: {time.strftime('%Y-%m-%d')}
total_parts: {len(parts)}
---

# {title}

> Source: {uploader} | {len(parts)} episodes

{chr(10).join(links)}
"""
    if not dry_run:
        write_note(os.path.join(vd, "MOC.md"), moc)
    print(f"  📋 MOC", file=sys.stderr)
    
    success = 0
    for p in parts:
        pnum = p["page"]
        ptitle = p["title"]
        dirs = list(Path(input_dir).glob(f"P{pnum:02d}_*"))
        if not dirs:
            continue
        print(f"\n📹 P{pnum:02d}: {ptitle}", file=sys.stderr)
        if process_part(str(dirs[0]), vd, dry_run):
            success += 1
    
    print(f"\n✅ {success}/{len(parts)}P → {vd}", file=sys.stderr)
    print(f"VAULT: {vd}")
    return success > 0

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("input_dir")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--vault", help="Override vault path")
    args = p.parse_args()
    if args.vault:
        OBSIDIAN_VAULT = os.path.expanduser(args.vault)
        VAULT_NOTES = os.path.join(OBSIDIAN_VAULT, "视频笔记")
    process_directory(args.input_dir, dry_run=args.dry_run)
