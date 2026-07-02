---
name: bilibili-fav-management
description: "Manage B站 favorites: scan, classify, online folder CRUD, merge, batch move, detect dead links, auto-download to video pipeline, weekly reports. Use when the user wants to organize their B站收藏夹, create/delete/merge folders, batch move videos via API, auto-download favorited videos, or audit their favorites."
version: 1.3.0
---

# B站收藏夹管理

全自动收藏夹管理系统：本地分析 + 在线操作（新建/删除/合并/批量移动）+ Kanban 流水线集成。

## 快速开始

```bash
# 本地分析
python3 scripts/fav-monitor.py                    # 扫描"下载"夹，新视频投喂 Kanban
python3 scripts/fav-deadlink.py 72927717          # 检测默认夹失效链接
python3 scripts/fav-report.py                     # 生成周报

# 在线管理
python3 scripts/fav-manage.py list-folders        # 查看所有文件夹
python3 scripts/fav-manage.py create "新文件夹"    # 新建
python3 scripts/fav-manage.py delete mlid          # 删除
python3 scripts/fav-manage.py merge --src --dst    # 合并
python3 scripts/fav-manage.py classify --file cache/fav-classify-final.json --dry-run  # 全量干跑
```

## 脚本清单

| 脚本 | 功能 |
|------|------|
| `fav-manage.py` | **在线管理**：新建/删除/合并文件夹，批量移动，基于分类JSON全量执行 |
| `fav-classify-execute.py` | **全量并行执行**：8线程 BVID→avid 转换 + 溢出分流，实测 6390/27.5min |
| `fav-clean-uppers.py` | **UP主批量清理 v3**：keyword 全局搜索 → 定位 → 逐视频 deal（或临时夹策略） |
| `fav-monitor.py` | 扫描指定收藏夹 → 检测新增 → Kanban 投喂 |
| `fav-classify.py` | 默认夹视频 → DeepSeek 语义分类（已被预处理替代90%） |
| `fav-deadlink.py` | 扫描指定收藏夹 → 检测删除/私有视频 |
| `fav-report.py` | 综合三源数据 → 生成周报 |
| `fav-enrich-partition.py` | 批量拉取视频分区 tid（多线程 WBI 签名） |
| `fav-preprocess-v2.py` | 本地预处理：分区优先 → 关键词 → UP主 |
| `tid_map.py` | tid→分区名→文件夹映射模块（116个分区） |

## 在线文件夹管理（fav-manage.py）

B站账号内收藏夹操作，全部通过 API 在线执行。

```bash
# 基本 CRUD
python3 scripts/fav-manage.py create "新文件夹名"
python3 scripts/fav-manage.py delete mlid
python3 scripts/fav-manage.py list-folders

# 批量移动
python3 scripts/fav-manage.py move --src mlid --dst mlid --bvids BV1xx,BV2yy

# 合并（移动全部 + 删除源夹）
python3 scripts/fav-manage.py merge --src mlid --dst mlid

# 全量分类执行（先 --dry-run！）
python3 scripts/fav-manage.py classify --file cache/fav-classify-final.json --dry-run
python3 scripts/fav-manage.py classify --file cache/fav-classify-final.json
```

### 溢出处理模式

B站每文件夹上限 999。当分类计划显示 `当前 + 新增 > 999`：

1. **创建溢出分夹**：`"原夹名(2)"`（如 搞笑(2)、编程/硬件/DIY(2)）
2. **分流策略**：原夹填满到 999，余量进溢出夹
3. **脚本化**：`fav-classify-execute.py` 内置 `OVERFLOW` 映射表

已预置：`搞笑 → 搞笑(2)`，`编程/硬件/DIY → 编程/硬件/DIY(2)`。

### 并行 BVID→avid 转换

大规模移动的核心瓶颈是逐视频 `view` API 获取 avid。8线程并发（`ThreadPoolExecutor`）可将 982 个视频的转换从 ~100s 降至 ~12s。

```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(bvid_to_aid, bvids))  # bvid_to_aid 内部调 view API
```

串行模式仅适用于 <20 个视频的小批量测试。

### 合并文件夹流程

1. 获取源夹全部 BVID → 转 `avid:2` 格式
2. 检查目标夹容量（≤999上限）→ 批量 `POST resource/move`
3. 移动完成后 `POST folder/del`（`media_ids` 参数）删除源夹
4. 若目标夹已满 → 跳过并报错

