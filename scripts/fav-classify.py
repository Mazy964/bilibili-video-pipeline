#!/usr/bin/env python3
"""
fav-classify.py — 默认收藏夹语义分类
=====================================
1. 分页拉取「默认收藏夹」(id=72927717) 全部视频（标题+简介+UP主）
2. 用 DeepSeek 批量分类(每批50个)，映射到已有71个收藏夹
3. 输出分类建议 JSON: {bvid: {title, suggested_folder, confidence}}
4. 不自动移动——结果等人确认

Usage:
  fav-classify.py                        # 全量拉取+分类
  fav-classify.py --fetch-only           # 只拉取视频列表，不分类
  fav-classify.py --classify-only        # 从已有缓存分类
  fav-classify.py --resume               # 断点续传(跳过已分类的)
  fav-classify.py --workers 1            # 拉取并行数(default: 1, 避免风控)
  fav-classify.py --batch-size 50        # 每批分类数量(default: 50)
  fav-classify.py --cookie-file <path>   # 指定 Cookie 文件路径

注意: B站 API 有严格的风控，拉取视频时会自动限速+WBI签名+指数退避。
如果被限流(-412)，脚本会自动等待并重试。建议首次运行用 --workers 1。
"""

import json, os, sys, time, hashlib, ssl, urllib.request, urllib.parse, urllib.error
import concurrent.futures, threading

# ── Config ──────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "..", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

VIDEOS_CACHE = os.path.join(CACHE_DIR, "fav-videos.json")
CLASSIFY_OUT = os.path.join(CACHE_DIR, "fav-classify.json")
PROGRESS_FILE = os.path.join(CACHE_DIR, "fav-classify-progress.json")

COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_full_cookies.txt")
# Fallback to old yt-dlp cookie file if full cookies don't exist
if not os.path.exists(COOKIE_FILE):
    COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")
CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")

FAV_ID = 72927717
PAGE_SIZE = 20  # B站每页固定20条

FETCH_ONLY = "--fetch-only" in sys.argv
CLASSIFY_ONLY = "--classify-only" in sys.argv
RESUME = "--resume" in sys.argv

BATCH_SIZE = 50
WORKERS = 1  # Sequential to avoid rate limits
for i, a in enumerate(sys.argv):
    if a == "--batch-size" and i + 1 < len(sys.argv):
        BATCH_SIZE = int(sys.argv[i + 1])
    if a == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])
    if a == "--cookie-file" and i + 1 < len(sys.argv):
        COOKIE_FILE = sys.argv[i + 1]

# ── WBI Signing ──────────────────────────────────────────────

MIXIN_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

_wbi_keys_cache = [None, None, 0]  # img_key, sub_key, timestamp


def get_mixin_key(orig):
    return ''.join(orig[i] for i in MIXIN_ENC_TAB)[:32]


def get_wbi_keys():
    """Get WBI keys from nav API. Cached for 30 minutes."""
    img_key, sub_key, ts = _wbi_keys_cache
    if img_key and sub_key and time.time() - ts < 1800:
        return img_key, sub_key

    req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
        data = json.loads(resp.read())
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    _wbi_keys_cache[0] = img_key
    _wbi_keys_cache[1] = sub_key
    _wbi_keys_cache[2] = time.time()
    return img_key, sub_key


def enc_wbi(params, img_key, sub_key):
    """Add WBI signature to params dict."""
    mixin_key = get_mixin_key(img_key + sub_key)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


def wbi_sign_url(base_url, params):
    """Return signed URL with WBI parameters."""
    img_key, sub_key = get_wbi_keys()
    signed = enc_wbi(params.copy(), img_key, sub_key)
    return base_url + "?" + urllib.parse.urlencode(signed)


# ── API helpers ─────────────────────────────────────────────

SSL_CTX = ssl.create_default_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.bilibili.com",
    "Referer": "https://www.bilibili.com/",
}


def load_cookie():
    """Load B站 cookies. Supports Netscape format (yt-dlp) and raw format (browser).
    Returns Cookie header string."""  
    if not os.path.exists(COOKIE_FILE):
        print("\u26a0\ufe0f  无 Cookie 文件，部分 API 可能受限", file=sys.stderr)
        return ""
    
    raw = open(COOKIE_FILE).read().strip()
    
    # Detect format: raw browser cookies are semicolon-separated key=value pairs
    if "\t" not in raw and "=" in raw and "SESSDATA" in raw:
        # Raw browser cookie format — use as-is
        return raw
    
    # Netscape format (from yt-dlp) — extract needed cookies
    parts = []
    for line in raw.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.strip().split("\t")
        if len(fields) >= 7:
            name, value = fields[5], fields[6]
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "buvid4", 
                        "bili_ticket", "sid", "b_nut", "_uuid", "b_lsid", "buvid_fp"):
                parts.append(f"{name}={value}")
    return "; ".join(parts)


