#!/usr/bin/env python3
"""
fav-monitor.py — 收藏夹监控：扫描指定收藏夹，检测新增视频，自动创建 Kanban 流水线任务

Pipeline: transcriber → obsidian-writer → reviewer
  1. transcriber: 下载视频 + 转录音频 + 说话人分离
  2. obsidian-writer: 格式化转录稿写入 Obsidian
  3. reviewer: 审核最终输出质量

技术说明:
  - 使用 resource/ids API 获取 BVID 列表（resource/list 端点已返回 412）
  - 单次最多返回 1000 个条目（B站限制），对于监控用途足够
  - 新视频通过 view API 获取标题等详情（仅对新视频调用，节省请求）

Usage:
  fav-monitor.py                            # 扫描「下载」文件夹(mlid=4106246717)，检测新增并创建任务
  fav-monitor.py --media-id 4106246717      # 指定收藏夹 mlid
  fav-monitor.py --dry-run                  # 仅扫描+对比，不创建 Kanban 任务
  fav-monitor.py --force                    # 强制为所有状态文件中的视频(重新)创建任务
  fav-monitor.py --workers 8                # 并行拉取数 (default: 4)

State file: ~/videos/.fav-state.json
{
  "media_id": 4106246717,
  "folder_title": "下载",
  "last_scan": "2026-07-01T18:30:00+08:00",
  "total_scanned": 42,
  "videos": {
    "BVxxx": {
      "title": "...",
      "uploader": "...",
      "duration": 123,
      "status": "tasked",          // new | tasked | done
      "kanban_tasks": {            // 仅 status=tasked 时有
        "transcriber": "t_xxx",
        "obsidian_writer": "t_xxx",
        "reviewer": "t_xxx"
      }
    }
  }
}
"""

import json, os, sys, time, ssl, urllib.request, urllib.parse, urllib.error
import concurrent.futures, subprocess, threading
from datetime import datetime, timezone, timedelta

os.environ["PYTHONUNBUFFERED"] = "1"

# ── Config ────────────────────────────────────────────────────

MEDIA_ID = 4106246717  # 「下载」mlid (fid=41062467, mid=11976717)
STATE_FILE = os.path.expanduser("~/videos/.fav-state.json")
COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")
WORKERS = 4
BILI_IDS_CAP = 1000  # resource/ids 单次最多返回数

DRY_RUN = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv

for i, a in enumerate(sys.argv):
    if a == "--media-id" and i + 1 < len(sys.argv):
        MEDIA_ID = int(sys.argv[i + 1])
    if a == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])

TZ = timezone(timedelta(hours=8))
SSL_CTX = ssl.create_default_context()

# ── Cookie ────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}

COOKIE_STR = ""
if os.path.exists(COOKIE_FILE):
    parts = []
    for line in open(COOKIE_FILE):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.strip().split("\t")
        if len(fields) >= 7:
            name, value = fields[5], fields[6]
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "sid"):
                parts.append(f"{name}={value}")
    COOKIE_STR = "; ".join(parts)
    if COOKIE_STR:
        HEADERS["Cookie"] = COOKIE_STR


# ── B站 API ───────────────────────────────────────────────────