### 全量分类执行（classify 命令）

读取 `fav-classify-final.json`，按 `suggested_folder` 分组：
1. 遍历目标文件夹，API 查 `media_count`，检查 `当前 + 新增 > 999`
2. 超限文件夹跳过并报告
3. 其余逐批调用 `resource/move`（每批后 sleep 2s 防限流）

**⚠️ 必须先 `--dry-run` 确认计划**——移动不可逆。

## 关键 API 端点

| 操作 | 端点 | 方法 | 认证 |
|------|------|------|------|
| 获取文件夹列表 | `fav/folder/created/list-all?up_mid=` | GET | Cookie |
| 获取文件夹信息 | `fav/folder/info?media_id=` | GET | Cookie |
| 获取文件夹内容ID | `fav/resource/ids?media_id=` | GET | Cookie |
| 获取文件夹内容 | `fav/resource/list?media_id=` | GET | Cookie + WBI |
| **全局搜索收藏** | `fav/resource/list?keyword=X&type=1` | GET | Cookie |
| 获取视频详情 | `web-interface/view?bvid=` | GET | 无 |
| **新建文件夹** | `fav/folder/add` | POST | Cookie + CSRF |
| **删除文件夹** | `fav/folder/del` | POST | Cookie + CSRF |
| 删除单个收藏 | `fav/resource/deal` | POST | Cookie + CSRF |
| ~~批量删除~~ | ~~`fav/resource/batch-del`~~ | ~~POST~~ | ~~-400，不可用~~ |
| **移动收藏** | `fav/resource/move` | POST | Cookie + CSRF |
| **清除失效内容** | `fav/resource/clean` | POST | Cookie + CSRF |

### 移动（resource/move）正确格式

```python
# ✅ 正确：resources 用 avid:2 格式，必须传 mid
form = {
    "src_media_id": "72927717",
    "tar_media_id": "3654805517",
    "resources": "115825101047931:2,115825101047932:2",  # avid:2，逗号分隔
    "mid": "11976717",       # ⚠️ 必须！用户 UID
    "platform": "web",
    "csrf": csrf,
}
# POST x-www-form-urlencoded → api.bilibili.com/x/v3/fav/resource/move
```

> `data:0` 是正常成功响应，不是 0 个移动。move 成功后永远返回 `{code:0, message:"OK", data:0}`。

### 新建文件夹（folder/add）

```python
POST "https://api.bilibili.com/x/v3/fav/folder/add"
form = {"title": "文件夹名", "intro": "", "privacy": "0", "cover": "", "csrf": csrf}
# 成功返回 {"code":0, "data": {"id": mlid}}  — id 即 mlid
```

### 删除文件夹（folder/del）

```python
POST "https://api.bilibili.com/x/v3/fav/folder/del"
form = {"media_ids": str(mlid), "csrf": csrf}
# ⚠️ 参数名是 media_ids（复数），值是 mlid
```

### 删除单个收藏（resource/deal）

```python
POST "https://api.bilibili.com/x/v3/fav/resource/deal"
form = {
    "rid": str(avid),         # ⚠️ avid数字，不是 BVID
    "type": "2",              # 2=视频稿件
    "add_media_ids": "",      # 添加目标夹（留空=仅删除）
    "del_media_ids": str(mid), # 从哪个夹删除
    "platform": "web",
    "csrf": csrf,
}
# 成功: {code:0, message:"OK"}
# 不需要 WBI 签名
```

> `batch-del` 实测始终返回 -400（请求格式错误），不可用。改用 `deal` 逐视频删除。

### UP主批量清理

```bash
python3 scripts/fav-clean-uppers.py                 # 内置 22 UP主名单
python3 scripts/fav-clean-uppers.py UP主1 UP主2 ... # 指定名单
```

**v3 流程（keyword 全局搜索法）**：

1. **全局搜索**：`resource/list?keyword=XX&type=1&tid=0` 一次性搜出全夹该 UP主 的视频
   - 每个结果自带 `id`（即 avid）、`bvid`、`upper.name`
   - ⚠️ `medias` 无结果时是 `None`（非 `[]`），必须 `data.get("medias") or []`
   - `type=1` = 跨全部收藏夹搜索，突破单个夹 1000 条限制
