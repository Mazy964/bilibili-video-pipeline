---
name: whisper-diarize-apple-silicon
description: "End-to-end B站 video pipeline on Apple Silicon: subtitle extraction via B站 API with Safari cookies, CC-subtitle fast path, local ASR with faster-whisper BatchedInferencePipeline, pyannote MPS diarization, LLM summarization, Obsidian export. ~10 min for 37 min audio."
category: mlops
---

# B站视频处理流水线 (Apple Silicon)

## 决策树

```
B站视频 URL
 ├─ 1. API 字幕（view API → CC字幕）              ← 秒级免费，优先
 │   ├─ 有字幕 → JSON → 纯文本（<1秒）→ 跳至步骤5
 │   └─ 无字幕 → 步骤2+并行web_search
 ├─ 2. 下载（并行：音频 + web_search第三方文字版）  ← 下载同时搜快科技/腾讯新闻
 │   ├─ 音频：yt-dlp+cookie → curl → aria2c 降级
 │   └─ web_search 参考：第三方文字版（仅交叉验证）
 ├─ 3. 本地 ASR                                  ← faster-whisper BatchedInferencePipeline
 │   └─ large-v3, float32, batch_size=16
 ├─ 4. 说话人分离（可选）                          ← pyannote MPS
 ├─ 5. LLM 精炼 → Obsidian                        ← DeepSeek 总结 + 写笔记
 └─ 6. VLM 帧分析（评测/教程类）                   ← Qwen2.5-VL-7B 读图表
     ├─ ffmpeg 抽帧（fps=1/10）
     └─ 批量推理 → JSON {数字, 图表类型}
```

**并行策略**：步骤2中音频下载和 web_search 同时启动。热门视频文字版秒出 → 可取消下载；长尾视频走完整 ASR 路径。

⚠️ **第三方文字版 = 编辑加工，可能夹带私货。** 仅用于交叉验证，音频转录始终是主路径。

## 第一步：获取字幕（Safari Cookie）

```python
import browser_cookie3, subprocess, json

# B站 Safari 登录态（需先在 Safari 登录 bilibili.com）
cj = browser_cookie3.safari(domain_name='bilibili.com')
cookie_str = '; '.join(f'{c.name}={c.value}' for c in cj)

# 1. 获取 aid + cid + CC字幕
r = subprocess.run(['curl', '-s', f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'], 
                   capture_output=True, text=True)
d = json.loads(r.stdout)['data']
aid, cid = d['aid'], d['cid']
cc_subs = d.get('subtitle', {}).get('list', [])

# 2. 获取 AI 字幕（需要 cookie + aid + cid + wbi/v2）
r2 = subprocess.run(['curl', '-s', '-H', f'Cookie: {cookie_str}',
    f'https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}'],
    capture_output=True, text=True)
ai_subs = json.loads(r2.stdout)['data']['subtitle']['subtitles']
```

## 第二步：下载音频（无字幕时）

**按顺序降级尝试：**

### ① yt-dlp + Safari Cookie（首选，解决 412 认证）
```bash
# 先导出 Safari cookie
python3 -c "
import browser_cookie3, os
cj = browser_cookie3.safari(domain_name='bilibili.com')
with open(os.path.expanduser('~/.hermes/bilibili_cookies_netscape.txt'),'w') as f:
    f.write('# Netscape HTTP Cookie File\n')
    for c in cj:
        if 'bilibili' in (c.domain or '') or 'bilivideo' in (c.domain or ''):
            dom = c.domain if c.domain.startswith('.') else '.'+c.domain
            f.write(f'{dom}\tTRUE\t{c.path}\t{\"TRUE\" if c.secure else \"FALSE\"}\t{c.expires or 0}\t{c.name}\t{c.value}\n')
"

yt-dlp --cookies ~/.hermes/bilibili_cookies_netscape.txt \
  -f 30280 --no-playlist -o ~/videos/.../audio.m4a \
  https://www.bilibili.com/video/BV...
```
格式 `30280` 是 ~178kbps m4a 音频（`-F` 可查所有格式）。

### ② B站 API + curl（备选，SSL兼容性好）
```python
play = json.loads(subprocess.run(['curl', '-s',
    f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=80&fourk=1'],
    capture_output=True, text=True).stdout)['data']
# ⚠️ 优先选最低码率（CDN对低码率分配快节点，转录质量完全够用）
audio = sorted(play['dash']['audio'], key=lambda x: x['bandwidth'])[0]['base_url']
subprocess.run(['curl', '-L', '--retry', '3',
    '-H', 'Referer: https://www.bilibili.com',
    '-o', f'{outdir}/audio.m4a', audio_url])
```

### ③ aria2c（多路并行，低码率首选）
```bash
# 低码率97kbps → 11MiB/s实测，32MB→2秒
aria2c -x16 -s16 -k1M --referer=https://www.bilibili.com -o audio.m4a <URL>
```

### CDN速度实测（2026-07）

B站CDN (`upos-sz-mirrorcosov.bilivideo.com`) 速度因码率/时间段差异巨大：

| 码率 | 大小 | 速度 | 耗时 |
|------|------|------|------|
| 97kbps | ~12MB | **11MiB/s** | ~2s |
| 177kbps | ~32MB | **288KiB/s** | ~2min |

**策略**：先试最高码率 `aria2c -x16 --allow-overwrite`。若 >2min 无进展，**立刻切最低码率** — 97kbps AAC 对语音转录完全够用。yt-dlp 无 cookie → 412；带 Safari cookie 可过。

