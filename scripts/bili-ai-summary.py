#!/usr/bin/env python3
"""Bilibili AI Summary — uses B站's built-in AI summary API.
Usage: bili-ai-summary.py <bvid|url> [--cookie-file <path>]
Exit codes:
  0 = got summary
  1 = API/network error
  2 = video has no AI summary
  3 = login required (need cookie)
"""
import sys, json, time, hashlib, urllib.request, urllib.parse, ssl, re, os

if len(sys.argv) < 2:
    print("Usage: bili-ai-summary.py <bvid|url> [--cookie-file <path>]", file=sys.stderr)
    sys.exit(1)

arg = sys.argv[1]
m = re.search(r'(BV[\w]+)', arg)
bvid = m.group(1) if m else arg

# Optional cookie file
cookie_file = None
for i, a in enumerate(sys.argv):
    if a == "--cookie-file" and i + 1 < len(sys.argv):
        cookie_file = sys.argv[i + 1]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
if cookie_file and os.path.exists(cookie_file):
    with open(cookie_file) as f:
        HEADERS["Cookie"] = f.read().strip()

CTX = ssl.create_default_context()

# ── WBI Signing ──

MIXIN_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

def get_mixin_key(orig):
    return ''.join(orig[i] for i in MIXIN_ENC_TAB)[:32]

def get_wbi_keys():
    req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
        data = json.loads(resp.read())
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key

def enc_wbi(params, img_key, sub_key):
    mixin_key = get_mixin_key(img_key + sub_key)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params

# ── Get video info ──

view_api = "https://api.bilibili.com/x/web-interface/view?bvid=" + bvid
req = urllib.request.Request(view_api, headers=HEADERS)
try:
    with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
        view = json.loads(resp.read())["data"]
except Exception as e:
    print(f"获取视频信息失败: {e}", file=sys.stderr)
    sys.exit(1)

cid = view["cid"]
up_mid = view["owner"]["mid"]
title = view["title"]
duration = view["duration"]
print(f"视频: {title} ({duration}s)", file=sys.stderr)

# ── Get AI summary ──

img_key, sub_key = get_wbi_keys()
params = enc_wbi({"bvid": bvid, "cid": cid, "up_mid": up_mid}, img_key, sub_key)

summary_api = "https://api.bilibili.com/x/web-interface/view/conclusion/get?" + urllib.parse.urlencode(params)
req = urllib.request.Request(summary_api, headers=HEADERS)
try:
    with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
        conclusion = json.loads(resp.read())
except Exception as e:
    print(f"AI总结API请求失败: {e}", file=sys.stderr)
    sys.exit(1)

if conclusion["code"] != 0:
    msg = conclusion.get("message", "")
    if "登录" in msg or "未登录" in msg:
        print("需要登录 (提供 --cookie-file)", file=sys.stderr)
        sys.exit(3)
    print(f"API 错误: {msg}", file=sys.stderr)
    sys.exit(1)

data = conclusion.get("data", {})
if data.get("code", -1) != 0:
    print("视频暂无 AI 总结", file=sys.stderr)
    sys.exit(2)

model = data.get("model_result", {})
result_type = model.get("result_type", 0)

print(f"=== B站AI总结 (result_type={result_type}) ===")
print()
print(model.get("summary", ""))

outline = model.get("outline")
if outline:
    print()
    print("--- 分段提纲 ---")
    for item in outline:
        ts = item.get("timestamp", 0)
        mins = int(ts // 60)
        secs = int(ts % 60)
        print(f"\n[{mins:02d}:{secs:02d}] {item.get('title', '')}")
        for part in item.get("part_outline", []):
            pts = part.get("timestamp", 0)
            pm = int(pts // 60)
            ps = int(pts % 60)
            print(f"  [{pm:02d}:{ps:02d}] {part.get('content', '')}")
