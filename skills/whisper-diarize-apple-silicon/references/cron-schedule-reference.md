# Cron Schedule Reference

## Recurring vs One-Shot

| Schedule value | Behavior | Example |
|---------------|----------|---------|
| `"2m"` / `"30m"` / `"2h"` | ❌ One-shot — runs once then completes | `schedule="2m"` → runs once at now+2min, then stops |
| `"*/2 * * * *"` | ✅ Recurring — every 2 minutes forever | Cron expression format |
| `"every 2m"` | ✅ Recurring — every 2 minutes forever | Human-readable format |
| `"0 9 * * *"` | ✅ Recurring — daily at 9am | Standard cron expression |
| ISO timestamp | One-shot at that time | `"2026-06-01T09:00:00"` |

## Repeat parameter

- Omit `repeat` for forever on recurring schedules
- `repeat=5` → run exactly 5 times then stop
- `repeat=0` → ❌ interpreted as "once" (effectively one-shot)

## Auto-Fixer Pattern

After initial cron creation, always verify with:

```bash
hermes cron list  # check it's "active" not "completed"
```

If state shows "completed" after one run but you wanted recurring,
the schedule was likely interpreted as one-shot. Recreate with
`"*/N * * * *"` or `"every Nm"` format.
