# Kanban 多 Agent 流水线部署

将视频处理流水线拆成专业 profile，通过 Kanban 看板实现自动级联执行。

## Profile 设计

```
default           → 编排者，只说"处理这个视频"
transcriber       → 下载+ASR+diarization，tools: terminal+file
obsidian-writer   → 转录→LLM精炼→Obsidian，tools: terminal+file  
reviewer          → 质量审核，tools: file
fixer             → 自动修复 blocked 任务，tools: terminal+file+kanban
```

## 搭建步骤

```bash
# 1. 创建 profile
hermes profile create transcriber --clone
hermes profile create obsidian-writer --clone
hermes profile create reviewer --clone
hermes profile create fixer --clone

# 2. 初始化 Kanban
hermes kanban init

# 3. 只需 default gateway 在线，Dispatcher 即可派发所有 profile
```

## 任务图模式

```
T1: 下载+转录       → transcriber      (ready)
T2: 精炼→Obsidian   → obsidian-writer   (todo, parents=[T1])
T3: 审核            → reviewer          (todo, parents=[T2])
```

T1 done → T2 自动升 ready → spawn → T2 done → T3 自动升 ready → spawn。

## 自动修复流水线（reviewer block → auto-fix）

当 reviewer block 后，用 cron + fixer 实现自动修复：

```bash
# 创建 fixer cron（每2分钟扫描 blocked reviewer 任务）
cronjob create name="auto-fix-blocked-reviews" schedule="*/2 * * * *" \
  skills=["kanban-auto-fixer"] \
  prompt="扫描 Kanban 看板，找到 blocked + assignee=reviewer 的任务，分类修复..."

# ⚠️ schedule 必须用 "*/2 * * * *" 而非 "2m"（后者是 one-shot）
```

fixer skill 按 domain 分派：
```
kanban-auto-fixer (base) → 扫描 blocked → 按 assignee 路由
  ├── reviewer       → kanban-auto-fixer-video  → 修转录/重建笔记
  ├── code-reviewer  → kanban-auto-fixer-code   → (待扩展)
  └── proofreader    → kanban-auto-fixer-proof  → (待扩展)
```

## 创建任务

```bash
T1_ID=$(hermes kanban create "下载+转录：xxx" --assignee transcriber \
  --body "..." 2>&1 | grep -o 't_[a-f0-9]*')

T2_ID=$(hermes kanban create "精炼笔记：xxx" --assignee obsidian-writer \
  --parent "$T1_ID" --body "..." 2>&1 | grep -o 't_[a-f0-9]*')

T3_ID=$(hermes kanban create "审核：xxx笔记" --assignee reviewer \
  --parent "$T2_ID" --body "..." 2>&1 | grep -o 't_[a-f0-9]*')
```

## 手动触发 dispatch

```bash
hermes kanban dispatch  # 不等 60s Dispatcher 轮询
```

## 避坑

### Gateway 端口冲突

多 profile gateway 共享 feishu/api_server 端口。只需 default gateway 在线——Dispatcher 嵌入其中，能派发所有 profile 的 worker 进程。

### Scratch workspace

默认 scratch workspace 在任务完成后删除。Worker 写产物到绝对路径（如 `~/videos/`）避免丢失。

### Reviewer block 是正常质量门

Block reason 列出具体问题。修复后 `hermes kanban unblock <task_id>` 重新派发。

### 代码任务用 review-required

Worker 完成代码修改后不要 `kanban_complete`，而是：
```python
kanban_comment(body="diff: ~/path, tests: 14/14 pass")
kanban_block(reason="review-required: 改动完成，需审核 xxx")
```
