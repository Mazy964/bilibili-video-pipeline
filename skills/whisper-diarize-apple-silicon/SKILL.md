---
name: whisper-diarize-apple-silicon
description: "End-to-end B站 video pipeline on Apple Silicon: subtitle extraction via B站 API with Safari cookies, CC-subtitle fast path, local ASR with faster-whisper BatchedInferencePipeline, pyannote MPS diarization, LLM summarization, Obsidian export. ~10 min for 37 min audio."
category: mlops
---

# B站视频处理流水线 (Apple Silicon)

## 决策树

```
B站视频 URL
 ├─ 1. 先搜 API 字幕（view API → CC字幕）
 │   ├─ 有 CC 字幕 → 下载 JSON → 纯文本（<1秒）
 │   └─ 无字幕 → 进入步骤2
 ├─ 2. 下载音频（yt-dlp+cookie → curl → aria2c 降级）
 └─ 3. 本地 ASR
     ├─ faster-whisper BatchedInferencePipeline (batch_size=16, float32)
     ├─ pyannote MPS diarization（可选）
     └─ LLM 总结 → Obsidian
```

**并行辅助**：下载音频的同时可 web_search "UP主 + 标题关键词 + 总结" 拿第三方文字版作为**参考补充**（快科技、腾讯新闻等）。但 ⚠️ **第三方文字版可能被编辑加工/夹带私货，不能替代原视频的原文转录**。web 版仅用于交叉验证和补充背景，音频转录始终是必须的。

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

可复用脚本：`scripts/transcribe-summarize.py` —— 转录 + DeepSeek 总结，一键完成。

```bash
python3 scripts/transcribe-summarize.py ~/videos/xxx/audio.m4a "视频标题"
# → transcript_raw.txt + transcript.md
```

## 第六步：VLM 帧分析（🆕 视觉理解）

纯音频转录抓解说词，但**跑分图表、功耗曲线、对比柱状图**只在画面里。VLM 给流水线加"眼睛"——读屏幕上的数字比听一遍再转录准确。

### 模型选择（M1 Max 32GB 实测）

| 模型 | 参数 | M1可用 | 推荐 |
|------|------|--------|------|
| **Penguin-VL-2B** | 2B | ✅ bfloat16 4GB | 🥇 本地首选 |
| Penguin-VL-8B | 8B | ✅ | 🥈 |
| Kimi-VL-A3B | 16B/3B激活 | ❌ OOM 30.6GiB | 需API |
| Keye-VL-2.0 | 30B/3B激活 | ❌ | 云 |

**Penguin-VL-2B** 是腾讯开源的轻量级 VLM：
- LLM 初始化的视觉编码器（非 CLIP/SigLIP）→ 对 OCR/图表更敏感
- 时间冗余感知压缩 → 长视频帧预算优化
- 纯 transformers 加载，无需 vLLM

```python
from transformers import AutoModelForCausalLM, AutoProcessor
model = AutoModelForCausalLM.from_pretrained(
    "tencent/Penguin-VL-2B", torch_dtype=torch.bfloat16,
    device_map="mps", trust_remote_code=True)
```

> Kimi-VL-A3B 虽然 MoE 只激活 3B，但总参数 16B，bfloat16 需 30.6GiB → M1 Max 32GB OOM。CPU 卸载可加载但推理极慢。
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
8. PyYAML 版本兼容：`yaml.safe_load(stream)` vs `yaml.safe_load(open(path))` — 始终用后者（传文件对象而非字符串）
9. ⚠️ 第三方文字版（快科技、腾讯新闻等）= 编辑二次加工，可能夹带私货/选择性引用/数据失真。只能做参考补充和交叉验证，不能替代原视频的完整原文转录。音频下载+ASR 始终是必须的主路径。

### 辅助参考：web_search 第三方文字版

下载音频的同时可并行 web_search 拿第三方文字版作为**补充参考**：
```
web_search("UP主名 + 视频标题 + 总结")
→ 快科技(mydrivers)、腾讯新闻、电玩帮 等常有时序文字版
```

⚠️ **第三方文字版 = 编辑加工产物，可能夹带私货/选择性引用/遗漏关键数据。** 仅用于交叉验证和补充背景信息，不能替代原视频的完整原文转录。音频下载 + ASR 始终是必须的主路径。

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
