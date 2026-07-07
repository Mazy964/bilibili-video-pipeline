# B站收藏夹 API 参考

> 账户: UID=11976717 | 收藏夹总数: 67 | 默认夹: 53个视频 (已清空)

## 认证要求

- 读操作：Cookie 中的 `SESSDATA`
- 写操作（POST）：Cookie 中的 `bili_jct` (CSRF token)

```http
Cookie: SESSDATA=<value>; bili_jct=<token>; DedeUserID=<uid>
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36
Referer: https://space.bilibili.com/
```

## API 端点

### 获取收藏夹列表
```
GET https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={uid}&platform=web
```
返回 `data.list[]` 每个收藏夹的 `id`, `title`, `media_count`, `attr`。

### 获取收藏夹内容（video list）
```
GET https://api.bilibili.com/x/v3/fav/resource/list?media_id={id}&pn=1&ps=20&platform=web
```
返回 `data.medias[]`，每个视频含 `id`, `bvid`, `title`。分页上限每页20。

### 批量获取视频IDs（更大容量）
```
GET https://api.bilibili.com/x/v3/fav/resource/ids?media_id={id}&platform=web
```
单次返回最多 1000 个视频的 bvid 列表。注意：`resource/list` 端点在某些收藏夹返回 412。

### 获取视频详情
```
GET https://api.bilibili.com/x/web-interface/view?bvid={bvid}
```
返回 `data.title`, `data.duration`, `data.owner.name`, `data.aid`, `data.cid`。

### 删除收藏（需 CSRF）
```
POST https://api.bilibili.com/x/v3/fav/resource/delete
Content-Type: application/x-www-form-urlencoded

resources={avid}:2&media_id={media_id}&platform=web&csrf={bili_jct}
```

### 移动收藏（需 CSRF）
```
POST https://api.bilibili.com/x/v3/fav/resource/move
Content-Type: application/x-www-form-urlencoded

resources=12345:2,67890:2&src_media_id={src}&tar_media_id={dst}&mid={uid}&platform=web&csrf={bili_jct}
```
⚠️ 参数名是 `resources`（不是 `res_media_ids` 或 `resources_ids`）。

## 避坑

1. `resource/list` 某些收藏夹返回 HTTP 412 → 改用 `resource/ids`
2. `attr` 位0=默认夹，位1=私有。默认夹的 ID 规则：`fid = uid + 个位数`，完整 ID = `fid * 100 + uid末两位`
3. 视频已删除返回 code=-404；视频私有化返回 code=62002
4. CSRF token (`bili_jct`) 在 Cookie 中，Safari 提取方式：`browser_cookie3.safari(domain_name='bilibili.com')`