HEADERS["Cookie"] = load_cookie()


def bili_get(url, timeout=15, retries=5):
    """GET request with retry logic. Handles -412 rate limiting."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                data = json.loads(resp.read())
            code = data.get("code", 0)
            if code == -412:
                # Banned / rate limited
                ttl = data.get("data", {}).get("ttl", 5) if isinstance(data.get("data"), dict) else data.get("ttl", 5)
                wait = max(ttl, 5 * (attempt + 1))
                print(f"  \u23f3 被限流, 等 {wait}s... ({attempt+1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            if code != 0 and code != -412:
                msg = data.get("message", "")
                if attempt < retries - 1:
                    print(f"  \u26a0\ufe0f API code={code} msg={msg}, 重试...", file=sys.stderr)
                    time.sleep(2 * (attempt + 1))
                else:
                    raise Exception(f"API error code={code}: {msg}")
            return data
        except urllib.error.HTTPError as e:
            if e.code == 412:
                # Read body to check if it's a JSON ban or HTML
                try:
                    body = json.loads(e.read())
                    ttl = body.get("data", {}).get("ttl", 5) if isinstance(body.get("data"), dict) else body.get("ttl", 5)
                except:
                    ttl = 10
                wait = max(ttl, 5 * (attempt + 1))
                print(f"  \u23f3 HTTP 412 限流, 等 {wait}s... ({attempt+1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                continue
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  \u26a0\ufe0f {e}, 重试... ({attempt+1}/{retries})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))


def get_user_mid():
    """Get current user's mid from nav API."""
    data = bili_get("https://api.bilibili.com/x/web-interface/nav")
    return data["data"]["mid"]


def get_folders():
    """Get all favorite folders (excluding default). Returns {id: {title, media_count}}."""
    mid = get_user_mid()
    data = bili_get(
        f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={mid}"
    )
    folders = {}
    for f in data["data"]["list"]:
        if f["id"] == FAV_ID:
            continue
        folders[f["id"]] = {
            "title": f["title"],
            "media_count": f.get("media_count", 0),
        }
    return folders


# ── Video fetching ──────────────────────────────────────────


def fetch_page(pn):
    """Fetch one page of favorites. Returns list of video dicts."""
    url = wbi_sign_url(
        "https://api.bilibili.com/x/v3/fav/resource/list",
        {"media_id": FAV_ID, "pn": pn, "ps": PAGE_SIZE}
    )
    data = bili_get(url)
    medias = data.get("data", {}).get("medias", [])
    results = []
    for m in medias:
        intro = m.get("intro", "")
        if intro == "-":
            intro = ""
        results.append(
            {
                "bvid": m["bvid"],
                "title": m["title"],
                "intro": intro,
                "upper": m.get("upper", {}).get("name", ""),
                "duration": m.get("duration", 0),
                "fav_time": m.get("fav_time", 0),
            }
        )
    return results


def fetch_all_videos():
    """Fetch all videos from default favorites, with parallel workers."""
    if os.path.exists(VIDEOS_CACHE):
        with open(VIDEOS_CACHE) as f:
            cached = json.load(f)
        print(f"\U0001f4e6 从缓存加载 {len(cached)} 个视频", file=sys.stderr)
        return cached

    # First request to get total count
    data = bili_get(wbi_sign_url(
        "https://api.bilibili.com/x/v3/fav/resource/list",
        {"media_id": FAV_ID, "pn": 1, "ps": 1}
    ))
    total = data["data"]["info"]["media_count"]
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    print(f"\U0001f4e5 默认收藏夹: {total} 个视频, {total_pages} 页", file=sys.stderr)

    all_videos = []
    page_nums = list(range(1, total_pages + 1))
    lock = threading.Lock()
    done = [0]

    def fetch_safe(pn):
        try:
            videos = fetch_page(pn)
            with lock:
                all_videos.extend(videos)
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == total_pages:
                    print(
                        f"  \U0001f4c4 {done[0]}/{total_pages} 页 ({len(all_videos)} 个视频)",
                        file=sys.stderr,
                    )
            time.sleep(0.5)  # Rate limit avoidance
            return pn, len(videos)
        except Exception as e:
            print(f"  \u274c 第{pn}页失败: {e}", file=sys.stderr)
            # Retry once
            time.sleep(2)
            try:
                videos = fetch_page(pn)
                with lock:
                    all_videos.extend(videos)
                    done[0] += 1
                return pn, len(videos)
            except Exception as e2:
                print(f"  \U0001f480 第{pn}页重试仍失败: {e2}", file=sys.stderr)
                return pn, 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(fetch_safe, page_nums))

    # Sort by fav_time (newest first) — maintains consistent order
    all_videos.sort(key=lambda v: v.get("fav_time", 0), reverse=True)

    with open(VIDEOS_CACHE, "w") as f:
        json.dump(all_videos, f, ensure_ascii=False, indent=2)

    print(f"\u2705 拉取完成: {len(all_videos)} 个视频 \u2192 {VIDEOS_CACHE}", file=sys.stderr)
    return all_videos


