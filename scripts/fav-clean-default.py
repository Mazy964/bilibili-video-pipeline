#!/usr/bin/env python3
"""扫描默认夹失效/短视频，批量清除"""
import json, os, ssl, time, urllib.request, urllib.parse
import concurrent.futures, threading

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

def bili_get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())

# 1. Get all BVIDs
print("📥 拉取默认夹...")
d = bili_get(f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={DEFAULT_MLID}&platform=web")
items = d.get("data", d) if isinstance(d, dict) else d
bvids = [item.get("bv_id") or item.get("bvid") for item in (items if isinstance(items, list) else [])]
print(f"  → {len(bvids)} 个视频")

# 2. Parallel check: is it dead or super short?
print(f"\n🔍 并行扫描 {len(bvids)} 个...")
to_delete = []
lock = threading.Lock()
done = [0]
total = len(bvids)

def check(bvid):
    try:
        data = bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=8)
        code = data.get("code")
        if code != 0:
            with lock:
                to_delete.append((bvid, f"失效(code={code})"))
                done[0] += 1
            return
        dd = data.get("data", {})
        duration = dd.get("duration", 0)
        title = dd.get("title", "")[:40]
        if duration > 0 and duration < 30:
            with lock:
                to_delete.append((bvid, f"超短({duration}s): {title}"))
    except Exception as e:
        with lock:
            to_delete.append((bvid, f"异常: {e}"))
    with lock:
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"  🔄 {done[0]}/{total}  (已发现 {len(to_delete)} 个)")

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(check, bvids))

print(f"\n📊 发现 {len(to_delete)} 个待清除:")
for bvid, reason in to_delete[:20]:
    print(f"  {bvid}: {reason}")
if len(to_delete) > 20:
    print(f"  ... 等 {len(to_delete)-20} 个")

if not to_delete:
    print("✅ 无需清除！")
    exit()

# 3. Convert to avid:2 format
print(f"\n🔄 转换 avid...")
avid_list = []
for bvid, _ in to_delete:
    try:
        data = bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=8)
        avid = data.get("data", {}).get("aid")
        if avid:
            avid_list.append(f"{avid}:2")
    except:
        pass

print(f"  → {len(avid_list)} 个可删除")

if not avid_list:
    print("⚠️ 无可删除的 avid")
    exit()

# 4. Batch delete
print(f"\n🗑️ 批量删除 {len(avid_list)} 个...")
resources = ",".join(avid_list)

data = urllib.parse.urlencode({
    "src_media_id": DEFAULT_MLID,
    "resources": resources,
    "platform": "web",
    "csrf": CSRF,
}).encode()
req = urllib.request.Request(
    "https://api.bilibili.com/x/v3/fav/resource/batch-del",
    data=data, headers=HEADERS, method="POST"
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
    result = json.loads(resp.read())
code = result.get("code")
msg = result.get("message", "?")
print(f"  code={code}, message={msg}")

if code == 0:
    print(f"✅ 删除成功!")
else:
    print(f"❌ 删除失败: {msg}")
