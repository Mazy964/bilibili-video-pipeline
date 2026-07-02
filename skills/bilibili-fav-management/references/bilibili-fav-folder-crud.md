# B站收藏夹 CRUD API 参考

实际操作验证通过的端点与参数。

## 新建收藏夹

```
POST https://api.bilibili.com/x/v3/fav/folder/add
Content-Type: application/x-www-form-urlencoded

title=🧪测试&intro=&privacy=0&cover=&csrf=xxx
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | str | 是 | 文件夹名 |
| intro | str | 否 | 简介 |
| privacy | str | 否 | 0=公开 |
| cover | str | 否 | 封面URL |
| csrf | str | 是 | Cookie中的bili_jct |

成功响应：
```json
{"code": 0, "message": "OK", "data": {"id": 4082421417}}
```
`data.id` 即为该文件夹的 mlid（完整 media_id）。

## 编辑/改名收藏夹

```
POST https://api.bilibili.com/x/v3/fav/folder/edit
Content-Type: application/x-www-form-urlencoded

media_id=111506717&title=新名字&csrf=xxx
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| media_id | str | 是 | 要改名的文件夹 mlid |
| title | str | 是 | 新名字 |
| csrf | str | 是 | Cookie中的bili_jct |

成功响应：`{"code": 0, "message": "OK"}`。

> ✅ 实测可用。以前以为 B站没有改名 API，实际有 `folder/edit`。比创建新夹+全量迁移+删旧夹高效得多。

## 删除收藏夹

```
POST https://api.bilibili.com/x/v3/fav/folder/del
Content-Type: application/x-www-form-urlencoded

media_ids=4082421417&csrf=xxx
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| media_ids | str | 是 | **注意：参数名是 media_ids（复数）**，值为 mlid |
| csrf | str | 是 | Cookie中的bili_jct |

成功响应：
```json
{"code": 0, "message": "OK", "data": 0}
```

## 获取全部文件夹

```
GET https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid=11976717
```

返回 `data.list[]`，每项：`{id, title, media_count, ...}`。`id` 即为各文件夹的 mlid。

默认收藏夹也可以通过此接口找到：`title == "默认收藏夹"`。

## 移动收藏（resource/move）

```
POST https://api.bilibili.com/x/v3/fav/resource/move
Content-Type: application/x-www-form-urlencoded

src_media_id=72927717&tar_media_id=3654805517&
resources=115825101047931:2,115825101047932:2&
mid=11976717&platform=web&csrf=xxx
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| src_media_id | str | 是 | 源文件夹 mlid |
| tar_media_id | str | 是 | 目标文件夹 mlid |
| resources | str | 是 | `{avid}:2` 格式，多个逗号分隔 |
| mid | str | **是** | 用户 UID（容易漏掉） |
| platform | str | 否 | 填 `web` |
| csrf | str | 是 | Cookie中的bili_jct |

**关键坑**：
1. `resources` 是 `avid:2`（数字:类型），**不是 BVID**
2. **必须传 `mid`**（用户 UID），不传会返回 code=0 但 data=0 且无实际操作
3. 响应 `{"code":0, "message":"OK", "data":0}` — `data:0` 是正常的，不是"移动了0个"
4. 失效视频（404）会静默跳过

## 合并文件夹完整流程

已验证可用的合并模式（测试了"网络"(10个) → "网络(2)"(17个))：

```python
# 1. 用 resource/ids 获取源夹全部 BVID
bvids = get_folder_videos(src_mlid)

# 2. 逐 BVID 转 avid（用 view API）
avid_list = []
for bvid in bvids:
    avid = bvid_to_aid(bvid)  # view API → data.aid
    if avid is None:  # 已删，跳过
        continue
    avid_list.append(f"{avid}:2")

# 3. 批量移动
resources = ",".join(avid_list)
bili_post("resource/move", {
    "src_media_id": src_mlid,
    "tar_media_id": dst_mlid,
    "resources": resources,
    "mid": "11976717",
    "platform": "web",
    "csrf": csrf,
})

# 4. 删除空源夹
bili_post("folder/del", {"media_ids": src_mlid, "csrf": csrf})
```

## 每文件夹容量上限

B站限制：**每文件夹最多 999 个视频**。

合并/批量移动前必须检查目标夹当前数量：
- `folder/info?media_id=` 返回 `media_count`
- 若 `media_count + 新增 > 999` → 跳过此文件夹，报告超限
- 已满的文件夹（999）无法再移入任何视频

## 限流策略

- 单次 move 支持批量（实测 10 个一批没问题）
- 连续多次 move 之间建议 sleep 2s
- `view` API 用于 avid 转换，多线程可做到 ~2 req/s
