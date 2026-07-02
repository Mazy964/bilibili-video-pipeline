#!/usr/bin/env python3
"""
批量清除指定UP主的所有收藏视频

策略: API keyword搜索 → 临时夹收纳 → 整夹删除
  - 分类缓存: 快速（覆盖 7279 原始视频）
  - keyword搜索: 兜底（突破 API 1000条上限，扫全夹）
  - 临时夹: 批量移入后整夹删除，绕过逐个 API 限流
"""
import json, os, ssl, time, urllib.request, urllib.parse
from collections import defaultdict

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

def bili_get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())

def bili_post(url, form_data, timeout=15):
    d = urllib.parse.urlencode(form_data).encode()
    req = urllib.request.Request(url, data=d, headers=HEADERS, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())


def cleanup_uppers(upper_names, cache_file="cache/fav-classify-final.json"):
    """Main cleanup: 1) cache lookup, 2) keyword deep search, 3) trash-folder delete."""
    t0 = time.time()
    
    # -- Phase 1: Classification cache --
    bvids_found = set()
    try:
        with open(cache_file) as f:
            classify = json.load(f)
        for bvid, info in classify.items():
            if info.get("upper") in set(upper_names) and info.get("suggested_folder") != "🗑️待删除":
                bvids_found.add(bvid)
    except FileNotFoundError:
        print("⚠️ 无分类缓存，跳过 Phase 1")
    
    print(f"📋 Phase 1 (缓存): {len(bvids_found)} 个")
    
    # -- Phase 2: Keyword deep search across ALL folders --
    print(f"🔍 Phase 2 (全夹搜索)...")
    for kw in upper_names:
        encoded = urllib.parse.quote(kw)
        for pn in range(1, 10):
            try:
                url = f"https://api.bilibili.com/x/v3/fav/resource/list?media_id=72927717&pn={pn}&ps=20&platform=web&keyword={encoded}&order=mtime&type=1&tid=0"
                d = bili_get(url)
                items = d.get("data", d).get("medias", [])
                if not items:
                    break
                for item in items:
                    upper = item.get("upper", {}).get("name", "")
                    if upper and kw in upper:
                        bvids_found.add(item.get("bvid"))
                if len(items) < 20:
                    break
            except:
                pass
            time.sleep(0.3)
    
    all_bvids = list(bvids_found)
    print(f"  → 总计: {len(all_bvids)} 个唯一视频")
    
    if not all_bvids:
        print("✅ 无需清理")
        return 0
    
    # -- Phase 3: Find locations + convert to avid --
    print(f"\n🗺️ 定位 {len(all_bvids)} 个视频...")
    
    # Get folder map
    d = bili_get(f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={MID}")
    folders = d["data"]["list"]
    
    # Scan all folders for BVIDs
    folder_bvids = {}
    for f in folders:
        mid = f["id"]
        try:
            r = bili_get(f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={mid}&platform=web")
            items = r.get("data", r) if isinstance(r, dict) else r
            folder_bvids[mid] = [item.get("bv_id") or item.get("bvid") for item in (items if isinstance(items, list) else [])]
        except:
            folder_bvids[mid] = []
        time.sleep(0.2)
    
    # Convert + locate
    by_folder = defaultdict(list)
    for bvid in all_bvids:
        try:
            v = bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=10)
            avid = v.get("data", {}).get("aid")
            if not avid:
                continue
            # Find folder
            for mid, bv_list in folder_bvids.items():
                if bvid in bv_list:
                    by_folder[mid].append(str(avid))
                    break
        except:
            pass
        time.sleep(0.3)
    
    total = sum(len(v) for v in by_folder.values())
    print(f"  → 定位 {total} 个, 在 {len(by_folder)} 个文件夹")
    
    # -- Phase 4: Trash folder strategy --
    print(f"\n📦 创建临时夹...")
    r = bili_post("https://api.bilibili.com/x/v3/fav/folder/add", {
        "title": "🧹待清理", "intro": "auto", "privacy": "0", "cover": "", "csrf": CSRF,
    })
    trash = r.get("data", {}).get("id")
    if not trash:
        print("❌ 创建临时夹失败"); return 0
    print(f"  mlid={trash}")
    
    moved = 0
    for mid, avids in by_folder.items():
        res = ",".join(f"{a}:2" for a in avids)
        r = bili_post("https://api.bilibili.com/x/v3/fav/resource/move", {
            "src_media_id": str(mid), "tar_media_id": str(trash),
            "resources": res, "mid": MID, "platform": "web", "csrf": CSRF,
        })
        fn = next((f["title"] for f in folders if f["id"]==mid), "?")
        ok = "✅" if r.get("code") == 0 else "❌"
        if r.get("code") == 0: moved += len(avids)
        print(f"  {ok} [{fn}] {len(avids)}个: {r.get('message')}")
    
    # Delete trash
    r = bili_post("https://api.bilibili.com/x/v3/fav/folder/del", {
        "media_ids": str(trash), "csrf": CSRF,
    })
    print(f"🗑️ 删除临时夹: {r.get('message')}")
    
    elapsed = time.time() - t0
    print(f"\n✅ {moved} 个已清除 ({elapsed/60:.1f}min)")
    return moved


if __name__ == "__main__":
    cleanup_uppers([
        "吃素的狮子","男孩不靠普CantoMando","歪果仁研究协会","东京大明白",
        "TESTV官方频道","小潮院长","老番茄","敬汉卿","仲尼Johnny777",
        "vivi可爱多","小缸和阿灿","终极小腾","俊晖JAN","信誓蛋蛋",
        "我是郭杰瑞","某幻君","兔叭咯","记录生活的蛋黄派","啊吗粽",
        "老爸评测","大祥哥来了","水蛭-JogsLeech",
    ])
