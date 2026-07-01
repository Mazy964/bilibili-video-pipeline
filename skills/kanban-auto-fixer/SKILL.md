---
name: kanban-auto-fixer
description: "Base fixer dispatcher: scans blocked Kanban tasks, routes to domain-specific fixer skills by assignee type. Load this skill alone for the cron; it dynamically loads the right domain fixer."
version: 2.0.0
---

# Kanban Auto-Fixer (Base Dispatcher)

One cron, one base skill, many domain fixers. The base skill scans the board and routes to the right specialist.

## Core logic

```
1. hermes kanban ls → find all blocked tasks
2. For each blocked task:
   a. Read assignee name
   b. Map assignee → domain skill:
      reviewer       → kanban-auto-fixer-video
      code-reviewer  → kanban-auto-fixer-code
      proofreader    → kanban-auto-fixer-proof
      (add new mappings here as new reviewer types are created)
   c. skill_view(domain_skill) → load fix instructions
   d. Execute domain-specific fixes
3. If no blocked tasks → quiet exit
4. If assignee not in map → skip (don't guess)
```

## Adding a new domain

1. Create skill `kanban-auto-fixer-<domain>` with fix strategies
2. Add mapping entry above
3. Done — no cron changes needed

## Safety rules

- Only touch blocked tasks whose assignee is in the mapping
- Never modify files outside the project's working directory
- Quiet exit when nothing to do (minimal token cost)