def bili_get(url, timeout=15, retries=3):
    """GET request with retry + backoff. Returns data dict or raises."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
            code = data.get("code")
            if code is not None and code != 0:
                raise RuntimeError(f"API code={code}: {data.get('message', '?')}")
            return data.get("data", data)
        except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError, OSError) as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  [重试 {attempt+1}/{retries}] {e}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except RuntimeError:
            raise
    raise RuntimeError("unreachable")


def get_folder_info(media_id):
    """Get folder metadata. Returns {title, media_count, ...} or None."""
    try:
        url = f"https://api.bilibili.com/x/v3/fav/folder/info?media_id={media_id}"
        return bili_get(url)
    except Exception as e:
        print(f"⚠️  获取收藏夹信息失败: {e}", file=sys.stderr)
        return None


def get_folder_bvids(media_id):
    """Get all BVIDs from a favorites folder via resource/ids API.
    Returns list of bvid strings. Caps at B站's limit (~1000).
    """
    url = f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={media_id}&platform=web"
    data = bili_get(url)
    items = data if isinstance(data, list) else data.get("data", data) if isinstance(data, dict) else []
    if not isinstance(items, list):
        print(f"⚠️  resource/ids 返回格式异常: {type(items)}", file=sys.stderr)
        return []
    bvids = [item.get("bv_id") or item.get("bvid") for item in items]
    return bvids


def get_video_info(bvid):
    """Get video details via view API (no retry — 404/private are permanent).
    Returns {title, uploader, duration, cover} or fallback with bvid as title.
    """
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            data = json.loads(resp.read())
        code = data.get("code")
        if code == 0:
            d = data.get("data", {})
            return {
                "bvid": bvid,
                "title": d.get("title", bvid),
                "uploader": d.get("owner", {}).get("name", "unknown"),
                "duration": d.get("duration", 0),
                "cover": d.get("pic", ""),
            }
        # Deleted (-404), private/inaccessible (62002, 62012), etc.
        # These are permanent states, not transient errors.
        return {
            "bvid": bvid,
            "title": bvid,
            "uploader": "unknown",
            "duration": 0,
            "cover": "",
        }
    except Exception as e:
        return {
            "bvid": bvid,
            "title": bvid,
            "uploader": "unknown",
            "duration": 0,
            "cover": "",
        }


# ── Scanning ──────────────────────────────────────────────────

def scan_folder(media_id):
    """Scan a favorites folder and return video info dicts.
    1. Uses resource/ids to get all BVIDs (up to BILI_IDS_CAP)
    2. For new BVIDs not in state file, fetches details via view API
    3. For known BVIDs, uses cached data from state file
    
    Returns (all_videos, new_videos) — both lists of video info dicts.
    """
    # Step 1: Get all BVIDs
    bvids = get_folder_bvids(media_id)
    print(f"📥 resource/ids 返回 {len(bvids)} 个 BVID", file=sys.stderr)

    if not bvids:
        return [], []

    if len(bvids) >= BILI_IDS_CAP:
        print(f"⚠️  已达 {BILI_IDS_CAP} 条目上限，可能有更多视频未被扫描", file=sys.stderr)

    # Step 2: Load state to separate known vs new
    state = load_state()
    known = state.get("videos", {})
    new_bvids = [b for b in bvids if b not in known]

    # Step 3: Build known videos from cache
    known_videos = []
    for bvid in bvids:
        if bvid in known:
            entry = known[bvid]
            known_videos.append({
                "bvid": bvid,
                "title": entry.get("title", bvid),
                "uploader": entry.get("uploader", "unknown"),
                "duration": entry.get("duration", 0),
                "cover": entry.get("cover", ""),
            })

    # Step 4: Fetch details for new BVIDs in parallel
    new_videos = []
    if new_bvids:
        print(f"🔍 获取 {len(new_bvids)} 个新视频详情...", file=sys.stderr)
        lock = threading.Lock()
        done = [0]
        total = len(new_bvids)

        def fetch_info(bvid):
            info = get_video_info(bvid)
            with lock:
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == total:
                    print(f"  📄 {done[0]}/{total}", file=sys.stderr)
            return info

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            new_videos = list(ex.map(fetch_info, new_bvids))

        # Register in state
        for v in new_videos:
            known[v["bvid"]] = {
                "title": v["title"],
                "uploader": v["uploader"],
                "duration": v["duration"],
                "cover": v.get("cover", ""),
                "status": "new",
            }

    # Combine all videos, maintain order from resource/ids (newest first)
    all_videos = []
    bvid_to_video = {}
    for v in known_videos + new_videos:
        bvid_to_video[v["bvid"]] = v
    for bvid in bvids:
        if bvid in bvid_to_video:
            all_videos.append(bvid_to_video[bvid])

    return all_videos, new_videos


# ── State file management ─────────────────────────────────────

def load_state():
    """Load .fav-state.json. Returns dict or empty skeleton."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  状态文件损坏，重新初始化: {e}", file=sys.stderr)
    return {
        "media_id": MEDIA_ID,
        "folder_title": "",
        "last_scan": None,
        "total_scanned": 0,
        "videos": {},
    }


def save_state(state):
    """Save state file atomically."""
    tmp = STATE_FILE + ".tmp"
    state["last_scan"] = datetime.now(TZ).isoformat()
    state["total_scanned"] = len(state.get("videos", {}))
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ── Kanban task creation ──────────────────────────────────────

def fmt_duration(sec):
    """Format seconds to MM:SS or HH:MM:SS."""
    if not sec:
        return "?"
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s}s"
    h, m = divmod(m, 60)
    return f"{h}h{m}m{s}s"


