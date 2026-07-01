#!/usr/bin/env python3
"""B站收藏夹失效链接检测 —— 扫描指定收藏夹，检测已删除/私有视频。
Usage:
  fav-deadlink.py <media_id>                         # 扫描并输出 CSV+JSON
  fav-deadlink.py <media_id> --delete                # 扫描并自动移除失效视频 (需要 cookie)
  fav-deadlink.py <media_id> --output-dir /path      # 自定义输出目录
  fav-deadlink.py <media_id> --concurrency 5         # 并发数 (默认 3)
  fav-deadlink.py <media_id> --cookie-file /path     # 指定 cookie 文件

media_id 从收藏夹 URL 获取: https://space.bilibili.com/.../favlist?fid=<media_id>
"""

import sys, json, time, os, re, ssl, urllib.request, urllib.parse, urllib.error, csv, concurrent.futures, threading
from datetime import datetime, timezone, timedelta

os.environ['PYTHONUNBUFFERED'] = '1'

# ── CLI args ──────────────────────────────────────────────────

if len(sys.argv) < 2:
    print("Usage: fav-deadlink.py <media_id> [--delete] [--concurrency N] [--cookie-file <path>] [--output-dir <path>]", file=sys.stderr)
    sys.exit(1)

MEDIA_ID = sys.argv[1]
DELETE = "--delete" in sys.argv
OUTPUT_DIR = None
COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")
CONCURRENCY = 3

for i, a in enumerate(sys.argv):
    if a == "--output-dir" and i + 1 < len(sys.argv):
        OUTPUT_DIR = sys.argv[i + 1]
    if a == "--cookie-file" and i + 1 < len(sys.argv):
        COOKIE_FILE = sys.argv[i + 1]
    if a == "--concurrency" and i + 1 < len(sys.argv):
        CONCURRENCY = int(sys.argv[i + 1])

if OUTPUT_DIR is None:
    OUTPUT_DIR = os.path.expanduser("~/videos/_fav_check")
else:
    OUTPUT_DIR = os.path.expanduser(OUTPUT_DIR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

TZ = timezone(timedelta(hours=8))

# ── Shared state ──────────────────────────────────────────────

SSL_CTX = ssl.create_default_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}

# Load cookie if available
COOKIE_TEXT = None
CSRF = None

def parse_netscape_cookie(filepath: str) -> tuple:
    """Parse Netscape cookie file (used by yt-dlp). Returns (cookie_header_string, bili_jct).
    Also handles raw cookie header format."""
    if not os.path.exists(filepath):
        return None, None
    
    with open(filepath) as f:
        raw = f.read().strip()
    
    # Detect Netscape format (contains tabs + columns)
    if "\t" in raw and not raw.startswith("SESSDATA=") and not raw.startswith("bili_jct="):
        params = {}
        jct = None
        for line in raw.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, _, path, secure, expires, name, value = parts[:7]
                params[name] = value
                if name == "bili_jct":
                    jct = value
        cookie_str = "; ".join(f"{k}={v}" for k, v in params.items())
        return cookie_str, jct
    
    # Raw cookie format
    jct = None
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith("bili_jct="):
            jct = part.split("=", 1)[1]
    return raw, jct

if COOKIE_FILE:
    COOKIE_TEXT, CSRF = parse_netscape_cookie(COOKIE_FILE)
    if COOKIE_TEXT:
        HEADERS["Cookie"] = COOKIE_TEXT
print_lock = threading.Lock()

# ── API helpers ───────────────────────────────────────────────

