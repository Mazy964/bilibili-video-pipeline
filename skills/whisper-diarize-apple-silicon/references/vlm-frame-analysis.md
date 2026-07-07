# VLM 帧分析 — 模型调研与部署记录 (2026-07)

## 动机

纯音频转录能抓解说词，但**跑分图表、功耗曲线、对比柱状图**只在画面里。
极客湾这类评测视频，帧里的表格数据比口播更精准——解说念一遍"单核1866分"，
但图表在屏幕上停留十几秒，VLM 可以直接读。

## 模型实测（M1 Max 32GB）

### Kimi-VL-A3B (Moonshot) — ❌ M1 装不下

- 架构：16B MoE，仅激活 2.8B
- 能力：VideoMME 71.9, LongVideoBench 64.5, 128K上下文
- **M1 实测**：bfloat16 加载需 30.56 GiB → `RuntimeError: Invalid buffer size: 30.56 GiB` OOM
- CPU 卸载：加载成功（36s, 16.4B params on mps:0），但推理极慢，不实用
- 兼容性：需 `transformers==4.51.3`，更高版本 API 不兼容
- vLLM：MPS 无 CUDA 内核，M1 上不可用

### Penguin-VL-2B (Tencent) — ✅ 本地首选

- 架构：2B，LLM 初始化视觉编码器（非 CLIP/SigLIP）
- 亮点：OCR 友好、时间冗余感知压缩（TRA）、vLLM 插件
- 加载：bfloat16 ~4GB，`device_map="mps"` 直接跑
- 部署：纯 transformers，无需 vLLM

### 其他候选

| 模型 | 参数 | M1可用 | 备注 |
|------|------|--------|------|
| StreamingVLM | 7B | ❌ | ICLR'26，无限流实时，用途不同 |
| Leum-VL-8B | 8B | 可能 | SV6D结构解析，适合创作分析 |
| Groq Whisper | 云API | ✅ | 228x实时转录，$0.04/h，用户不熟悉故未集成 |

## 部署方式（Penguin-VL-2B）

```python
from transformers import AutoModelForCausalLM, AutoProcessor

model = AutoModelForCausalLM.from_pretrained(
    "tencent/Penguin-VL-2B",
    torch_dtype=torch.bfloat16,
    device_map="mps",
    trust_remote_code=True,
)
processor = AutoProcessor.from_pretrained(
    "tencent/Penguin-VL-2B", trust_remote_code=True
)

# 帧分析
messages = [{
    "role": "user",
    "content": [
        {"type": "image", "image": "frame_001.jpg"},
        {"type": "text", "text": "这张图中有什么数据？提取所有数字。"}
    ]
}]
text = processor.apply_chat_template(messages, add_generation_prompt=True)
inputs = processor(text=text, images=["frame_001.jpg"], return_tensors="pt").to("mps")
outputs = model.generate(**inputs, max_new_tokens=512)
```

## 帧抽取

```bash
# 下载低码率视频（仅用于抽帧，不存档）
ffmpeg -i video_low.mp4 -vf "fps=1/10" -q:v 2 frames/frame_%03d.jpg
# 25分钟 → ~150帧
```

**已知问题**：B站视频 CDN 比音频 CDN 慢得多（音频 2s vs 视频 120s+超时）。
浏览器截图可作为兜底方案。

## 集成计划

```
流水线新增步骤 ⑤：
├─ 下载低码率视频（仅抽帧用）
├─ ffmpeg 抽关键帧（fps=1/10）
├─ Penguin-VL-2B 批量分析帧 → JSON {数字, 图表类型, 屏显文字}
└─ 融合：DeepSeek（转录 + 帧数据）→ 完整报告
```