def kanban_create(title, assignee, body, parents=None, skills=None, idempotency_key=None):
    """Create a Kanban task via CLI. Returns task_id or None on failure."""
    cmd = ["hermes", "kanban", "create", title,
           "--assignee", assignee,
           "--body", body,
           "--json"]

    if parents:
        for p in parents:
            cmd.extend(["--parent", p])
    if skills:
        for s in skills:
            cmd.extend(["--skill", s])
    if idempotency_key:
        cmd.extend(["--idempotency-key", idempotency_key])

    result = None
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"  ❌ kanban create 失败: {result.stderr.strip()}", file=sys.stderr)
            return None
        data = json.loads(result.stdout)
        return data.get("task_id") or data.get("id")
    except subprocess.TimeoutExpired:
        print(f"  ❌ kanban create 超时", file=sys.stderr)
        return None
    except json.JSONDecodeError:
        stdout_preview = result.stdout[:200] if result else "(no output)"
        print(f"  ❌ kanban create 输出非 JSON: {stdout_preview}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ❌ kanban create 异常: {e}", file=sys.stderr)
        return None


def build_transcriber_body(v):
    """Build body for transcriber kanban task."""
    duration_str = fmt_duration(v["duration"])
    return (
        f"下载并转录 B站视频:\n\n"
        f"- BV号: {v['bvid']}\n"
        f"- 标题: {v['title']}\n"
        f"- UP主: {v['uploader']}\n"
        f"- 时长: {duration_str}\n"
        f"- 封面: {v.get('cover', '')}\n\n"
        f"使用 video-process.py {v['bvid']} --diarize 完成下载+转录+说话人分离。"
    )


def build_obsidian_body(v):
    """Build body for obsidian-writer kanban task."""
    return (
        f"将转录稿格式化为 Obsidian 笔记:\n\n"
        f"- BV号: {v['bvid']}\n"
        f"- 标题: {v['title']}\n"
        f"- UP主: {v['uploader']}\n\n"
        f"从上游 transcriber 任务获取转录 JSON，格式化后写入 Obsidian vault。"
    )


def build_reviewer_body(v):
    """Build body for reviewer kanban task."""
    return (
        f"审核最终输出质量:\n\n"
        f"- BV号: {v['bvid']}\n"
        f"- 标题: {v['title']}\n"
        f"- UP主: {v['uploader']}\n\n"
        f"检查上游 obsidian-writer 输出的 Obsidian 笔记：\n"
        f"1. 转录质量（错别字、断句）\n"
        f"2. 说话人标注准确性\n"
        f"3. 格式化美观度\n"
        f"4. 元数据完整性\n\n"
        f"发现问题则退回上游修正，无误则标记完成。"
    )


def create_pipeline(v):
    """Create the 3-task pipeline for one video.
    Returns dict of task_ids or None on failure.
    """
    bvid = v["bvid"]
    title_short = v["title"][:60] if v["title"] else bvid
    print(f"  🔗 {bvid}: 创建 pipeline...", file=sys.stderr)

    # Use bvid as dedup key prefix
    dedup_key = f"fav-monitor:{bvid}"

    # 1. transcriber (no parent)
    t_id = kanban_create(
        title=f"下载+转录: {title_short}",
        assignee="transcriber",
        body=build_transcriber_body(v),
        skills=["whisper-diarize-apple-silicon", "video-summary"],
        idempotency_key=f"{dedup_key}:transcriber",
    )
    if not t_id:
        return None
    print(f"    ✅ transcriber: {t_id}", file=sys.stderr)

    # 2. obsidian-writer (parent = transcriber)
    o_id = kanban_create(
        title=f"写笔记: {title_short}",
        assignee="obsidian-writer",
        body=build_obsidian_body(v),
        parents=[t_id],
        idempotency_key=f"{dedup_key}:obsidian-writer",
    )
    if not o_id:
        print(f"    ⚠️  obsidian-writer 创建失败，transcriber 任务 {t_id} 已存在", file=sys.stderr)
        return {"transcriber": t_id}
    print(f"    ✅ obsidian-writer: {o_id}", file=sys.stderr)

    # 3. reviewer (parent = obsidian-writer)
    r_id = kanban_create(
        title=f"审核: {title_short}",
        assignee="reviewer",
        body=build_reviewer_body(v),
        parents=[o_id],
        idempotency_key=f"{dedup_key}:reviewer",
    )
    if not r_id:
        print(f"    ⚠️  reviewer 创建失败，上游 {t_id} → {o_id} 已存在", file=sys.stderr)
        return {"transcriber": t_id, "obsidian_writer": o_id}

    print(f"    ✅ reviewer: {r_id}", file=sys.stderr)
    return {
        "transcriber": t_id,
        "obsidian_writer": o_id,
        "reviewer": r_id,
    }


# ── Main ──────────────────────────────────────────────────────

