#!/usr/bin/env python3
"""
fav-manage.py — B站收藏夹在线管理
================================
基于 B站 API 的收藏夹批量操作：
  - move: 批量移动视频到目标文件夹
  - merge: 合并源文件夹到目标（移动全部 + 删除源）
  - create: 新建收藏夹
  - delete: 删除收藏夹
  - classify   : 基于分类文件执行全量移动

API 要点：
  - resources 参数格式: avid:2（数字，非 BVID）
  - 需要 mid + csrf 参数
  - 每文件夹上限 999 个视频
  - 目录满时自动跳过

Usage:
  # 小范围测试
  fav-manage.py move --src mlid --dst mlid --bvids BV1xx,BV2yy
  fav-manage.py merge --src mlid --dst mlid
  fav-manage.py create "新文件夹名"
  fav-manage.py delete mlid

  # 全量分类执行
  fav-manage.py classify --file cache/fav-classify-final.json --dry-run
  fav-manage.py classify --file cache/fav-classify-final.json
"""

import json, os, sys, ssl, time, urllib.request, urllib.parse
from collections import defaultdict

MID = "11976717"
COOKIE_FILE = os.path.expanduser("~/.hermes/bilibili_cookies.txt")

# ── Config ──
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
            if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "sid"):
                parts.append(f"{name}={value}")
            if name == "bili_jct":
                CSRF = value
    if parts:
        HEADERS["Cookie"] = "; ".join(parts)

SSL_CTX = ssl.create_default_context()


def bili_get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        data = json.loads(resp.read())
    code = data.get("code")
    if code != 0:
        raise RuntimeError(f"API code={code}: {data.get('message', '?')}")
    return data.get("data", data)


def bili_post(url, form_data, timeout=15):
    data = urllib.parse.urlencode(form_data).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return json.loads(resp.read())


# ── BVID ↔ avid ──

def bvid_to_aid(bvid):
    """Convert BVID to avid. Returns None if video deleted."""
    try:
        data = bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        return data.get("aid")
    except RuntimeError:
        return None


def get_folder_videos(mlid):
    """Get BVID list from a folder. Capped at B站's return limit (~1000)."""
    data = bili_get(
        f"https://api.bilibili.com/x/v3/fav/resource/ids?media_id={mlid}&platform=web"
    )
    if isinstance(data, list):
        return [item.get("bv_id") or item.get("bvid") for item in data]
    return []


def get_folder_info(mlid):
    """Get folder metadata. Returns {title, media_count, ...}."""
    return bili_get(f"https://api.bilibili.com/x/v3/fav/folder/info?media_id={mlid}")


def get_all_folders():
    """Get all user-created folders. Returns [{id, title, media_count}, ...]."""
    data = bili_get(
        f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={MID}"
    )
    return data.get("list", [])


# ── Actions ──

