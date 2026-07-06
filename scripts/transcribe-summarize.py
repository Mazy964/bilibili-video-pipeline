#!/usr/bin/env python3
"""通用 B站视频转录+总结脚本
用法: python3 scripts/transcribe-summarize.py <audio.m4a> "视频标题"
输出: 同目录下 transcript_raw.txt + transcript.md
"""
import json, os, sys, time, yaml, requests

AUDIO = sys.argv[1]
TITLE = sys.argv[2] if len(sys.argv) > 2 else "未命名视频"
OUT_DIR = os.path.dirname(os.path.abspath(AUDIO))

# ── 1. Transcribe ──
print(f"🎙️ 转录 {os.path.basename(AUDIO)} ...")
t0 = time.time()
from faster_whisper import WhisperModel, BatchedInferencePipeline

model = WhisperModel("large-v3", device="cpu", compute_type="float32")
batched = BatchedInferencePipeline(model=model)
segments, info = batched.transcribe(AUDIO, language="zh", beam_size=5, batch_size=16)

lines = [f"[{s.start:.1f}-{s.end:.1f}] {s.text.strip()}" for s in segments]
text = "\n".join(lines)
t1 = time.time()
print(f"✅ {len(lines)}句 {len(text)}字 {t1-t0:.0f}s lang={info.language}")

raw_path = os.path.join(OUT_DIR, "transcript_raw.txt")
with open(raw_path, "w") as f:
    f.write(text)

# ── 2. DeepSeek Summary ──
print("🤖 DeepSeek 总结 ...")
with open(os.path.expanduser("~/.hermes/config.yaml")) as f:
    cfg = yaml.safe_load(f)
ds = next(p for p in cfg["custom_providers"] if p["name"] == "hermes-ds")

def ds_chat(prompt, max_tok=4096):
    r = requests.post(
        f"{ds['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {ds['api_key']}"},
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": max_tok},
        timeout=180,
    )
    return r.json()["choices"][0]["message"]["content"]

# Chunk if >8000 chars
CHUNK = 8000
chunks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]

if len(chunks) == 1:
    summary = ds_chat(f"""以下是视频"{TITLE}"的转录文字，请总结。

格式：
## 一、核心结论
## 二、主要内容（分段）
## 三、关键观点/数据
## 四、总结

转录：\n{chunks[0]}""")
else:
    parts = []
    for i, c in enumerate(chunks):
        parts.append(ds_chat(f"总结以下转录片段({i + 1}/{len(chunks)})：\n\n{c}"))
    combined = "\n---\n".join(parts)
    summary = ds_chat(f"将以下片段整理为最终报告：\n\n## 一、核心结论\n## ...\n\n{combined}", max_tok=6000)

# ── 3. Save ──
report = f"""# {TITLE}

**转录 {len(lines)}句 | {t1 - t0:.0f}s | {time.strftime('%Y-%m-%d %H:%M')}**

{summary}
"""
md_path = os.path.join(OUT_DIR, "transcript.md")
with open(md_path, "w") as f:
    f.write(report)
print(f"✅ {md_path}")
print(summary[:500])
