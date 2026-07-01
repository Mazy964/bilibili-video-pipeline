---
name: kanban-auto-fixer-video
description: "Video pipeline fixer: repairs blocked reviewer tasks for B站 video→transcript→Obsidian pipeline. Called by kanban-auto-fixer base when assignee=reviewer."
version: 1.0.0
---

# Video Pipeline Auto-Fixer

Called by the base dispatcher when a video pipeline reviewer blocks a task.

## Decision logic

Parse the reviewer's block reason. Classify each issue:

| Issue pattern | Layer | Fix action |
|--------------|-------|------------|
| "时间戳" + incorrect range | T1 | Fix timestamp alignment in transcript |
| "ASR错误" / name errors / typos | T1 | Correct transcript.json, regenerate transcript.txt |
| Missing timestamps in transcript | T1 | Regenerate transcript.txt from transcript.json |
| "遗漏" content / missing sections | T2 | Recreate obsidian-writer task with missing content hints |
| "格式" / formatting issues | T2 | Recreate obsidian-writer task with format requirements |
| "术语" / terminology errors | T1+T2 | Fix in transcript if ASR error; else in T2 instructions |

## Fix workflow

### Step 1: Locate the project directory

From the blocked reviewer task's body or parent T2's body, extract the project path. Usually `~/videos/<uploader>_<title>/`.

### Step 2: T1-layer fixes (transcript)

```python
import json, os

# Load transcript
with open(f"{project_dir}/transcript.json") as f:
    segments = json.load(f)

# Apply corrections from block reason
for seg in segments:
    seg["text"] = seg["text"].replace("冯静", "冯骥")
    seg["text"] = seg["text"].replace("乌赫泉", "邬贺铨")
    # ... add more as needed

# Regenerate timestamped transcript.txt
with open(f"{project_dir}/transcript.txt", "w") as f:
    for seg in segments:
        sm, ss = int(seg["start"]//60), int(seg["start"]%60)
        em, es = int(seg["end"]//60), int(seg["end"]%60)
        f.write(f"[{sm:02d}:{ss:02d}-{em:02d}:{es:02d}] {seg['text']}\n")
```

### Step 3: T2-layer fixes (notes)

```python
# Create new obsidian-writer task with fix instructions
t2_new = kanban_create(
    title=f"精炼笔记v{n+1}: {title}（自动修复版）",
    assignee="obsidian-writer",
    body=f"""重新生成笔记，修复以下问题：
{fix_list}

使用转录文件: {project_dir}/transcript.txt（已修复时间戳和ASR错误）
输出到 Obsidian vault「视频笔记/{project_slug}/」
要求：时间戳精确匹配 transcript、术语准确、完整覆盖全片内容""",
)

# Create new reviewer
kanban_create(
    title=f"审核v{n+1}: {title}（自动修复版）",
    assignee="reviewer",
    parents=[t2_new["task_id"]],
    body=f"检查以下问题是否修复: {original_block_reasons}",
)
```

### Step 4: Cleanup

```python
kanban_complete(
    summary=f"已修复T1层{len(t1_issues)}项+T2层{len(t2_issues)}项，新建T2b+T3b"
)
```

## Common ASR fix dictionary

Frequently misrecognized names/terms to check:

```python
FIXES = {
    "冯静": "冯骥", "冯济": "冯骥",
    "乌赫泉": "邬贺铨", "课金": "氪金",
    "颇天": "泼天", "出圈西经典": "出圈新经典",
    "三D": "3D", "S-C-D-M-A": "TD-SCDMA",
}
```
