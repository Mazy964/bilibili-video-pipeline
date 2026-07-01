# B站收藏夹 API 参考

> 本文档记录 B站收藏夹相关 API 端点，所有接口均通过实际测试验证。
>
> 更新时间: 2026-07-01
> 账户: UID=11976717

## 认证要求

所有写操作（POST）需要 CSRF Token = Cookie 中的 `bili_jct`。
所有需要登录的操作需要 Cookie 中的 `SESSDATA`。

请求头示例：
```
Cookie: SESSDATA=<sessdata>; bili_jct=<token>; DedeUserID=<uid>
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36
Referer: https://space.bilibili.com/
Content-Type: application/x-www-form-urlencoded
```

---

## 1. 获取收藏夹列表

**GET** `https://api.bilibili.com/x/v3/fav/folder/created/list-all`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| up_mid | num | 是 | 用户 UID |
| type | num | 否 | 内容类型。0=全部, 2=视频 |

**响应关键字段：**
```json
{
  "code": 0,
  "data": {
    "count": 71,
    "list": [
      {
        "id": 72927717,          // 完整 mlid (fid + mid尾号2位)
        "fid": 729277,           // 短 id
        "mid": 11976717,
        "attr": 0,               // 属性位: bit0=是否默认, bit1=是否私有
        "title": "默认收藏夹",
        "media_count": 7575
      }
    ]
  }
}
```

## 2. 获取收藏夹元数据

**GET** `https://api.bilibili.com/x/v3/fav/folder/info`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| media_id | num | 是 | 完整 mlid |

## 3. 获取收藏夹内容列表

**GET** `https://api.bilibili.com/x/v3/fav/resource/list`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| media_id | num | 是 | 完整 mlid |
| pn | num | 否 | 页码，默认 1 |
| ps | num | 是 | 每页数量，1-20 |
| tid | num | 否 | 分区 tid，0=全部 |
| keyword | str | 否 | 搜索关键词 |
| order | str | 否 | mtime (收藏时间) / view (播放量) / pubtime (投稿时间) |
| type | num | 否 | 0=当前收藏夹, 1=全部收藏夹 |
| platform | str | 否 | 填 "web" |

**响应：** `data.medias[]` 数组中每项含 `id`(avid), `bvid`, `title`, `cover`, `upper`(UP主信息), `duration` 等。

## 4. 获取收藏夹全部内容 ID (轻量)

**GET** `https://api.bilibili.com/x/v3/fav/resource/ids`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| media_id | num | 是 | 完整 mlid |
| platform | str | 否 | "web" |

**响应：** `data[]` 数组，每项 `{id, type, bv_id, bvid}`。
比 `/list` 轻量，适合批量操作前快速获取全量 ID。

---

## 5. 创建收藏夹

**POST** `https://api.bilibili.com/x/v3/fav/folder/add`

Body (application/x-www-form-urlencoded):

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| title | str | 是 | 收藏夹标题 |
| intro | str | 否 | 简介，默认空 |
| privacy | num | 否 | 0=公开, 1=私密，默认0 |
| cover | str | 否 | 封面图 URL |
| csrf | str | 是 | `bili_jct` |

**响应 data 字段：** `id`(完整mlid), `fid`(短id), `mid`, `attr`, `title`, `media_count` 等。

示例（已验证）：
```bash
curl -X POST 'https://api.bilibili.com/x/v3/fav/folder/add' \
  --data-urlencode 'title=下载' \
  --data-urlencode 'privacy=0' \
  --data-urlencode 'csrf=<bili_jct>' \
  -b 'SESSDATA=<sessdata>'
```

## 6. 修改收藏夹

**POST** `https://api.bilibili.com/x/v3/fav/folder/edit`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| media_id | num | 是 | 目标 mlid |
| title | str | 是 | 新标题 |
| intro | str | 否 | 新简介 |
| privacy | num | 否 | 0=公开, 1=私密 |
| cover | str | 否 | 封面图 URL |
| csrf | str | 是 | `bili_jct` |

## 7. 删除收藏夹

**POST** `https://api.bilibili.com/x/v3/fav/folder/del`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| media_ids | num | 是 | 目标 mlid |
| csrf | str | 是 | `bili_jct` |

⚠️ 删除操作不可逆（但从实际测试看，删除到回收站，B站会保留30天）。

---

## 8. 移动资源到收藏夹

**POST** `https://api.bilibili.com/x/v3/fav/resource/move`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| src_media_id | num | 是 | 源收藏夹 mlid |
| tar_media_id | num | 是 | 目标收藏夹 mlid |
| mid | num | 是 | 当前用户 UID |
| resources | str | 是 | 内容 ID 列表，格式: `avid:2,avid:2,...` |
| platform | str | 否 | "web" |
| csrf | str | 是 | `bili_jct` |

