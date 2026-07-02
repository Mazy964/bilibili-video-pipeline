#!/usr/bin/env python3
"""
批量清除指定UP主的所有收藏视频 v3

流程: keyword全局搜索 → 批量解除收藏（直接删，不绕临时夹）
  - 用 resource/list?type=1 搜全夹，一次拿到全部匹配视频
  - 每个结果自带 avid（id字段），无需二次转换
  - 搜索结果的 upper.name 精确匹配确认归属
  - 用 resource/deal 逐个解除（del_media_ids 用搜索结果里的 folder 信息）
"""
import json, os, ssl, time, urllib.request, urllib.parse
from collections import defaultdict

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


def search_global(keyword, max_pages=5):
    """全局搜索收藏夹，返回 [(bvid, avid, upper_name, title), ...]"""
    results = []
    encoded = urllib.parse.quote(keyword)
    for pn in range(1, max_pages + 1):
        try:
            url = (f"https://api.bilibili.com/x/v3/fav/resource/list"
                   f"?media_id={DEFAULT_MLID}&pn={pn}&ps=20&platform=web"
                   f"&keyword={encoded}&order=mtime&type=1&tid=0")
            d = bili_get(url)
            items = d.get("data", d).get("medias", [])
            if not items:
                break
            for item in items:
                upper = item.get("upper", {}).get("name", "")
                if upper and keyword in upper:
                    avid = item.get("id")  # resource/list 返回的 id 就是 avid!
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
    """取消收藏一个视频。用 deal API，del_media_ids 填默认夹即可（B站会自动找到）。"""
    for attempt in range(3):
        try:
            data = urllib.parse.urlencode({
                "rid": avid, "type": "2",
                "add_media_ids": "", "del_media_ids": DEFAULT_MLID,
                "platform": "web", "csrf": CSRF,
            }).encode()
            req = urllib.request.Request(
                "https://api.bilibili.com/x/v3/fav/resource/deal",
                data=data, headers=HEADERS, method="POST"
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


def cleanup(upper_names, max_pages=10):
    print(f"🎯 {len(upper_names)} 个UP主\n")

    all_found = []
    for kw in upper_names:
        found = search_global(kw, max_pages)
        print(f"  {kw}: {len(found)} 个")
        for bvid, avid, upper, title in found[:3]:
            print(f"    {bvid} {title}")
        if len(found) > 3:
            print(f"    ... 等 {len(found)-3} 个")
        all_found.extend(found)

    # Dedup by bvid
    seen = set()
    unique = []
    for bvid, avid, upper, title in all_found:
        if bvid not in seen:
            seen.add(bvid)
            unique.append((bvid, avid, upper, title))

    print(f"\n📊 唯一: {len(unique)} 个")

    if not unique:
        print("✅ 无需清理")
        return

    # Confirm
    print(f"\n🗑️ 解除收藏 {len(unique)} 个...")
    ok = 0
    for i, (bvid, avid, upper, title) in enumerate(unique):
        success, msg = unfavorite(avid)
        if success:
            ok += 1
            if (i + 1) % 10 == 0:
                print(f"  ✅ {i+1}/{len(unique)}")
        else:
            print(f"  ❌ {bvid} [{upper}]: {msg}")
        time.sleep(1.5)  # 1.5s per deletion to avoid rate limit

    print(f"\n✅ {ok}/{len(unique)}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # CLI mode
        uppers = sys.argv[1:]
    else:
        # Default list
        uppers = [
            "吃素的狮子","男孩不靠普CantoMando","歪果仁研究协会","东京大明白",
            "TESTV官方频道","小潮院长","老番茄","敬汉卿","仲尼Johnny777",
            "vivi可爱多","小缸和阿灿","终极小腾","俊晖JAN","信誓蛋蛋",
            "我是郭杰瑞","某幻君","兔叭咯","记录生活的蛋黄派","啊吗粽",
            "老爸评测","大祥哥来了","水蛭-JogsLeech",
        ]
    cleanup(uppers)
