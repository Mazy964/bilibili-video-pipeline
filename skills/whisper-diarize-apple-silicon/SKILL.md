---
name: whisper-diarize-apple-silicon
description: "End-to-end B站 video pipeline on Apple Silicon: subtitle extraction via B站 API with Safari cookies, CC-subtitle fast path, local ASR with faster-whisper BatchedInferencePipeline, pyannote MPS diarization, LLM summarization, Obsidian export. ~10 min for 37 min audio."
category: mlops
---

# B站视频处理流水线 (Apple Silicon)

## 决策树

```
B站视频 URL
 ├─ 1. 先搜 API 字幕（view API → player/wbi/v2 + aid+cid + Safari cookie）
 │   ├─ 有 CC 字幕 → 直接下载 JSON → 纯文本输出（<1秒，质量好）
 │   ├─ 只有 AI 字幕 → 质量差（碎片化/噪音），不推荐
 │   └─ 无字幕 → 进入本地 ASR
 └─ 2. 本地 ASR
     ├─ 下载音频（B站 API + aria2c，不用 yt-dlp cookie）
     ├─ faster-whisper BatchedInferencePipeline (batch_size=16, float32)
     ├─ pyannote MPS diarization（可选，79s for 37min）
     └─ LLM 总结
```

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

```python
play = json.loads(subprocess.run(['curl', '-s',
    f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=80&fourk=1'],
    capture_output=True, text=True).stdout)['data']
audio_url = sorted(play['dash']['audio'], key=lambda x: x['bandwidth'], reverse=True)[0]['base_url']
subprocess.run(['aria2c', '-x4', '-s4', '--referer=https://www.bilibili.com',
    '-d', outdir, '-o', 'audio.m4a', audio_url])
```

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

脚本：`~/.hermes/scripts/transcript-to-obsidian.py`
- DeepSeek API 精炼 → 结构化 Obsidian 笔记
- 核心概念 / 内容精要 / 关键引用 / 术语词条
- 输出到 iCloud Obsidian vault

## 关键 API

| 端点 | 参数 | 说明 |
|------|------|------|
| `x/web-interface/view` | bvid | CC字幕 + aid/cid |
| `x/player/wbi/v2` | aid, cid | AI字幕（需要cookie） |
| `x/player/playurl` | bvid, cid, fnval=80 | 音频流地址 |

## 避坑

1. yt-dlp cookie 卡死 → B站 API + aria2c
2. player/v2 无 AI 字幕 → 用 player/wbi/v2 + aid+cid
3. B站 AI 字幕质量差 → 优先本地 large-v3 ASR
4. pyannote 4.x generator → StopIteration.value.speaker_diarization
5. float32 单核 → BatchedInferencePipeline 才对

## 性能 (M1 Max)

| 步骤 | 时间 |
|------|------|
| API 字幕 | <1s |
| 音频下载 | ~15s |
| ASR | ~8min |
| Diarization | ~79s |
| LLM 总结 | ~1min |