2. **精确匹配**：对比 `upper.name` 确认归属（keyword 会模糊匹配，如 "TESTV" 命中 "TESTV官方频道"）
3. **逐视频解除收藏**：`resource/deal` API，`del_media_ids` 填默认夹 mlid 即可（B站自动定位）
   - 间隔 1.5s，避免 412 限流
4. **去重**：同一 UP主 关键词可能重叠（如 "老番茄" 和 "番茄"），按 bvid 去重

**临时夹策略（批量移动→整夹删除）**：适用于需要一次删除大量视频的场景。比逐个 `deal` 快 100 倍且不触发限流：

```
1. 创建临时文件夹 ("🧹待清理")
2. 将所有目标视频 move 到临时夹（按源夹分批，一次 API 调用每批）
3. folder/del 删除临时夹 → 夹内视频自动解除收藏
```

> 已确认：删除收藏夹时，夹内视频**自动从全部收藏中移除**，不是移到默认夹。

**旧版 v1/v2**（已弃用）：分类缓存查 `upper` 字段 → 74 夹全扫描定位 → deal API。缺点：缓存只覆盖初始 7279 个视频，不包含新增或缓存在 1000 条之外的视频。

### 限流与重试

- **`deal` API**：极容易触发 412（Precondition Failed），必须间隔 3-5 秒
- **`view` API**：4-6线程安全，超过 8线程易触发限流
- **`resource/move`**：2秒间隔安全
- **重试策略**：捕获 412/HTTPError → 等 5 秒 → 最多 3 次

```python
for attempt in range(3):
    try:
        # ... deal API call
        break
    except HTTPError as e:
        if attempt < 2: time.sleep(5)  # 递增退避
        else: raise
```

### 清除失效内容（resource/clean）

B站原生接口，一键清除指定文件夹内所有失效/删除/私有化的视频，无需逐视频扫描。

```python
POST "https://api.bilibili.com/x/v3/fav/resource/clean"
form = {"media_id": str(mlid), "csrf": csrf}
# 返回 {code:0, message:"OK", data:0}  — data:0 表示操作完成（非清除数）
# 若文件夹无失效内容，同样返回 code:0, data:0
```

> 注意：此 API 只清除**已从 B站消失**的视频（删除/私有/下架），不清除正常可播放的短视频。

## 三个 Cron

| Cron | 频率 | 功能 |
|------|------|------|
| `📥 下载夹监控` | 每3天 03:00 | 扫描"下载"夹 → 新视频投 Kanban 流水线 |
| `📊 收藏夹周报` | 每周一 08:00 | 生成收藏夹周报 |
| `🗑️ 死链扫描` | 每月1日 02:00 | 检测失效链接 |

## 本地预处理（分区优先，89%覆盖率）

### 核心策略

关键字/UP主规则只能覆盖 ~11%。真正利器是 **B站分区（tid）**——收藏夹 API 不返回，需逐视频拉 `x/web-interface/view`，通过手工映射表 → 用户文件夹。

**实测效果**（7279 个视频）：

| 方法 | 数量 | 占比 |
|---|---|---|
| 分区匹配 | 6,101 | 83.8% |
| LLM 已有 | 368 | 5.1% |
| 关键词/UP主 | 12 | 0.2% |
| **合计** | **6,481** | **89.0%** |

剩余 ~800 个主要是已删/私密视频。

### 多线程分区拉取（5 workers，62 min / 7279 个）

```python
# 关键：WBI 密钥在主线程预计算，避免 cache 竞争
MIXIN = ''.join((img_key + sub_key)[i] for i in MIXIN_ENC_TAB)[:32]
# 子线程只读 MIXIN + 独立 headers → 无竞争
```

速率：5 workers × ~0.4 req/s ≈ 2 req/s，API 限流不明显。

### 分区映射

映射表：`references/bilibili-partition-tid-map.md`（116 个二级分区 + 完整文件夹映射）。

Python 模块：`scripts/tid_map.py`，`get_folder_for_tid(tid) → (folder, confidence)`。

## 分类质量与修正

### 分区映射的局限性

