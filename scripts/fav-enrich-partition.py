#!/usr/bin/env python3
"""fav-enrich-partition.py — 多线程批量获取视频分区 tid"""

import json, os, sys, time, hashlib, ssl, urllib.request, urllib.parse
import concurrent.futures, threading

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "..", "cache")
VIDEOS_FILE = os.path.join(CACHE_DIR, "fav-videos.json")
ENRICHED_FILE = os.path.join(CACHE_DIR, "fav-videos-enriched.json")
PROGRESS_FILE = os.path.join(CACHE_DIR, "fav-enrich-progress.json")

SSL_CTX = ssl.create_default_context()

def load_cookie():
    """Thread-safe cookie loading"""
    for cp in [os.path.expanduser("~/.hermes/bilibili_full_cookies.txt"),
               os.path.expanduser("~/.hermes/bilibili_cookies.txt")]:
        if not os.path.exists(cp): continue
        raw = open(cp).read().strip()
        if "\t" not in raw:
            return raw
        parts = []
        for line in raw.split("\n"):
            if line.startswith("#") or not line.strip(): continue
            fld = line.strip().split("\t")
            if len(fld) >= 7 and fld[5] in ("SESSDATA","bili_jct","DedeUserID","buvid3","buvid4"):
                parts.append(f"{fld[5]}={fld[6]}")
        return "; ".join(parts)
    return ""

COOKIE = load_cookie()

def make_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
        "Cookie": COOKIE,
    }

MIXIN_ENC_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

# Init WBI (once at startup)
req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=make_headers())
with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
    nav = json.loads(resp.read())
wbi = nav["data"]["wbi_img"]
ik = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
sk = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
MIXIN = ''.join((ik + sk)[i] for i in MIXIN_ENC_TAB)[:32]
print(f"WBI ready")

# Stats
lock = threading.Lock()
stats = {"done": 0, "ok": 0, "deleted": 0, "errors": 0}
t_start = time.time()

def fetch_one(bvid):
    """Thread-safe: fetch tid for one bvid."""
    headers = make_headers()
    for attempt in range(2):
        try:
            params = {"bvid": bvid, "wts": round(time.time())}
            params = dict(sorted(params.items()))
            clean = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
            query = urllib.parse.urlencode(clean)
            w_rid = hashlib.md5((query + MIXIN).encode()).hexdigest()
            clean["w_rid"] = w_rid
            url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(clean)

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
                data = json.loads(resp.read())

            code = data.get("code", 0)
            if code == 0:
                return data["data"].get("tid", 0)
            elif code in (-404, 62002, 62004):
                return -1
            elif code == -412:
                time.sleep(2)
                continue
            else:
                time.sleep(0.5)
                continue
        except Exception:
            time.sleep(0.5)
            continue
    return None


def worker(bvid):
    tid = fetch_one(bvid)
    with lock:
        stats["done"] += 1
        if tid is None:
            stats["errors"] += 1
        elif tid == -1:
            stats["deleted"] += 1
        else:
            stats["ok"] += 1
        n = stats["done"]
        if n % 200 == 0:
            elapsed = time.time() - t_start
            rate = n / elapsed if elapsed > 0 else 0
            eta = (stats["total"] - n) / rate if rate > 0 else 0
            print(
                f"  {n}/{stats['total']} ({n/stats['total']*100:.0f}%) "
                f"| {rate:.1f}/s | ETA {eta/60:.0f}min "
                f"| ✓={stats['ok']} ✗={stats['errors']} 🗑={stats['deleted']}",
                file=sys.stderr, flush=True,
            )
    return bvid, tid


def main():
    with open(VIDEOS_FILE) as f:
        videos = json.load(f)

    # Load progress
    done = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            done = set(json.load(f))

    # Load existing
    enriched = {}
    if os.path.exists(ENRICHED_FILE):
        with open(ENRICHED_FILE) as f:
            enriched = json.load(f)

    remaining = [v["bvid"] for v in videos if v["bvid"] not in done]
    stats["total"] = len(remaining)

    print(f"总视频: {len(videos)} | 已完成: {len(done)} | 待拉取: {len(remaining)}")
    if not remaining:
        print("✅ 全部完成")
        return

    # Save periodic checkpoints
    def save():
        with open(PROGRESS_FILE, "w") as f:
            json.dump(list(done), f)
        with open(ENRICHED_FILE, "w") as f:
            json.dump(enriched, f, ensure_ascii=False)

    last_save = [len(done)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(worker, bvid): bvid for bvid in remaining}
        for future in concurrent.futures.as_completed(futures):
            bvid, tid = future.result()
            done.add(bvid)
            if tid is not None and tid != -1:
                enriched[bvid] = {"tid": tid}

            # Save every ~1000 new results
            if len(enriched) - last_save[0] >= 1000:
                save()
                last_save[0] = len(enriched)

    # Final save
    save()

    elapsed = time.time() - t_start
    print(f"\n✅ 完成! {elapsed/60:.1f}min")
    print(f"   分区信息: {len(enriched)} | 错误: {stats['errors']} | 已删: {stats['deleted']}")


if __name__ == "__main__":
    main()