# ── DeepSeek API ────────────────────────────────────────────


def load_api_key():
    """Extract hermes-ds API key from Hermes config.yaml."""
    with open(CONFIG_PATH) as f:
        lines = f.readlines()
    in_ds = False
    for i, line in enumerate(lines):
        if line.strip() == "- name: hermes-ds":
            in_ds = True
        elif in_ds and line.strip().startswith("- name:"):
            break
        elif in_ds and "api_key:" in line:
            return line.split("api_key:", 1)[1].strip()
    return None


API_KEY = load_api_key()
DS_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = "deepseek-v4-pro"


def deepseek_chat(messages, temperature=0.3, max_tokens=4000, retries=3):
    """Call DeepSeek chat completions API. Returns content string."""
    payload = json.dumps(
        {
            "model": DS_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                DS_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as resp:
                body = json.loads(resp.read().decode())
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()[:500]
            if e.code == 429:
                wait = min(30 * (attempt + 1), 120)
                print(
                    f"  \u23f3 rate limited, 等 {wait}s... ({attempt+1}/{retries})",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                print(
                    f"  \u274c HTTP {e.code}: {err_body} ({attempt+1}/{retries})",
                    file=sys.stderr,
                )
                if attempt < retries - 1:
                    time.sleep(5)
        except Exception as e:
            print(f"  \u274c {e} ({attempt+1}/{retries})", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(5)

    return None


# ── Classification ──────────────────────────────────────────


def build_classify_prompt(videos_batch, folder_list):
    """Build prompt for one batch of videos."""
    # Format folder list
    folder_lines = []
    for fid, info in folder_list.items():
        folder_lines.append(f"- {info['title']} (id={fid})")
    folders_text = "\n".join(folder_lines)

    # Format video list
    video_lines = []
    for i, v in enumerate(videos_batch):
        parts = [f"{i+1}. [{v['bvid']}] {v['title']}"]
        if v.get("intro"):
            parts.append(f"   备注: {v['intro'][:100]}")
        if v.get("upper"):
            parts.append(f"   UP: {v['upper']}")
        video_lines.append("\n".join(parts))
    videos_text = "\n".join(video_lines)

    system_prompt = f"""你是一个 B站视频分类助手。根据视频的标题、简介和UP主信息，将每个视频分类到最合适的收藏夹。

可用的收藏夹列表:
{folders_text}

分类规则:
1. 选择最匹配的一个收藏夹
2. 如果视频不属于任何现有收藏夹，分类到"默认收藏夹"
3. confidence 用高/中/低 三个级别:
   - 高: 非常确定（标题+简介明确指向该分类）
   - 中: 比较确定（有一定线索但不够明确）
   - 低: 猜测（信息不足，模糊推断）
4. 注意相似分类的区分（如"网络" vs "网络"有两个，选更符合的那个）

输出严格的 JSON 数组格式，每个元素包含:
- bvid: 视频bvid
- title: 原视频标题
- suggested_folder: 收藏夹名称（不是id）
- confidence: 高/中/低
- reason: 一句话理由

只输出 JSON 数组，不要任何其他文字。"""

    user_prompt = f"请对以下 {len(videos_batch)} 个视频进行分类:\n\n{videos_text}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_classify_result(text, videos_batch):
    """Parse DeepSeek response → list of classification dicts."""
    if not text:
        return None

    # Try to extract JSON array
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        text = "\n".join(
            lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        )

    # Find the outermost [ ... ]
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]

    try:
        results = json.loads(text)
        if isinstance(results, list):
            return results
    except json.JSONDecodeError:
        pass

    # Try line-by-line fallback: look for {...} objects
    import re

    objs = re.findall(r"\{[^{}]*\}", text)
    results = []
    for obj_str in objs:
        try:
            results.append(json.loads(obj_str))
        except json.JSONDecodeError:
            continue
    if results:
        return results

    print(f"  \u26a0\ufe0f 无法解析响应，原始文本前200字: {text[:200]}", file=sys.stderr)
    return None


def classify_batch(videos_batch, folder_list, batch_idx, total_batches):
    """Classify one batch of videos. Returns list of result dicts."""
    print(
        f"  \U0001f916 批次 {batch_idx}/{total_batches}: {len(videos_batch)} 个视频...",
        file=sys.stderr,
    )

    messages = build_classify_prompt(videos_batch, folder_list)
    result = deepseek_chat(messages)

    if not result:
        print(f"  \U0001f480 批次 {batch_idx} API 调用失败", file=sys.stderr)
        return None

    parsed = parse_classify_result(result, videos_batch)
    if parsed:
        print(f"  \u2705 批次 {batch_idx}: 分类 {len(parsed)} 个", file=sys.stderr)
    else:
        # Save raw response for debugging
        debug_file = os.path.join(CACHE_DIR, f"batch-{batch_idx}-raw.txt")
        with open(debug_file, "w") as f:
            f.write(result)
        print(
            f"  \u26a0\ufe0f 批次 {batch_idx}: 解析失败，原始结果已保存 \u2192 {debug_file}",
            file=sys.stderr,
        )

    return parsed


def load_progress():
    """Load progress: set of already-classified bvids."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def save_progress(classified):
    """Save progress: set of classified bvids."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(classified, f, ensure_ascii=False)


def merge_classify_results(existing, new_results):
    """Merge new classification results into existing dict (keyed by bvid)."""
    for item in new_results:
        bvid = item.get("bvid", "")
        if bvid:
            existing[bvid] = {
                "title": item.get("title", ""),
                "suggested_folder": item.get("suggested_folder", ""),
                "confidence": item.get("confidence", "低"),
                "reason": item.get("reason", ""),
            }
    return existing


def classify_all(videos, folder_list):
    """Classify all videos in batches. Supports resume."""
    progress = load_progress()
    already_done = set(progress.keys()) if isinstance(progress, dict) else set(progress)

    # Load existing results
    results = {}
    if os.path.exists(CLASSIFY_OUT):
        with open(CLASSIFY_OUT) as f:
            results = json.load(f)

    # Filter out already classified
    remaining = [v for v in videos if v["bvid"] not in already_done]
    if not remaining:
        print("\u2705 全部已分类", file=sys.stderr)
        return results

    print(
        f"\U0001f50d 待分类: {len(remaining)} 个视频, 批次大小={BATCH_SIZE}",
        file=sys.stderr,
    )

    total_batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE + len(
        [v for v in videos if v["bvid"] in already_done]
    )

    batch_idx = len(already_done) // BATCH_SIZE + 1
    for i in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[i : i + BATCH_SIZE]
        parsed = classify_batch(
            batch, folder_list, batch_idx, total_batches
        )
        if parsed:
            results = merge_classify_results(results, parsed)
            # Mark as done
            for item in parsed:
                already_done.add(item.get("bvid", ""))

            # Save progress
            save_progress(list(already_done))
            with open(CLASSIFY_OUT, "w") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        batch_idx += 1
        # Brief pause between batches to avoid rate limits
        time.sleep(2)

    return results


# ── Main ────────────────────────────────────────────────────


def main():
    if not API_KEY:
        print("\u274c 无法从 config.yaml 读取 DeepSeek API key", file=sys.stderr)
        sys.exit(1)

    # 1. Fetch folder list
    print("\U0001f4c2 获取收藏夹列表...", file=sys.stderr)
    folders = get_folders()
    print(f"\U0001f4c2 {len(folders)} 个收藏夹（排除默认）", file=sys.stderr)

    # 2. Fetch videos
    videos = fetch_all_videos()
    if not videos:
        print("\u274c 无视频数据", file=sys.stderr)
        sys.exit(1)

    if FETCH_ONLY:
        print(f"\u2705 只拉取模式: {len(videos)} 个视频已缓存 \u2192 {VIDEOS_CACHE}")
        return

    # 3. Classify
    results = classify_all(videos, folders)

    # 4. Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"\U0001f4ca 分类完成: {len(results)}/{len(videos)} 个视频", file=sys.stderr)

    # Count by confidence
    conf_counts = {"高": 0, "中": 0, "低": 0}
    folder_counts = {}
    for bvid, info in results.items():
        conf = info.get("confidence", "低")
        conf_counts[conf] = conf_counts.get(conf, 0) + 1
        folder = info.get("suggested_folder", "未知")
        folder_counts[folder] = folder_counts.get(folder, 0) + 1

    print(f"  置信度分布: 高={conf_counts.get('高',0)} 中={conf_counts.get('中',0)} 低={conf_counts.get('低',0)}", file=sys.stderr)

    unclassified = len(videos) - len(results)
    if unclassified > 0:
        print(f"  \u26a0\ufe0f 未分类: {unclassified} 个（解析失败或未处理）", file=sys.stderr)

    print(f"\n\U0001f4c1 输出 \u2192 {CLASSIFY_OUT}", file=sys.stderr)
    print(CLASSIFY_OUT)


if __name__ == "__main__":
    main()