B站分区体系（20+大分区）与个人文件夹（49个）语义差异显著：
- B站「搞笑」→ 涵盖段子/讽刺/整活/电影片段，但用户只想放纯粹搞笑内容
- B站「学习」→ 多数是学习类内容，但会混入生活vlog、西北刀客、小人国动画
- B站「生活」→ 可能命中政治评论、段子、日常
- B站「汽车」→ 可能命中非汽车类闲聊

**84%靠分区映射，准确率不够**。混合区（搞笑、学习、思考、编程/硬件/DIY）尤甚。

### 用户对具体夹的语义订正

- **AC** = Assassin's Creed 游戏系列，不是 ACG → 应并入「游戏」
- **漫 + 番** → 合并为「动漫番剧」（漫画/番剧及解说）
- **电影解说** → 并入「电影」（不单独建夹）
- **鬼畜** → 独立，不从属于搞笑
- **搞笑** → 低优先级，现有分类暂不调整

### 重分类策略（LLM复审）

当用户反馈某夹内容不准时：

1. 拉取该夹全部视频（标题 + BVID）
2. 用 DeepSeek 批量复审（每批50-100个，提示词列出所有可用文件夹名）
3. LLM 输出 `{bvid: suggested_folder, reason: str}` 格式
4. 按源夹分组 → 转 avid → 批量 move 到新位置
5. 费用估计：1680 个 × 0.5分/个 ≈ 几块钱

```
1. fav-classify.py --fetch-only     → 拉取全部视频列表 → cache/fav-videos.json
2. fav-enrich-partition.py          → 逐视频拉分区 tid → cache/fav-videos-enriched.json
3. fav-preprocess-v2.py             → 分区 + 关键词 + UP主 → cache/fav-preprocess.json
4. 手工收尾                          → 已删视频标记为"🗑️待删除"
5. 合并 → cache/fav-classify-final.json（100%覆盖）
6. 创建溢出夹（搞笑(2)、编程/硬件/DIY(2) 等）
7. fav-classify-execute.py          → 全量并行移动（含溢出分流）
8. 验证：fav-manage.py list-folders
```

详见：`references/bilibili-classify-execution.md`

## 避坑

1. **移动 API `resources` 用 `avid:2` 格式**，不是 BVID、不是 `res_media_ids`、不是纯数字。必须传 `mid`。成功响应 `data:0` 是正常的
2. **删除文件夹 API `folder/del`** 参数名是 `media_ids`（复数）
3. **每文件夹上限 999 个视频**：合并前必须检查容量，目标已满则跳过
4. **在线操作前过滤失效视频**：`view` API 返回 -404 的不在收藏夹中，move 会静默跳过
5. `resource/ids` 单次最多返回 **1000 条**（不是全部）。大夹（>1000）需用 `resource/list?keyword=X&type=1` 全局搜索突破
6. `resource/list` 搜索结果空时 `medias` 字段是 **`None`**（非 `[]`），必须 `data.get("medias") or []`
7. CSV token = Cookie 中的 `bili_jct`
8. WBI 签名：必须 `''.join((img_key + sub_key)[i] for i in MIXIN_ENC_TAB)[:32]`
9. `view` API 的 `tname` 总是空，只能用 `tid` + 手工映射
10. 多线程 WBI：主线程预计算 mixin，子线程只读 + 独立 headers
11. 已删视频标题被 B站替换为"已失效视频"
12. 全量 classify 前**必须先 `--dry-run`**，移动不可逆
13. 每批 move 后 sleep 2s 防限流
14. `batch-del` API 不可用（始终返回 -400），删除单个视频用 `resource/deal`
15. `deal` API 限流严格（412），串行间隔 **1.5s**。批量场景优先用**临时夹策略**（move→del folder），一次搞定不触发限流
16. `resource/clean` 返回 `data:0` = 操作完成（即使无可清除内容），不是清除数量
17. 大规模执行（>1000 视频）必须用并行 conversion（8线程），串行会慢 8 倍
18. 溢出夹必须在移动前创建好，做进 `OVERFLOW` 映射表
19. **批量删除首选临时夹策略**：建临时夹 → 移入全部目标视频 → 删除临时夹。绕开 76 次 deal 调用的限流，仅需 2 次 API（move + del folder）
20. **全局搜索优于缓存**：`resource/list?keyword=X&type=1` 搜全夹，覆盖缓存未收录的新增/大夹溢出视频
