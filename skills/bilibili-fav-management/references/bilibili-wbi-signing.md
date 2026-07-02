# B站 WBI 签名参考

## 密钥获取

```python
import json, urllib.request, ssl

SSL_CTX = ssl.create_default_context()
headers = {
    "User-Agent": "Mozilla/5.0 ...",
    "Referer": "https://www.bilibili.com/",
    "Cookie": "..."
}

req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=headers)
with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
    nav = json.loads(resp.read())

img_url = nav["data"]["wbi_img"]["img_url"]
sub_url = nav["data"]["wbi_img"]["sub_url"]
img_key = img_url.rsplit("/", 1)[1].split(".")[0]  # 32 chars
sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]  # 32 chars
```

> 密钥有效期 ~30 分钟，建议缓存 + 过期刷新。

## 签名算法（正确 vs 错误）

```python
MIXIN_ENC_TAB = [
    46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,
    27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
    37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
    22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52
]

# ✅ 正确：img_key + sub_key 拼接后再做 mixin
mixin = ''.join((img_key + sub_key)[i] for i in MIXIN_ENC_TAB)[:32]

# ❌ 错误1：只对 img_key 做 mixin（sub_key 未参与）
mixin = ''.join(img_key[i] for i in MIXIN_ENC_TAB)[:32]

# ❌ 错误2：分别做 mixin 再拼接后截断
mixin = ''.join(img_key[i] for i in MIXIN_ENC_TAB)[:32] + ''.join(sub_key[i] for i in MIXIN_ENC_TAB)[:32]
mixin = mixin[:32]  # 结果等于只用 img_key
```

## 请求签名

```python
import hashlib, urllib.parse, time

params = {"bvid": bvid, "wts": round(time.time())}
params = dict(sorted(params.items()))
clean = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
query = urllib.parse.urlencode(clean)
w_rid = hashlib.md5((query + mixin).encode()).hexdigest()

clean["w_rid"] = w_rid
url = f"https://api.bilibili.com/x/web-interface/view?{urllib.parse.urlencode(clean)}"
```

## 多线程注意

- **mixin** 在主线程预计算（只读共享，线程安全）
- **headers**（含 Cookie）每个线程独立创建 → 避免 Cookie header 被并发修改
- 不要用共享的全局 HEADERS dict

```python
# 主线程
MIXIN = ...  # 一次性计算
COOKIE = ... # 一次性加载

# 子线程
def fetch_one(bvid):
    headers = {  # 每个线程独立
        "User-Agent": "...",
        "Referer": "...",
        "Cookie": COOKIE,
    }
    # ... 使用 MIXIN + headers 发起请求 ...
```