def move_videos(src_mlid, dst_mlid, bvids):
    """Move videos between folders. bvids: list of BVID strings.
    Returns (success_count, error_message)."""
    avid_list = []
    skipped = []
    for bvid in bvids:
        avid = bvid_to_aid(bvid)
        if avid is None:
            skipped.append(bvid)
            continue
        avid_list.append(f"{avid}:2")

    if not avid_list:
        return 0, "没有可移动的视频"

    if skipped:
        print(f"  ⚠️  跳过 {len(skipped)} 个失效视频: {skipped}")

    resources = ",".join(avid_list)
    result = bili_post(
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
    code = result.get("code")
    msg = result.get("message", "?")
    if code == 0:
        return len(avid_list), None
    return 0, f"code={code} {msg}"


def create_folder(title, intro=""):
    """Create a new favorites folder. Returns mlid or None."""
    result = bili_post(
        "https://api.bilibili.com/x/v3/fav/folder/add",
        {
            "title": title,
            "intro": intro,
            "privacy": "0",
            "cover": "",
            "csrf": CSRF,
        },
    )
    if result.get("code") == 0:
        fid = result.get("data", {}).get("id") or result.get("data")
        print(f"  ✅ 创建成功: {title} (mlid={fid})")
        return fid
    else:
        print(f"  ❌ 创建失败: {result.get('message')}")
        return None


def delete_folder(mlid):
    """Delete a favorites folder. Returns True on success."""
    result = bili_post(
        "https://api.bilibili.com/x/v3/fav/folder/del",
        {"media_ids": str(mlid), "csrf": CSRF},
    )
    return result.get("code") == 0


def merge_folders(src_mlid, dst_mlid):
    """Merge source folder into destination (move all + delete source).
    Returns (moved, deleted, error)."""
    src_info = get_folder_info(src_mlid)
    dst_info = get_folder_info(dst_mlid)
    src_title = src_info.get("title", "?")
    dst_title = dst_info.get("title", "?")
    src_count = src_info.get("media_count", 0)
    dst_count = dst_info.get("media_count", 0)

    print(f"\n📂 合并: [{src_title}]({src_count}) → [{dst_title}]({dst_count})")

    if dst_count >= 999:
        return 0, False, "目标文件夹已满（999上限）"

    # Get source BVIDs
    bvids = get_folder_videos(src_mlid)
    print(f"  → 源夹 {len(bvids)} 个视频")

    if not bvids:
        return 0, delete_folder(src_mlid), "源夹为空"

    # Move
    moved, err = move_videos(src_mlid, dst_mlid, bvids)
    if err:
        return moved, False, err

    print(f"  → 已移动 {moved} 个")

    # Delete
    if delete_folder(src_mlid):
        print(f"  ✅ 已删除源文件夹")
        return moved, True, None
    else:
        return moved, False, "删除源文件夹失败"


def classify_execute(classification_file, default_mlid, folders_map, dry_run=False):
    """Execute full classification: move videos to target folders.
    
    Args:
        classification_file: path to fav-classify-final.json
        default_mlid: the default folder's media_id
        folders_map: {folder_name: mlid} dict
        dry_run: if True, only print plan
    """
    with open(classification_file) as f:
        classify = json.load(f)

    # Group by target folder
    plan = defaultdict(list)
    for bvid, info in classify.items():
        target = info.get("suggested_folder", "")
        if target and target in folders_map:
            plan[target].append(bvid)

    total = sum(len(v) for v in plan.values())
    print(f"\n{'🧪 DRY RUN' if dry_run else '🚀 执行'} 分类移动")
    print(f"  视频总数: {len(classify)}")
    print(f"  可移动: {total} (分布到 {len(plan)} 个文件夹)")
    print(f"  默认夹 mlid: {default_mlid}")

    # Check folder capacities
    folder_info = {}
    for name, mlid in folders_map.items():
        try:
            fi = get_folder_info(mlid)
            folder_info[name] = {
                "mlid": mlid,
                "count": fi.get("media_count", 0),
                "added": len(plan.get(name, [])),
            }
        except Exception as e:
            folder_info[name] = {"mlid": mlid, "count": "?", "added": len(plan.get(name, [])), "error": str(e)}

    # Show plan
    print(f"\n📋 计划:")
    for name in sorted(folder_info.keys(), key=lambda n: -folder_info[n]["added"]):
        fi = folder_info[name]
        count = fi["count"]
        added = fi["added"]
        after = count + added if isinstance(count, int) else "?"
        flag = " ⚠️会超999!" if isinstance(count, int) and count + added > 999 else " ✅"
        print(f"  [{name}] {count} + {added} = {after}{flag}")

    if dry_run:
        return

    # Execute
    print(f"\n⏳ 开始执行...")
    success = 0
    fail = 0
    skip_full = 0

    for name in sorted(folder_info.keys()):
        fi = folder_info[name]
        bvids = plan.get(name, [])
        if not bvids:
            continue

        if isinstance(fi["count"], int) and fi["count"] + len(bvids) > 999:
            print(f"  ⏭️ [{name}] 目标已满，跳过 ({fi['count']}+{len(bvids)}>999)")
            skip_full += len(bvids)
            continue

        moved, err = move_videos(default_mlid, fi["mlid"], bvids)
        if err:
            print(f"  ❌ [{name}] {err}")
            fail += len(bvids)
        else:
            print(f"  ✅ [{name}] {moved}/{len(bvids)}")
            success += moved

        time.sleep(2)  # Rate limit

    print(f"\n📊 完成: 成功 {success}, 失败 {fail}, 跳过(满) {skip_full}")


# ── CLI ──

def usage():
    print(__doc__)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0]

    if cmd == "move":
        src = dst = bvids = None
        for i, a in enumerate(args):
            if a == "--src" and i + 1 < len(args):
                src = args[i + 1]
            elif a == "--dst" and i + 1 < len(args):
                dst = args[i + 1]
            elif a == "--bvids" and i + 1 < len(args):
                bvids = args[i + 1].split(",")
        if not all([src, dst, bvids]):
            print("用法: fav-manage.py move --src mlid --dst mlid --bvids BV1xx,BV2yy")
            sys.exit(1)
        moved, err = move_videos(src, dst, bvids)
        if err:
            print(f"❌ {err}")
            sys.exit(1)
        print(f"✅ 移动 {moved} 个")

    elif cmd == "create":
        if len(args) < 2:
            print("用法: fav-manage.py create \"文件夹名\"")
            sys.exit(1)
        title = args[1]
        mlid = create_folder(title)
        if mlid:
            print(mlid)

    elif cmd == "delete":
        if len(args) < 2:
            print("用法: fav-manage.py delete mlid")
            sys.exit(1)
        mlid = args[1]
        ok = delete_folder(mlid)
        print("✅" if ok else "❌")

    elif cmd == "merge":
        src = dst = None
        for i, a in enumerate(args):
            if a == "--src" and i + 1 < len(args):
                src = args[i + 1]
            elif a == "--dst" and i + 1 < len(args):
                dst = args[i + 1]
        if not all([src, dst]):
            print("用法: fav-manage.py merge --src mlid --dst mlid")
            sys.exit(1)
        moved, deleted, err = merge_folders(src, dst)
        if err:
            print(f"❌ {err}")
            sys.exit(1)
        print(f"✅ 合并完成 ({moved} 移动)")

    elif cmd == "classify":
        file_path = None
        dry_run = "--dry-run" in args
        for i, a in enumerate(args):
            if a == "--file" and i + 1 < len(args):
                file_path = args[i + 1]
        if not file_path:
            print("用法: fav-manage.py classify --file cache/fav-classify-final.json [--dry-run]")
            sys.exit(1)

        # Load folders map
        folders = get_all_folders()
        folders_map = {}
        default_mlid = None
        for f in folders:
            title = f.get("title", "")
            mlid = f.get("id")
            folders_map[title] = mlid
            if title == "默认收藏夹":
                default_mlid = mlid

        if not default_mlid:
            # fallback
            default_mlid = max(int(f.get("id")) for f in folders)
            print(f"⚠️ 未找到默认夹，使用最大 mlid={default_mlid}")

        classify_execute(file_path, default_mlid, folders_map, dry_run=dry_run)

    elif cmd == "list-folders":
        folders = get_all_folders()
        for f in folders:
            print(f"{f.get('id')}\t{f.get('title')}\t{f.get('media_count')}")

    else:
        print(f"未知命令: {cmd}")
        usage()


if __name__ == "__main__":
    main()