def api_get(url, attempt=1):
    """GET request with retry. Returns parsed JSON data or raises."""
    req = urllib.request.Request(url, headers=HEADERS)
    for retry in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
            if data.get("code") is not None and data["code"] != 0:
                raise Exception(f"API error code={data['code']}: {data.get('message', 'unknown')}")
            return data["data"]
        except (urllib.error.URLError, urllib.error.HTTPError, ssl.SSLError) as e:
            if retry < 2:
                wait = 2 ** retry
                with print_lock:
                    print(f"  [重试 {retry+1}/{3}] {e}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise Exception("unreachable")


def api_post(url, data_dict):
    """POST request (form-encoded). Returns parsed JSON."""
    data = urllib.parse.urlencode(data_dict).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        return json.loads(resp.read())


# ── Favorites scanning ────────────────────────────────────────

def get_fav_ids(media_id: str) -> list:
    """Get all video IDs from a favorites folder. Returns list of {fav_id, bvid}."""
    url = f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={media_id}"
    data = api_get(url)
    items = data if isinstance(data, list) else data.get("list", data.get("data", []))
    results = []
    for item in items:
        results.append({
            "fav_id": item["id"],
            "bvid": item.get("bvid", item.get("bv_id", "")),
        })
    return results


def get_fav_infos(fav_ids: list) -> dict:
    """Batch-get video metadata from favorites. Returns {fav_id: metadata_dict}.
    Calls resource/infos in batches of 20."""
    metadata = {}
    batch_size = 20
    for i in range(0, len(fav_ids), batch_size):
        batch = fav_ids[i:i + batch_size]
        resources = ",".join(f"{fid}:2" for fid in batch)
        url = f"https://api.bilibili.com/x/v3/fav/resource/infos?resources={urllib.parse.quote(resources)}&platform=web"
        try:
            data = api_get(url)
            for item in (data if isinstance(data, list) else data.get("medias", data.get("list", []))):
                fid = item.get("id", 0)
                metadata[fid] = {
                    "title": item.get("title", ""),
                    "uploader": item.get("upper", {}).get("name", "unknown") if isinstance(item.get("upper"), dict) else "unknown",
                    "fav_time": item.get("fav_time", 0),
                    "cnt_info": item.get("cnt_info", {}),
                }
        except Exception as e:
            with print_lock:
                print(f"  [warn] 获取元数据失败 (batch {i//batch_size+1}): {e}", file=sys.stderr)
        time.sleep(0.3)  # Rate limit
    return metadata


def scan_favorites(media_id: str) -> list:
    """Scan all videos in a favorites folder. Returns list of video dicts."""
    print(f"  [扫描] 获取视频 ID 列表...", file=sys.stderr)
    id_list = get_fav_ids(media_id)
    total = len(id_list)
    print(f"  [扫描] 共 {total} 个视频", file=sys.stderr)

    # Get metadata in batches
    print(f"  [扫描] 获取视频元数据...", file=sys.stderr)
    fav_ids = [item["fav_id"] for item in id_list]
    metadata = get_fav_infos(fav_ids)

    # Build full video list
    all_videos = []
    for item in id_list:
        fid = item["fav_id"]
        meta = metadata.get(fid, {})
        all_videos.append({
            "fav_id": fid,
            "bvid": item["bvid"],
            "title": meta.get("title", ""),
            "uploader": meta.get("uploader", "unknown"),
            "fav_time": meta.get("fav_time", 0),
            "cnt_info": meta.get("cnt_info", {}),
        })

    return all_videos


# ── Video status check ────────────────────────────────────────

def check_video(bvid: str) -> dict:
    """Check a single video's status via view API.
    Returns {
        "bvid": "...",
        "alive": True/False,
        "reason": "deleted" / "private" / "ok",
        "title": "...",   # current title (if alive)
        "msg": "...",     # API message
        "code": ...,      # raw API code
    }
    """
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
            data = json.loads(resp.read())

        api_code = data.get("code", 0)
        msg = data.get("message", "")

        # code=0 = alive
        if api_code == 0:
            return {
                "bvid": bvid,
                "alive": True,
                "reason": "ok",
                "title": data.get("data", {}).get("title", ""),
                "msg": "",
                "code": 0,
            }
        # code=-404 = deleted
        elif api_code == -404:
            return {
                "bvid": bvid,
                "alive": False,
                "reason": "deleted",
                "title": "",
                "msg": msg,
                "code": -404,
            }
        # code=62002 = not accessible (private / under review)
        elif api_code in (62002, 62004):
            return {
                "bvid": bvid,
                "alive": False,
                "reason": "private",
                "title": "",
                "msg": msg,
                "code": api_code,
            }
        else:
            return {
                "bvid": bvid,
                "alive": False,
                "reason": "error",
                "title": "",
                "msg": f"code={api_code}: {msg}",
                "code": api_code,
            }
    except Exception as e:
        return {
            "bvid": bvid,
            "alive": False,
            "reason": "error",
            "title": "",
            "msg": str(e),
            "code": -1,
        }


def batch_check(videos: list, concurrency: int = 3) -> list:
    """Check all videos in parallel. Returns list of result dicts."""
    results = []
    total = len(videos)
    checked = [0]
    lock = threading.Lock()

    def check_one(v):
        bvid = v["bvid"]
        result = check_video(bvid)
        result["fav_id"] = v["fav_id"]
        result["title_original"] = v["title"]
        result["uploader"] = v["uploader"]
        result["fav_time"] = v["fav_time"]  # unix timestamp
        result["play"] = v.get("cnt_info", {}).get("play", 0)
        result["danmaku"] = v.get("cnt_info", {}).get("danmaku", 0)

        with lock:
            checked[0] += 1
            status = "✓" if result["alive"] else "✗"
            detail = result["reason"] if not result["alive"] else ""
            print(f"  [{checked[0]}/{total}] {status} {bvid} {detail}", file=sys.stderr)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(check_one, v): v for v in videos}
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    # Sort by fav_id (chronological within folder)
    results.sort(key=lambda r: r["fav_id"])
    return results


