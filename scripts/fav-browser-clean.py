#!/usr/bin/env python3
"""
fav-browser-clean.py — 浏览器兜底清理（API 搜 + 浏览器删）

当 API 不支持跨夹批量操作时，用浏览器完成可视化清理。

策略：
  1. API keyword 搜索 → 定位目标视频（突破 1000 上限）
  2. 浏览器导航到 B站收藏夹
  3. 搜索 UP主名 → 批量勾选 → 一键删除

浏览器操作指引（给 Agent 用）：
  ┌─────────────────────────────────────────────────┐
  │ 1. 打开 https://space.bilibili.com/{mid}/favlist│
  │ 2. 在搜索框输入 UP主名                          │
  │ 3. 点击"批量操作"→ 勾选"全选"                    │
  │ 4. 点击"删除"→ 确认                              │
  │ 5. 翻页重复（如有）                              │
  └─────────────────────────────────────────────────┘

适用场景：
  - 跨多个收藏夹的 UP主 批量删除（API 需逐个文件夹处理）
  - 批量修改文件夹名/封面（无对应 API）
  - 清除失效内容（API clean 偶尔无效时）
"""

import json, os, ssl, time, urllib.request, urllib.parse

MID = "11976717"
COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")
DEFAULT_MLID = "72927717"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
CSRF = ""
if os.path.exists(COOKIE_FILE):
    parts = []
    for line in open(COOKIE_FILE):
        if line.startswith("#") or not line.strip():
            continue
        fields = line.strip().split("\t")
        if len(fields) >= 7:
            name, value = fields[5], fields[6]
            if name == "bili_jct":
                CSRF = value
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "sid"):
                parts.append(f"{name}={value}")
    if parts:
        HEADERS["Cookie"] = "; ".join(parts)

SSL_CTX = ssl.create_default_context()


def bili_get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())


def search_global(keyword, max_pages=5):
    """全局搜索收藏夹。返回 [(bvid, avid, upper_name, title), ...]"""
    results = []
    encoded = urllib.parse.quote(keyword)
    for pn in range(1, max_pages + 1):
        url = (
            f"https://api.bilibili.com/x/v3/fav/resource/list"
            f"?media_id={DEFAULT_MLID}&pn={pn}&ps=20&platform=web"
            f"&keyword={encoded}&order=mtime&type=1&tid=0"
        )
        try:
            d = bili_get(url)
            items = d.get("data", d).get("medias") or []
            if not items:
                break
            for item in items:
                upper = item.get("upper", {}).get("name", "")
                if upper and keyword in upper:
                    avid = item.get("id")
                    bvid = item.get("bvid")
                    title = item.get("title", "")[:50]
                    if avid:
                        results.append((bvid, str(avid), upper, title))
            if len(items) < 20:
                break
        except Exception as e:
            print(f"  ⚠️ pn={pn}: {e}")
            break
        time.sleep(0.3)
    return results


def unfavorite(avid):
    """取消收藏一个视频。"""
    for attempt in range(3):
        try:
            data = urllib.parse.urlencode(
                {
                    "rid": avid,
                    "type": "2",
                    "add_media_ids": "",
                    "del_media_ids": DEFAULT_MLID,
                    "platform": "web",
                    "csrf": CSRF,
                }
            ).encode()
            req = urllib.request.Request(
                "https://api.bilibili.com/x/v3/fav/resource/deal",
                data=data,
                headers=HEADERS,
                method="POST",
            )
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
                r = json.loads(resp.read())
            return r.get("code") == 0, r.get("message", "?")
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                return False, str(e)
    return False, "unknown"


def api_cleanup(upper_names):
    """API 方式清理：搜索 → 逐个解除收藏。返回未成功的 [(bvid,avid,upper), ...]"""
    print(f"🔍 API 搜索 {len(upper_names)} 个UP主...\n")

    all_found = []
    for kw in upper_names:
        found = search_global(kw)
        count = len(found)
        if count:
            all_found.extend(found)
        print(f"  {kw}: {count} 个")

    seen = set()
    unique = []
    for bvid, avid, upper, title in all_found:
        if bvid not in seen:
            seen.add(bvid)
            unique.append((bvid, avid, upper, title))

    print(f"\n📊 唯一: {len(unique)} 个")

    if not unique:
        print("✅ 已清空")
        return []

    print(f"\n🗑️ API 解除收藏...")
    ok = 0
    remaining = []
    for i, (bvid, avid, upper, title) in enumerate(unique):
        success, msg = unfavorite(avid)
        if success:
            ok += 1
        else:
            remaining.append((bvid, avid, upper, title))
            print(f"  ❌ {bvid} [{upper}]: {msg}")
        if (i + 1) % 10 == 0:
            print(f"  ✅ {i+1}/{len(unique)}")
        time.sleep(1.5)

    print(f"\n✅ API: {ok}/{len(unique)}")

    if remaining:
        print(f"\n⚠️ {len(remaining)} 个 API 失败，建议用浏览器手动清理:")
        for bvid, avid, upper, title in remaining:
            print(f"    https://www.bilibili.com/video/{bvid} [{upper}] {title}")
        print(f"\n💡 浏览器操作: 打开 https://space.bilibili.com/{MID}/favlist")
        print(f"    → 搜索 UP主名 → 批量操作 → 全选 → 删除")

    return remaining


# ── Browser fallback guide ──

BROWSER_GUIDE = r"""
╔══════════════════════════════════════════════════════════╗
║           🌐 浏览器兜底：B站收藏夹批量清理              ║
╠══════════════════════════════════════════════════════════╣
║                                                        ║
║  1. 导航到:                                            ║
║     https://space.bilibili.com/{mid}/favlist            ║
║                                                        ║
║  2. 在左侧收藏夹列表点击目标夹（或搜全夹用默认夹）       ║
║                                                        ║
║  3. 在顶部搜索框输入 UP主名称                           ║
║                                                        ║
║  4. 点击上方 "批量操作" 按钮                            ║
║                                                        ║
║  5. 勾选 "全选本页" → 点击 "删除"                       ║
║                                                        ║
║  6. 确认删除 → 翻到下一页 → 重复 4-5                    ║
║                                                        ║
║  💡 "清除失效内容" = API clean 端点                     ║
║  💡 此方式可跨夹批量操作，无需逐个文件夹切换              ║
║                                                        ║
╚══════════════════════════════════════════════════════════╝
""".format(mid=MID)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--browser-guide":
        print(BROWSER_GUIDE)
    elif len(sys.argv) > 1:
        remaining = api_cleanup(sys.argv[1:])
    else:
        print("用法:")
        print("  fav-browser-clean.py UP主1 [UP主2 ...]  — 搜索+API清理")
        print("  fav-browser-clean.py --browser-guide       — 浏览器操作指南")
        print()
        print(BROWSER_GUIDE)
