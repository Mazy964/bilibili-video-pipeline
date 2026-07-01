# bilibili-video-pipeline

B站视频全自动处理流水线：下载 → 字幕提取 → 本地ASR → 说话人分离 → LLM精炼 → Obsidian笔记

Apple Silicon (M1 Max) 优化，~10分钟处理37分钟视频。

## 功能

| 步骤 | 方法 | 耗时 |
|------|------|------|
| 字幕提取 | B站 API (CC字幕秒级) | <1s |
| 音频下载 | B站 API + aria2c 多路并行 | ~15s |
| 本地 ASR | faster-whisper BatchedInferencePipeline (batch_size=16, float32) | ~8min |
| 说话人分离 | pyannote MPS (Apple GPU 加速) | ~79s |
| LLM 精炼 | DeepSeek → 结构化 Obsidian 笔记 + 术语词条 | ~1min |

## 项目结构

```
.
├── scripts/
│   ├── video-process.py           # 主流程：下载+转录（支持多P并行下载）
│   ├── transcript-to-obsidian.py  # 转录 → LLM → Obsidian 结构化笔记
│   ├── bili-ai-summary.py         # B站 AI 总结 API 查询
│   └── check-provider.py          # Provider 健康检查
├── skills/
│   ├── whisper-diarize-apple-silicon/
│   │   └── SKILL.md               # Hermes Agent skill（核心流水线）
│   └── ego-browser/
│       └── SKILL.md               # ego-lite 浏览器自动化
└── README.md
```

## 快速开始

### 前置条件

- macOS Apple Silicon (M1/M2/M3/M4 Max)
- Python 3.11+ (推荐用 venv)
- [aria2c](https://aria2.github.io/) (`brew install aria2`)
- ffmpeg@7 (`brew install ffmpeg@7`)

### 安装依赖

```bash
# 下载模型（首次运行自动下载）
pip install faster-whisper pyannote.audio torch
# MPS diarization 需要 HuggingFace token（接受 pyannote 许可）
```

### 使用

```bash
# 1. 下载+转录（全流程）
python scripts/video-process.py "https://www.bilibili.com/video/BVxxx"

# 2. 只下载（支持多P并行）
python scripts/video-process.py "https://www.bilibili.com/video/BVxxx" --download-only --workers 5

# 3. 只转录已有音频
python scripts/video-process.py "https://www.bilibili.com/video/BVxxx" --transcribe-only --diarize --hf-token YOUR_TOKEN

# 4. 转录 → Obsidian 笔记
python scripts/transcript-to-obsidian.py ~/videos/uploader_title/P01_xxx/

# 5. 查询 B站 AI 总结
python scripts/bili-ai-summary.py BVxxx --cookie-file ~/.hermes/bilibili_cookies.txt
```

## 关键决策

- **BatchedInferencePipeline**：faster-whisper float32 在 Apple Silicon 仅单核，batch推理 2.5x 加速
- **pyannote MPS**：说话人分离用 Apple GPU，79s 处理 37min（vs CPU 96x 慢）
- **B站字幕策略**：CC字幕（UP主上传）→ API秒拿；AI字幕 → 质量差，直接本地ASR
- **章节分段**：自动从 B站 API `view_points` 拉取章节，LLM 按章节组织笔记
- **放弃 yt-dlp cookie**：macOS 上 `--cookies-from-browser chrome` 卡死，改用 B站 API + aria2c

## 输出示例

```
~/videos/老石谈芯_一期视频看懂物理AI/
├── info.yaml              # 视频元信息
├── P01_xxx/
│   ├── video.mp4
│   ├── audio.m4a
│   ├── transcript.txt
│   └── info.yaml
├── transcript_combined.txt
└── summary.md

→ Obsidian vault: 视频笔记/老石谈芯_一期视频看懂物理AI/
  ├── MOC.md
  ├── P01_xxx.md            # 结构化笔记（核心概念+精要+引用）
  ├── P01_xxx_transcript.md # 完整转录
  └── terms/                # 术语词条
```

## License

MIT
