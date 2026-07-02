#!/usr/bin/env python3
"""
fav-classify-execute.py — 执行全量分类移动（含溢出分流）+ 并行 BVID 转换

优化: 8线程并行 BVID→avid, 串行每夹 API 太慢 → 8x 提速
"""
import json, os, sys, ssl, time, urllib.request, urllib.parse
import concurrent.futures, threading
from collections import defaultdict

MID = "11976717"
DEFAULT_MLID = "72927717"
COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
CSRF = ""
if os.path.exists(COOKIE_FILE):
    parts = []
    for line in open(COOKIE_FILE):
        if line.startswith("#") or not line.strip(): continue
        fields = line.strip().split("\t")
        if len(fields) >= 7:
            name, value = fields[5], fields[6]
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "sid"):
                parts.append(f"{name}={value}")
            if name == "bili_jct":
                CSRF = value
    if parts:
        HEADERS["Cookie"] = "; ".join(parts)

SSL_CTX = ssl.create_default_context()
CONVERT_WORKERS = 8  # parallel BVID→avid

def bili_get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        data = json.loads(resp.read())
    code = data.get("code")
    if code != 0:
        raise RuntimeError(f"code={code}: {data.get('message','?')}")
    return data.get("data", data)


def bili_post(url, form_data, timeout=15):
    d = urllib.parse.urlencode(form_data).encode()
    req = urllib.request.Request(url, data=d, headers=HEADERS, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())


def bvid_to_aid(bvid):
    """Convert single BVID to avid. Returns None if deleted."""
    try:
        return bili_get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=10,
        ).get("aid")
    except:
        return None


def convert_batch(bvids):
    """Parallel BVID→avid conversion. Returns [(bvid, avid)] for valid ones."""
    valid = []
    skipped = []
    lock = threading.Lock()
    total = len(bvids)
    done = [0]

    def convert(bvid):
        avid = bvid_to_aid(bvid)
        with lock:
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  🔄 转换 {done[0]}/{total}", flush=True)
        if avid:
            valid.append((bvid, avid))
        else:
            skipped.append(bvid)
        return avid

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONVERT_WORKERS) as ex:
        list(ex.map(convert, bvids))

    print(f"  → 有效: {len(valid)}, 跳过失效: {len(skipped)}")
    return valid, skipped


def do_move(src_mlid, dst_mlid, bvid_avid_pairs, allow_av=False):
    """Move videos. bvid_avid_pairs: [(bvid, avid)] format.
    Returns (moved_count, error)."""
    if not bvid_avid_pairs:
        return 0, "无可移动视频"

    resources = ",".join(f"{avid}:2" for _, avid in bvid_avid_pairs)
    r = bili_post(
        "https://api.bilibili.com/x/v3/fav/resource/move",
        {
            "src_media_id": str(src_mlid),
            "tar_media_id": str(dst_mlid),
            "resources": resources,
            "mid": MID,
            "platform": "web",
            "csrf": CSRF,
        },
        timeout=30,
    )
    code = r.get("code")
    msg = r.get("message", "?")
    if code == 0:
        return len(bvid_avid_pairs), None
    return 0, f"code={code} {msg}"


# ── Main ──
print("=" * 60)
print("🚀 全量分类移动（并行 BVID→avid）")
print("=" * 60)

# Folder map
t0 = time.time()
folders_list = bili_get(
    f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={MID}"
)["list"]
FOLDERS = {f["title"]: (f["id"], f["media_count"]) for f in folders_list}
print(f"📂 {len(FOLDERS)} 个文件夹 ({time.time()-t0:.1f}s)")

# Overflow config
OVERFLOW = {
    "搞笑": ("搞笑(2)", FOLDERS["搞笑(2)"][0]),
    "编程/硬件/DIY": ("编程/硬件/DIY(2)", FOLDERS["编程/硬件/DIY(2)"][0]),
}

# Load classification
with open("cache/fav-classify-final.json") as f:
    classify = json.load(f)

# Build plan (skip 默认收藏夹 — already there)
plan = defaultdict(list)
for bvid, info in classify.items():
    target = info.get("suggested_folder", "")
    if target and target != "默认收藏夹":
        plan[target].append(bvid)

total = sum(len(v) for v in plan.values())
print(f"📋 {len(plan)} 目标, {total} 视频")

results = {"success": 0, "fail": 0, "skip": 0}
sorted_targets = sorted(plan.keys(), key=lambda n: -len(plan[n]))

for idx, name in enumerate(sorted_targets):
    bvids = plan[name]
    n = len(bvids)
    print(f"\n{'─'*60}")
    print(f"[{idx+1}/{len(sorted_targets)}] {name} ({n} 个)")

    # Determine destination
    if name == "🗑️待删除":
        dst_name = "放弃区"
        dst_mlid = FOLDERS[dst_name][0]
    elif name in FOLDERS:
        current = FOLDERS[name][1]
        dst_mlid = FOLDERS[name][0]
        capacity = 999 - current

        if capacity <= 0:
            if name in OVERFLOW:
                dst_name, dst_mlid = OVERFLOW[name]
            else:
                print(f"  ⏭️ 夹满且无溢出")
                results["skip"] += n
                continue
        elif n > capacity and name in OVERFLOW:
            # Split
            ov_name, ov_dst = OVERFLOW[name]
            print(f"  ⚡ 分流: {capacity}→{name}, {n-capacity}→{ov_name}")

            # Batch 1
            valid, _ = convert_batch(bvids[:capacity])
            if valid:
                m, err = do_move(DEFAULT_MLID, dst_mlid, valid)
                if err:
                    print(f"  ❌ {name}: {err}")
                    results["fail"] += n
                else:
                    print(f"  ✅ {name}: {m}")
                    results["success"] += m
            else:
                print(f"  ⏭️ {name}: 全部失效")
                results["skip"] += n
            time.sleep(1.5)

            # Batch 2
            valid2, _ = convert_batch(bvids[capacity:])
            if valid2:
                m2, err2 = do_move(DEFAULT_MLID, ov_dst, valid2)
                if err2:
                    print(f"  ❌ {ov_name}: {err2}")
                    results["fail"] += n - capacity
                else:
                    print(f"  ✅ {ov_name}: {m2}")
                    results["success"] += m2
            else:
                results["skip"] += n - capacity
            time.sleep(1.5)
            continue
    else:
        print(f"  ⚠️ B站无此夹")
        results["skip"] += n
        continue

    # Single batch
    valid, _ = convert_batch(bvids)
    if valid:
        m, err = do_move(DEFAULT_MLID, dst_mlid, valid)
        if err:
            print(f"  ❌ {err}")
            results["fail"] += n
        else:
            print(f"  ✅ {m}/{n}")
            results["success"] += m
    else:
        print(f"  ⏭️ 全部失效")
        results["skip"] += n

    time.sleep(1.5)

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"📊 完成! ({elapsed/60:.1f}min)")
print(f"  成功: {results['success']}")
print(f"  失败: {results['fail']}")
print(f"  跳过: {results['skip']}")