## 第三步：本地 ASR (BatchedInferencePipeline)

```python
from faster_whisper import WhisperModel, BatchedInferencePipeline
model = WhisperModel("large-v3", device="cpu", compute_type="float32")
batched = BatchedInferencePipeline(model=model)
segments, info = batched.transcribe("audio.m4a", language="zh", beam_size=5, batch_size=16)
```

## 第四步：说话人分离（可选）

```python
pipe = Pipeline.from_pretrained(model_path, token="HF_TOKEN").to(torch.device("mps"))
output = pipe({"waveform": wf, "sample_rate": 16000})
try:
    next(iter(output))
except StopIteration as e:
    diar = e.value.speaker_diarization  # pyannote 4.x magic!
turns = [(float(t.start), float(t.end), str(s)) for t, _, s in diar.itertracks(yield_label=True)]
```

## 第五步：LLM 总结 → Obsidian

可复用脚本：
- `scripts/transcribe-summarize.py` —— 转录 + DeepSeek 总结，一键完成
- `scripts/transcript-to-obsidian.py` —— 已有转录文件 → LLM 精炼 → Obsidian 笔记（支持单集/整系列）

```bash
python3 scripts/transcribe-summarize.py ~/videos/xxx/audio.m4a "视频标题"
# → transcript_raw.txt + transcript.md
```

## 第六步：VLM 帧分析（🆕 视觉理解）

纯音频转录抓解说词，但**跑分图表、功耗曲线、对比柱状图**只在画面里。VLM 给流水线加"眼睛"——读屏幕上的数字比听一遍再转录准确。

### 模型选择（M1 Max 32GB 实测）

| 模型 | 参数 | M1 实测 | 推荐 |
|------|------|---------|------|
| **Qwen2.5-VL-7B** | 8.3B | ✅ bfloat16 MPS, ~14GB | 🥇 本地首选 |
| Qwen2.5-VL-3B | 3B | ✅ | 🥈 轻量备选 |
| Penguin-VL-2B | 2B | ❌ 缺视觉编码器权重 | 不可用 |
| Kimi-VL-A3B | 16B/3B激活 | ❌ OOM 30.6GiB | 需API |

**Qwen2.5-VL-7B** 阿里通义千问多模态版，M1 Max 实测：
- 加载 17 秒（5 个 shard），推理 <10 秒/帧
- 正确识别芯片架构图（NPU/ISP/GPU 频率、晶体管对比表）
- 官方支持，无需 `trust_remote_code`，生态成熟

```python
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    "Qwen/Qwen2.5-VL-7B-Instruct", torch_dtype=torch.bfloat16,
    device_map="mps", trust_remote_code=True)
processor = AutoProcessor.from_pretrained("Qwen/Qwen2.5-VL-7B-Instruct")
# pip install qwen-vl-utils
```

> Kimi-VL-A3B 虽然 MoE 只激活 3B，但总参数 16B，bfloat16 需 30.6GiB → M1 Max 32GB OOM。Penguin-VL-2B 缺少 `Penguin-Encoder` 权重下载失败。
> 详见 `references/vlm-frame-analysis.md`

## 转录后端

| 后端 | 速度 | 成本 | |
|------|------|------|------|
| faster-whisper (本地) | 0.3x实时 | 免费 | ✅ 默认 |
| Groq Whisper (云) | 228x实时 | $0.04/h | ❌ 不集成 |

## 关键 API

| 端点 | 参数 | 说明 |
|------|------|------|
| `x/web-interface/view` | bvid | CC字幕 + aid/cid |
| `x/player/wbi/v2` | aid, cid | AI字幕（需要cookie） |
| `x/player/playurl` | bvid, cid, fnval=80 | 音频流地址 |

## 扩展：B站收藏夹管理

详见 `references/bilibili-fav-api.md` — 收藏夹列表、视频内容、批量操作、删除/移动 API。

脚本：`scripts/fav-monitor.py`, `scripts/fav-classify.py`, `scripts/fav-deadlink.py`, `scripts/fav-report.py`

## 避坑

1. yt-dlp 412 → 用 Safari cookie（`browser_cookie3.safari` → Netscape格式）
2. aria2c SSL 握手失败 → 降级到 curl 下载
3. B站CDN慢（~16KiB/s）→ 先试 yt-dlp + cookie，不行降级到最低码率（97kbps 语音够用），不要放弃下载切 web 版
4. player/v2 无 AI 字幕 → 用 player/wbi/v2 + aid+cid
5. B站 AI 字幕质量差 → 优先本地 large-v3 ASR
6. pyannote 4.x generator → StopIteration.value.speaker_diarization
7. PyYAML 版本兼容：`yaml.safe_load(stream)` vs `yaml.safe_load(open(path))` — 始终用后者（传文件对象而非字符串）
8. ⚠️ 第三方文字版（快科技、腾讯新闻等）= 编辑二次加工，可能夹带私货/选择性引用/数据失真。只能做参考补充和交叉验证，不能替代原视频的完整原文转录。音频下载+ASR 始终是必须的主路径。

## 多 Agent 自动流水线（Kanban）

拆成 transcriber / obsidian-writer / reviewer 三个 profile，Kanban 看板自动级联执行。你只需说一句"处理这个视频"。

详见 `references/kanban-multi-agent.md`

## 性能 (M1 Max)

| 步骤 | 时间 |
|------|------|
| API 字幕 | <1s |
| 音频下载 | ~15s |
| ASR | ~8min |
| Diarization | ~79s |
| LLM 总结 | ~1min |
