# B站收藏夹移动 API

## 端点

```
POST https://api.bilibili.com/x/v3/fav/resource/move
Content-Type: application/x-www-form-urlencoded
认证方式: Cookie (SESSDATA) + CSRF (bili_jct)
```

## 参数

| 参数 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `src_media_id` | str | ✅ | 源收藏夹 ID（list-all 返回的 `id` 直接使用） |
| `tar_media_id` | str | ✅ | 目标收藏夹 ID |
| `resources` | str | ✅ | **`{avid}:2,{avid}:2,...`** 格式。末尾 `:2` = 视频稿件类型 |
| `mid` | str | ✅ | 用户 UID |
| `csrf` | str | ✅ | Cookie 中的 `bili_jct` |
| `platform` | str | 否 | 固定 `"web"` |

## 成功响应

```json
{"code": 0, "message": "OK", "data": 0}
```

> ⚠️ `data: 0` 是正常成功响应，不是"0 个移动成功"。

## 正确调用示例

```python
import urllib.request, urllib.parse, json, ssl

SSL_CTX = ssl.create_default_context()

# 先通过 view API 获取 avid（数字 ID）
req = urllib.request.Request(
    "https://api.bilibili.com/x/web-interface/view?bvid=BV17ZvfBHEzZ",
    headers=HEADERS
)
with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
    avid = json.loads(resp.read())["data"]["aid"]

# resources 格式：{avid}:2
form = {
    "src_media_id": "72927717",        # 源文件夹
    "tar_media_id": "3654805517",      # 目标文件夹
    "resources": f"{avid}:2",
    "mid": "11976717",                 # ⚠️ 必传
    "platform": "web",
    "csrf": csrf,
}

data = urllib.parse.urlencode(form).encode()
req = urllib.request.Request(
    "https://api.bilibili.com/x/v3/fav/resource/move",
    data=data,
    headers=HEADERS,
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")
with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
    result = json.loads(resp.read())
```

## 常见错误

| 错误 | 原因 | 修复 |
|---|---|---|
| `code=-400` | 参数名/格式错误 | 检查参数名 `resources`（不是 `res_media_ids`） |
| `code=0, data=0` 但未移动 | 视频已失效/不在源文件夹 | 先 `view` API 验证存在性 |
| `code=-101` | 未登录 | 检查 SESSDATA cookie |
| `code=-111` | CSRF 校验失败 | 刷新 `bili_jct` |
| `code=11010` | 内容不存在 | 视频已删除 |
| `data=0` 但不知是否移动 | **这不是错误！** | 正常成功响应，验证目标文件夹即可 |

## 批量移动注意

- 单次 `resources` 可包含多个：`"123:2,456:2,789:2"`
- 大量移动建议分批（每批 20-50 个），避免单次请求过大
- 移动操作**不可逆**（除非再移回去），建议先小范围测试
- 移动前过滤已失效视频（`view` API 返回 code=-404 的跳过）
- 移动前确认视频仍在源文件夹（遍历所有文件夹用 `resource/ids` 搜索）

## 获取所有文件夹列表

```python
# 返回所有文件夹，包括默认收藏夹
url = "https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid=11976717"
# 返回的 list 中每个元素：
#   id: str (直接用作 media_id)
#   title: str
#   media_count: int
```
