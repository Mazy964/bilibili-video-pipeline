# B站分类批量执行参考

## 实测数据（2026-07-02）

| 指标 | 值 |
|---|---|
| 分类源文件 | `cache/fav-classify-final.json` |
| 总视频数 | 7,200（48 个目标文件夹） |
| 成功移动 | 6,390 |
| 失败 | 0 |
| 跳过（已失效） | 790 |
| 耗时 | 27.5 分钟 |
| 默认夹 | 7,577 → 1,188 |

## 分流细节

| 文件夹 | 操作 |
|---|---|
| 搞笑(+1463) | 982→搞笑（满999）+ 481→搞笑(2) |
| 编程/硬件/DIY(+267) | 全部→编程/硬件/DIY(2) |
| 🗑️待删除(790) | 全部跳过（视频已删/私密） |
| 其余 45 个文件夹 | 单批直移 |

## 溢出夹创建

```bash
# 在执行分类移动前创建
python3 scripts/fav-manage.py create "搞笑(2)"
python3 scripts/fav-manage.py create "编程/硬件/DIY(2)"
python3 scripts/fav-manage.py create "放弃区"
```

## 并行转换性能

982 个视频 BVID→avid：
- 串行：~100s（1 req/100ms）
- 8线程并行：~12s（8 req/100ms）

ThreadPoolExecutor 设置：
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

lock = threading.Lock()
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(bvid_to_aid, bvid): bvid for bvid in bvids}
    for f in as_completed(futures):
        result = f.result()
```

或更简洁的 map：
```python
with ThreadPoolExecutor(max_workers=8) as ex:
    results = list(ex.map(bvid_to_aid, bvids))
```
