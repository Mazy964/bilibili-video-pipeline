#!/usr/bin/env python3
"""批量清除指定UP主的所有收藏视频"""
import json, os, ssl, time, urllib.request, urllib.parse
import concurrent.futures, threading

MID = "11976717"
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
TARGETS = {"吃素的狮子","男孩不靠普CantoMando","歪果仁研究协会","东京大明白",
           "TESTV官方频道","小潮院长","老番茄","敬汉卿","仲尼Johnny777",
           "vivi可爱多","小缸和阿灿","终极小腾","俊晖JAN","信誓蛋蛋",
           "我是郭杰瑞","某幻君","兔叭咯","记录生活的蛋黄派","啊吗粽",
           "老爸评测","大祥哥来了"}

def bili_get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())

# 1. Get all folders
print("📂 拉取收藏夹列表...")
d = bili_get(f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={MID}")
folders = d["data"]["list"]
folder_map = {f["id"]: f["title"] for f in folders}
print(f"  → {len(folders)} 个文件夹")

# 2. Get all BVIDs from all folders
print("\n📥 拉取所有视频...")
all_bvids = set()
for f in folders:
    mid = f["id"]
    try:
        r = bili_get(f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={mid}&platform=web")
        items = r.get("data", r) if isinstance(r, dict) else r
        for item in (items if isinstance(items, list) else []):
            bvid = item.get("bv_id") or item.get("bvid")
            if bvid:
                all_bvids.add(bvid)
    except:
        pass
print(f"  → {len(all_bvids)} 个唯一视频")

# 3. Parallel check for target uploaders
print(f"\n🔍 扫描 {len(TARGETS)} 个UP主...")
found = []
lock = threading.Lock()
done = [0]
total = len(all_bvids)
bvids_list = list(all_bvids)

def check(bvid):
    try:
        v = bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=8)
        if v.get("code") != 0:
            return
        upper = v.get("data", {}).get("owner", {}).get("name", "")
        if upper in TARGETS:
            avid = v.get("data", {}).get("aid")
            with lock:
                found.append((bvid, avid, upper))
    except:
        pass
    with lock:
        done[0] += 1
        if done[0] % 200 == 0:
            print(f"  🔄 {done[0]}/{total} (命中 {len(found)})")

with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
    list(ex.map(check, bvids_list))

print(f"\n📊 命中 {len(found)} 个视频")
if not found:
    print("✅ 无需清理")
    exit()

# Group by uploader
from collections import defaultdict
by_upper = defaultdict(list)
for bvid, avid, upper in found:
    by_upper[upper].append((bvid, avid))
for u, vids in sorted(by_upper.items()):
    print(f"  {u}: {len(vids)}")
    for bvid, avid in vids:
        print(f"    {bvid}")

# 4. Find current folder for each video + delete
print(f"\n🗑️ 删除 {len(found)} 个...")
deleted = 0
for bvid, avid, upper in found:
    # Find folder
    in_folder = None
    for f in folders:
        mid = f["id"]
        try:
            r = bili_get(f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={mid}&platform=web")
            items = r.get("data", r) if isinstance(r, dict) else r
            bv_list = [item.get("bv_id") or item.get("bvid") for item in (items if isinstance(items, list) else [])]
            if bvid in bv_list:
                in_folder = mid
                break
        except:
            pass
    
    if not in_folder:
        print(f"  ⏭️ {bvid}: 未定位")
        continue
    
    # Delete
    data = urllib.parse.urlencode({
        "rid": str(avid), "type": "2",
        "add_media_ids": "", "del_media_ids": str(in_folder),
        "platform": "web", "csrf": CSRF,
    }).encode()
    req = urllib.request.Request(
        "https://api.bilibili.com/x/v3/fav/resource/deal",
        data=data, headers=HEADERS, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
        r = json.loads(resp.read())
    if r.get("code") == 0:
        deleted += 1
        print(f"  ✅ {bvid} [{upper}]")
    else:
        print(f"  ❌ {bvid}: {r.get('message')}")
    time.sleep(0.3)

print(f"\n✅ 完成: {deleted}/{len(found)}")