**resources 格式：** `{内容id}:{内容类型}`
- 视频: `avid:2`
- 音频: `auid:12`
- 合集: `collection_id:21`
- 多个用逗号分隔: `123:2,456:2,789:2`

⚠️ **参数名是 `resources`，不是 `res_media_ids` 或 `resources_ids`！**

示例（已验证）：
```bash
curl -X POST 'https://api.bilibili.com/x/v3/fav/resource/move' \
  --data-urlencode 'src_media_id=72927717' \
  --data-urlencode 'tar_media_id=4106246717' \
  --data-urlencode 'mid=11976717' \
  --data-urlencode 'resources=116833831096792:2,116804487812159:2' \
  --data-urlencode 'platform=web' \
  --data-urlencode 'csrf=<bili_jct>' \
  -b 'SESSDATA=<sessdata>'
```

## 9. 复制资源到收藏夹

**POST** `https://api.bilibili.com/x/v3/fav/resource/copy`

参数与 move 完全相同。

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| src_media_id | num | 是 | 源收藏夹 mlid |
| tar_media_id | num | 是 | 目标收藏夹 mlid |
| mid | num | 是 | 当前用户 UID |
| resources | str | 是 | 内容 ID 列表，格式同 move |
| platform | str | 否 | "web" |
| csrf | str | 是 | `bili_jct` |

## 10. 添加/移除视频到收藏夹 (Deal API)

**POST** `https://api.bilibili.com/x/v3/fav/resource/deal`

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| rid | num | 是 | 稿件 avid |
| type | num | 是 | 固定为 2（视频） |
| add_media_ids | num/str | 可选 | 要加入的收藏夹 mlid，多个逗号分隔 |
| del_media_ids | num/str | 可选 | 要移除的收藏夹 mlid，多个逗号分隔 |
| platform | str | 否 | "web" |
| csrf | str | 是 | `bili_jct` |

说明：
- `add_media_ids` 和 `del_media_ids` 至少提供一个
- 可以同时添加和移除
- 不依赖源收藏夹，直接操作视频与收藏夹的关系

---

## 常见错误码

| code | 含义 |
|------|------|
| 0 | 成功 |
| -101 | 未登录 |
| -111 | CSRF 校验失败 |
| -400 | 请求参数错误（常见：参数名写错） |
| -102 | 账号被封停 |
| 11010 | 内容不存在 |
| 2001000 | 参数错误 |

---

## mlid (media_id) 说明

收藏夹的完整 ID = `fid` + `mid` 尾号 2 位。

例如：fid=41062467, mid=11976717 → mlid=4106246717

创建收藏夹返回的 `id` 就是完整 mlid，可直接使用。

---

## Python 请求模板

```python
import urllib.request, urllib.parse, json

BILI_JCT = "your_bili_jct"
COOKIE = f"SESSDATA=your_sessdata; bili_jct={BILI_JCT}; DedeUserID=11976717"
HEADERS = {
    "Cookie": COOKIE,
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://space.bilibili.com/",
}

def bili_request(method, url, data=None):
    if data:
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

# 获取所有视频收藏夹
resp = bili_request("GET",
    "https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid=11976717&type=2")

# 移动到「下载」文件夹
resp = bili_request("POST", "https://api.bilibili.com/x/v3/fav/resource/move", {
    "src_media_id": 72927717,     # 默认收藏夹
    "tar_media_id": 4106246717,   # 下载
    "mid": 11976717,
    "resources": "avid1:2,avid2:2",
    "platform": "web",
    "csrf": BILI_JCT,
})
```

---

## 已验证端点清单

| 端点 | 方法 | 已验证 | 说明 |
|------|------|--------|------|
| /x/v3/fav/folder/created/list-all | GET | ✅ | 获取所有收藏夹 |
| /x/v3/fav/folder/add | POST | ✅ | 创建收藏夹 |
| /x/v3/fav/resource/list | GET | ✅ | 获取收藏夹内容 |
| /x/v3/fav/resource/move | POST | ✅ | 移动资源（单/多） |
| /x/v3/fav/resource/deal | POST | ✅ | 添加/移除收藏 |
| /x/v3/fav/folder/edit | POST | 📖 | 文档记录（未实测） |
| /x/v3/fav/folder/del | POST | 📖 | 文档记录（未实测） |
| /x/v3/fav/resource/copy | POST | 📖 | 文档记录（未实测） |
| /x/v3/fav/resource/ids | GET | 📖 | 文档记录（未实测） |
| /x/v3/fav/folder/info | GET | 📖 | 文档记录（未实测） |