def main():
    t_start = time.time()

    # 1. Get folder info
    print(f"📂 收藏夹 mlid={MEDIA_ID}", file=sys.stderr)
    folder_info = get_folder_info(MEDIA_ID)
    folder_title = folder_info.get("title", f"收藏夹_{MEDIA_ID}") if folder_info else f"收藏夹_{MEDIA_ID}"
    video_count = folder_info.get("media_count", 0) if folder_info else 0
    print(f"   名称: {folder_title}  ·  视频数: {video_count}", file=sys.stderr)

    if video_count == 0:
        print("📭 收藏夹为空，退出。", file=sys.stderr)
        return

    # 2. Scan folder + detect new
    print(f"\n🔍 扫描收藏夹...", file=sys.stderr)
    all_videos, new_videos = scan_folder(MEDIA_ID)
    print(f"   共 {len(all_videos)} 个视频 (其中 {len(new_videos)} 个新增)", file=sys.stderr)

    # 3. Load state (already done in scan_folder, reload for consistency)
    state = load_state()
    state["media_id"] = MEDIA_ID
    state["folder_title"] = folder_title

    # Merge new video entries from scan_folder into state
    for v in new_videos:
        bvid = v["bvid"]
        if bvid not in state.get("videos", {}):
            state.setdefault("videos", {})[bvid] = {
                "title": v["title"],
                "uploader": v["uploader"],
                "duration": v["duration"],
                "cover": v.get("cover", ""),
                "status": "new",
            }

    if not new_videos and not FORCE:
        save_state(state)
        elapsed = time.time() - t_start
        print(f"\n✅ 无新视频，状态已更新 ({elapsed:.1f}s)", file=sys.stderr)
        return

    # 4. Determine which videos to create tasks for
    if FORCE:
        targets = all_videos
        print(f"\n🔁 --force: 为全部 {len(targets)} 个视频创建/更新任务", file=sys.stderr)
    else:
        targets = new_videos
        print(f"\n🆕 新增视频: {len(targets)} 个", file=sys.stderr)
        for v in targets:
            print(f"   + {v['bvid']}  {v['title'][:50]}  ({v['uploader']})", file=sys.stderr)

    if DRY_RUN:
        print(f"\n🏷️  --dry-run: 跳过 Kanban 任务创建", file=sys.stderr)
        if not FORCE:
            save_state(state)
            elapsed = time.time() - t_start
            print(f"   已将 {len(new_videos)} 个新视频写入状态文件 (dry-run)", file=sys.stderr)
            print(f"   状态文件: {STATE_FILE}", file=sys.stderr)
            print(f"   耗时: {elapsed:.1f}s", file=sys.stderr)
        return

    # 5. Create Kanban pipeline for each target
    print(f"\n🔗 创建 Kanban 流水线任务...", file=sys.stderr)
    created = 0
    failed = 0
    partial = 0
    skipped = 0

    for v in targets:
        bvid = v["bvid"]
        current_status = state["videos"].get(bvid, {}).get("status", "new")

        # Skip already tasked unless --force
        if current_status == "tasked" and not FORCE:
            skipped += 1
            continue

        result = create_pipeline(v)
        if not result:
            failed += 1
            continue

        # Check completeness
        has_all = all(k in result for k in ("transcriber", "obsidian_writer", "reviewer"))
        if has_all:
            created += 1
            state["videos"][bvid]["status"] = "tasked"
        else:
            partial += 1
            state["videos"][bvid]["status"] = "partial"
        state["videos"][bvid]["kanban_tasks"] = result

        # Brief pause between pipeline creations
        time.sleep(0.5)

    # 6. Save state
    save_state(state)

    elapsed = time.time() - t_start
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"📊 汇总:", file=sys.stderr)
    print(f"   扫描: {len(all_videos)} 个视频", file=sys.stderr)
    print(f"   新增: {len(new_videos)} 个", file=sys.stderr)
    print(f"   已创建完整 pipeline: {created}", file=sys.stderr)
    print(f"   部分创建: {partial}", file=sys.stderr)
    print(f"   失败: {failed}", file=sys.stderr)
    print(f"   跳过: {skipped}", file=sys.stderr)
    print(f"   状态文件: {STATE_FILE}", file=sys.stderr)
    print(f"   耗时: {elapsed:.1f}s", file=sys.stderr)

    # Summary to stdout for piping
    print(json.dumps({
        "scanned": len(all_videos),
        "new": len(new_videos),
        "created": created,
        "partial": partial,
        "failed": failed,
        "skipped": skipped,
        "state_file": STATE_FILE,
    }, ensure_ascii=False))

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