# ── Delete from favorites ─────────────────────────────────────

def delete_from_fav(media_id: str, fav_ids: list, csrf: str):
    """Remove videos from favorites folder. fav_ids are the fav entry ids.
    Batch deletes in groups of 20 (API limit).
    """
    if not csrf:
        print("[warn] 缺少 bili_jct (CSRF token)，无法删除。请使用带 Cookie 的文件。", file=sys.stderr)
        return 0

    deleted = 0
    batch_size = 20
    FORM_HEADERS = dict(HEADERS)
    FORM_HEADERS["Content-Type"] = "application/x-www-form-urlencoded"

    for i in range(0, len(fav_ids), batch_size):
        batch = fav_ids[i:i + batch_size]
        resources = ",".join(str(fid) for fid in batch)
        data = {
            "media_id": media_id,
            "resources": resources,
            "platform": "web",
            "csrf": csrf,
        }
        encoded_data = urllib.parse.urlencode(data).encode()
        url = "https://api.bilibili.com/x/v3/fav/resource/batch-del"
        req = urllib.request.Request(url, data=encoded_data, headers=FORM_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
                result = json.loads(resp.read())
            if result.get("code") == 0:
                deleted += len(batch)
                with print_lock:
                    print(f"  [删除] {len(batch)}/{len(fav_ids)} 个已移除", file=sys.stderr)
            else:
                with print_lock:
                    print(f"  [删除] 失败: code={result.get('code')} {result.get('message', '')}", file=sys.stderr)
        except Exception as e:
            with print_lock:
                print(f"  [删除] 异常: {e}", file=sys.stderr)
        time.sleep(1)  # Rate limit

    return deleted


# ── Output ─────────────────────────────────────────────────────

def save_results(results: list, output_dir: str, media_id: str):
    """Save dead link results to CSV and JSON."""
    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    dead = [r for r in results if not r["alive"]]
    alive = [r for r in results if r["alive"]]

    # Format fav_time as readable date
    def fmt_time(ts):
        if ts:
            return datetime.fromtimestamp(ts, tz=TZ).strftime("%Y-%m-%d %H:%M")
        return ""

    # ── JSON ──
    json_path = os.path.join(output_dir, f"fav_deadlink_{media_id}_{timestamp}.json")
    output = {
        "media_id": media_id,
        "scan_time": datetime.now(TZ).isoformat(),
        "total": len(results),
        "alive": len(alive),
        "dead": len(dead),
        "by_reason": {},
        "dead_videos": [],
    }
    for r in dead:
        reason = r["reason"]
        output["by_reason"][reason] = output["by_reason"].get(reason, 0) + 1
        output["dead_videos"].append({
            "fav_id": r["fav_id"],
            "bvid": r["bvid"],
            "title": r["title_original"],
            "uploader": r["uploader"],
            "reason": r["reason"],
            "fav_time": fmt_time(r["fav_time"]),
            "fav_time_ts": r["fav_time"],
        })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── CSV ──
    csv_path = os.path.join(output_dir, f"fav_deadlink_{media_id}_{timestamp}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fav_id", "bvid", "title", "uploader", "reason", "fav_time"])
        for r in dead:
            writer.writerow([r["fav_id"], r["bvid"], r["title_original"], r["uploader"], r["reason"], fmt_time(r["fav_time"])])

    # ── Full CSV ──
    full_csv_path = os.path.join(output_dir, f"fav_full_{media_id}_{timestamp}.csv")
    with open(full_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fav_id", "bvid", "title", "uploader", "status", "play", "danmaku", "fav_time", "current_title"])
        for r in results:
            status = "alive" if r["alive"] else r["reason"]
            writer.writerow([
                r["fav_id"], r["bvid"], r["title_original"], r["uploader"],
                status, r.get("play", 0), r.get("danmaku", 0),
                fmt_time(r["fav_time"]), r.get("title", "")
            ])

    print(f"\nJSON: {json_path}", file=sys.stderr)
    print(f"CSV:  {csv_path}", file=sys.stderr)
    print(f"Full CSV: {full_csv_path}", file=sys.stderr)

    return json_path, csv_path


# ── Main ──────────────────────────────────────────────────────

def main():
    t_start = time.time()

    # 1) Get folder info (and user mid)
    print(f"📂 收藏夹 ID: {MEDIA_ID}", file=sys.stderr)
    folder_title = f"收藏夹_{MEDIA_ID}"
    video_count = 0
    user_mid = None
    try:
        # Get user mid from nav API, then list folders
        nav_data = api_get("https://api.bilibili.com/x/web-interface/nav")
        user_mid = nav_data.get("mid", 0)
        folders = api_get(f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={user_mid}")
        folder_list = folders if isinstance(folders, list) else folders.get("list", [])
        for f in folder_list:
            if str(f["id"]) == str(MEDIA_ID):
                folder_title = f.get("title", folder_title)
                video_count = f.get("media_count", 0)
                break
        if video_count:
            print(f"   名称: {folder_title}  ·  视频数: {video_count}", file=sys.stderr)
        else:
            print(f"   名称: {folder_title}", file=sys.stderr)
    except Exception as e:
        with print_lock:
            print(f"   [warn] 获取文件夹信息失败: {e}", file=sys.stderr)

    # 2) Scan all videos
    print(f"\n🔍 扫描收藏夹...", file=sys.stderr)
    videos = scan_favorites(MEDIA_ID)
    actual_count = len(videos)
    print(f"   共 {actual_count} 个视频", file=sys.stderr)

    if not videos:
        print("收藏夹为空，退出。", file=sys.stderr)
        sys.exit(0)

    # 3) Check video status
    print(f"\n🔎 检查视频状态 (并发: {CONCURRENCY})...", file=sys.stderr)
    results = batch_check(videos, CONCURRENCY)
    alive = sum(1 for r in results if r["alive"])
    dead = sum(1 for r in results if not r["alive"])
    deleted = sum(1 for r in results if r.get("reason") == "deleted")
    private = sum(1 for r in results if r.get("reason") == "private")
    error = sum(1 for r in results if r.get("reason") == "error")

    elapsed = time.time() - t_start
    print(f"\n📊 结果: {alive} 存活 · {dead} 失效 (已删除: {deleted} · 私有: {private} · 错误: {error})", file=sys.stderr)
    print(f"   耗时: {elapsed:.1f}s", file=sys.stderr)

    # 4) Save results
    json_path, csv_path = save_results(results, OUTPUT_DIR, MEDIA_ID)

    # 5) Optional delete
    if DELETE and dead > 0:
        dead_ids = [r["fav_id"] for r in results if not r["alive"] and r.get("reason") != "error"]
        if dead_ids:
            print(f"\n🗑️  移除 {len(dead_ids)} 个失效视频...", file=sys.stderr)
            csrf_token = CSRF
            if not csrf_token:
                print("⚠️  bili_jct 未找到，尝试重新解析 cookie...", file=sys.stderr)
                try:
                    _, csrf_token = parse_netscape_cookie(COOKIE_FILE)
                except:
                    pass
            if csrf_token:
                removed = delete_from_fav(MEDIA_ID, dead_ids, csrf_token)
                print(f"   已移除: {removed}/{len(dead_ids)}", file=sys.stderr)
            else:
                print("   [warn] 无 CSRF token，跳过删除。请用带 bili_jct 的 cookie 文件重试。", file=sys.stderr)

    # Print summary to stdout for piping
    print(f"STATUS: total={len(results)} alive={alive} dead={dead}")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")

    sys.exit(0 if dead == 0 else 1)


if __name__ == "__main__":
    main()
