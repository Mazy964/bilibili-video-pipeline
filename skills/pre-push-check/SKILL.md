---
name: pre-push-check
description: "Pre-push code quality gate: runs security scan, syntax check, and dead code detection before git push. Use before every push to GitHub."
version: 1.0.0
---

# Pre-Push Code Check

Run before every `git push`. Catches issues that shouldn't land on GitHub.

## Checklist

Run these in order. Stop on first failure.

### 1. Hardcoded credentials scan

```bash
# Scan for API keys, tokens, passwords in code
rg -n '(ghp_|sk-|hf_|DEEPSEEK|OPENAI|ANTHROPIC)_?(API_)?KEY|[a-zA-Z0-9]{32,}' \
   --type py --type md --type yaml \
   scripts/ skills/ 2>/dev/null
```

Ignore lines that are:
- `rg` command itself
- `echo "ghp_..."` in docs/README
- Placeholder strings like `YOUR_TOKEN` or `HF_TOKEN`
- `.env` or config references like `DEEPSEEK_API_KEY`

**If real credentials found → STOP, remove them, DO NOT push.**

### 2. Python syntax check

```bash
python3 -m py_compile scripts/*.py 2>&1
```

**If compile errors → fix before push.**

### 3. Dead code / duplicate check

Quick review of each changed file:
- Functions never called anywhere
- Variables assigned but never used
- Duplicated logic blocks (>5 lines identical)

### 4. Git diff review

```bash
git diff --cached --stat   # if staged
git diff --stat            # if not staged yet
```

Manual sanity check: does every changed file belong in this push?

## On failure

- Fix the issue
- Re-run the full checklist from step 1
- Only push when all pass

## On success

```bash
git push
```
