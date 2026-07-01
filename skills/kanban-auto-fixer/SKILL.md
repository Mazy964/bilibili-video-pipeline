---
name: kanban-auto-fixer
description: "Auto-fix blocked Kanban tasks in the video pipeline. Reads reviewer block reasons, determines source layer (T1 transcript or T2 notes), applies fixes, and re-triggers the pipeline."
version: 1.0.0
---

# Kanban Auto-Fixer

Automatically resolves blocked reviewer tasks in the B站 video pipeline.

## Decision logic

When a reviewer blocks with issues, classify each issue:

| Issue type | Source layer | Fix action |
|-----------|-------------|------------|
| Transcription errors (wrong names, garbled text) | T1 transcriber | Fix transcript.json → regenerate transcript.txt |
| Missing timestamps in transcript | T1 transcriber | Regenerate transcript.txt with timestamps from transcript.json |
| Content gaps (missing sections/characters) | T2 obsidian-writer | Recreate T2 with updated instructions |
| Formatting/typo issues in notes | T2 obsidian-writer | Recreate T2 with fix hints |
| Terminology errors (课金→氪金) | T1 or T2 | Fix in transcript.json if source error; else in T2 instructions |

## Workflow

```
1. Read blocked reviewer task → extract block reason
2. Parse issues into T1-layer and T2-layer buckets
3. If T1-layer issues exist:
   a. Read transcript.json → apply fixes → regenerate transcript.txt
4. If T2-layer issues exist:
   a. Create new T2 task with fix instructions referencing fixed transcript
5. Create new reviewer task as child of new T2
6. Archive old blocked reviewer task
```

## Fix transcript.json

Load `/Users/mazy/videos/<project>/transcript.json`, apply corrections:

```python
import json
with open(transcript_json) as f:
    segments = json.load(f)

fixes = {}  # populated from block reason
for seg in segments:
    for wrong, right in fixes.items():
        seg["text"] = seg["text"].replace(wrong, right)
```

## Regenerate transcript.txt with timestamps

```python
with open(transcript_path, "w") as f:
    for seg in segments:
        sm, ss = int(seg["start"]//60), int(seg["start"]%60)
        em, es = int(seg["end"]//60), int(seg["end"]%60)
        f.write(f"[{sm:02d}:{ss:02d}-{em:02d}:{es:02d}] {seg['text']}\n")
```

## Create follow-up Kanban tasks

```python
# New T2 with fix instructions
t2_new = kanban_create(
    title=f"精炼笔记v{n+1}: {title}（自动修复版）",
    assignee="obsidian-writer",
    body=f"使用修复后的转录生成笔记。修复内容：{fix_summary}",
)

# New reviewer
kanban_create(
    title=f"审核v{n+1}: {title}（自动修复版）",
    assignee="reviewer",
    parents=[t2_new["task_id"]],
    body=f"检查以下问题是否修复：{block_reasons}",
)

# Clean up old reviewer
kanban_complete(summary="Issues forwarded to auto-fixer")
```
