#!/usr/bin/env python3
"""
fav-report.py — 收藏夹周报
==========================
综合 fav-classify 分类建议 + fav-deadlink 失效清单 + fav-monitor 下载记录，
用 DeepSeek 生成周报 Markdown，输出到 Obsidian vault。

Usage:
  fav-report.py                        # 生成周报并写入 Obsidian vault
  fav-report.py --stdout               # 打印到标准输出
  fav-report.py --dry-run              # 仅收集数据，不调用 DeepSeek
  fav-report.py --weeks 2              # 汇总最近 N 周的数据 (默认 1)
  fav-report.py --vault <path>         # 自定义 Obsidian vault 路径

数据来源:
  cache/fav-classify.json             — 分类建议
  ~/videos/_fav_check/fav_dead_*.json — 失效检测 (取最新一份)
  ~/videos/.fav-state.json            — 下载记录
  cache/fav-videos.json               — 收藏夹全量视频列表
"""

import json, os, sys, time, ssl, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta
from glob import glob

os.environ["PYTHONUNBUFFERED"] = "1"

# ── Paths ──────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "..", "cache")

CLASSIFY_FILE  = os.path.join(CACHE_DIR, "fav-classify.json")
VIDEOS_FILE    = os.path.join(CACHE_DIR, "fav-videos.json")
DEADLINK_GLOB  = os.path.expanduser("~/videos/_fav_check/fav_dead_*.json")
STATE_FILE     = os.path.expanduser("~/videos/.fav-state.json")
CONFIG_PATH    = os.path.expanduser("~/.hermes/config.yaml")

OBSIDIAN_VAULT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Computer System"
)

# ── CLI args ───────────────────────────────────────────────────

STDOUT    = "--stdout" in sys.argv
DRY_RUN   = "--dry-run" in sys.argv
NUM_WEEKS = 1
VAULT     = OBSIDIAN_VAULT

for i, a in enumerate(sys.argv):
    if a == "--weeks" and i + 1 < len(sys.argv):
        NUM_WEEKS = int(sys.argv[i + 1])
    if a == "--vault" and i + 1 < len(sys.argv):
        VAULT = os.path.expanduser(sys.argv[i + 1])

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)
WEEK_AGO = NOW - timedelta(days=7 * NUM_WEEKS)

SSL_CTX = ssl.create_default_context()


# ── DeepSeek API ──────────────────────────────────────────────

def load_api_key():
    """Extract hermes-ds API key from Hermes config.yaml."""
    try:
        with open(CONFIG_PATH) as f:
            lines = f.readlines()
        in_ds = False
        for i, line in enumerate(lines):
            if line.strip() == "- name: hermes-ds":
                in_ds = True
            elif in_ds and line.strip().startswith("- name:"):
                break
            elif in_ds and "api_key:" in line:
                return line.split("api_key:", 1)[1].strip()
    except Exception:
        pass
    return None


API_KEY = load_api_key()
DS_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = "deepseek-v4-pro"


def deepseek_chat(messages, temperature=0.5, max_tokens=4000, retries=2):
    """Call DeepSeek chat API. Returns content string or None."""
    if DRY_RUN:
        print("[dry-run] 跳过 DeepSeek 调用", file=sys.stderr)
        return None

    payload = json.dumps({
        "model": DS_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                DS_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as resp:
                body = json.loads(resp.read().decode())
            return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300]
            print(f"  ⚠️ HTTP {e.code}: {err}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(5)
        except Exception as e:
            print(f"  ⚠️ {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(5)
    return None


# ── Data loading ──────────────────────────────────────────────

def load_json(path, default=None):
    """Load JSON file, return default if missing or broken."""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠️ 无法加载 {path}: {e}", file=sys.stderr)
        return default


def find_latest_deadlink():
    """Find the most recent fav_dead_*.json file."""
    files = sorted(glob(DEADLINK_GLOB))
    if not files:
        return None, None
    latest = files[-1]
    # Extract timestamp from filename: fav_dead_{media_id}_{YYYYMMDD_HHMMSS}.json
    basename = os.path.basename(latest)
    parts = basename.replace(".json", "").split("_")
    timestamp = "_".join(parts[-2:]) if len(parts) >= 2 else "unknown"
    return latest, timestamp


def load_classify_data():
    """Load classification results. Returns dict {bvid: {...}}."""
    return load_json(CLASSIFY_FILE, {})


def load_deadlink_data():
    """Load latest dead link scan results. Returns list of dicts."""
    path, ts = find_latest_deadlink()
    if not path:
        return [], None
    data = load_json(path, [])
    return data if isinstance(data, list) else [], ts


def load_monitor_state():
    """Load fav-monitor state. Returns dict."""
    return load_json(STATE_FILE, {"videos": {}, "last_scan": None})


def load_raw_videos():
    """Load raw video list from fav-videos.json. Returns list of dicts."""
    return load_json(VIDEOS_FILE, [])


# ── Stats computation ──────────────────────────────────────────

def ts_to_datetime(ts):
    """Convert Unix timestamp (int or str) to datetime."""
    if isinstance(ts, str):
        try:
            ts = int(ts)
        except ValueError:
            return None
    if ts is None or ts == 0:
        return None
    try:
        return datetime.fromtimestamp(ts, TZ)
    except (ValueError, OSError):
        return None


def compute_stats(raw_videos, classify, dead_data, state):
    """Compute all report statistics."""
    now = datetime.now(TZ)
    week_ago = now - timedelta(days=7 * NUM_WEEKS)

    # ── 本周新增: videos added in the last N weeks ──
    new_this_week = []
    if isinstance(raw_videos, list):
        for v in raw_videos:
            ft = v.get("fav_time", 0)
            dt = ts_to_datetime(ft)
            if dt and dt >= week_ago:
                new_this_week.append(v)
    if not new_this_week and isinstance(raw_videos, list):
        # Fallback: if no fav_time available, compare against state
        state_bvids = set(state.get("videos", {}).keys())
        for v in raw_videos:
            if v["bvid"] not in state_bvids:
                new_this_week.append(v)

    # ── 已下载: videos with status tasked/done in state ──
    downloaded = []
    videos_state = state.get("videos", {})
    for bvid, info in videos_state.items():
        if info.get("status") in ("tasked", "done"):
            downloaded.append({"bvid": bvid, **info})

    # ── 失效链接 ──
    dead = [d for d in dead_data if not d.get("alive", True)]

    # ── 分类建议 ──
    classify_list = []
    if isinstance(classify, dict):
        for bvid, info in classify.items():
            classify_list.append({"bvid": bvid, **info})
    elif isinstance(classify, list):
        classify_list = classify

    high_conf = [c for c in classify_list if c.get("confidence") == "高"]
    medium_conf = [c for c in classify_list if c.get("confidence") == "中"]
    low_conf = [c for c in classify_list if c.get("confidence") == "低"]

    # ── 热门标签: extract from suggested_folders ──
    folder_counts = {}
    for c in classify_list:
        folder = c.get("suggested_folder", "默认收藏夹")
        folder_counts[folder] = folder_counts.get(folder, 0) + 1
    top_folders = sorted(folder_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # ── 总收藏夹视频数 ──
    total_videos = len(raw_videos) if isinstance(raw_videos, list) else 0
    total_classified = len(classify_list)
    total_downloaded = len(downloaded)
    total_dead = len(dead)

    return {
        "week_ago": week_ago.strftime("%Y-%m-%d"),
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "weeks": NUM_WEEKS,

        "total_videos": total_videos,
        "total_classified": total_classified,
        "total_downloaded": total_downloaded,
        "total_dead": total_dead,

        "new_this_week": new_this_week,
        "new_count": len(new_this_week),

        "downloaded": downloaded,
        "downloaded_count": total_downloaded,

        "dead": dead,
        "dead_count": total_dead,

        "high_conf": high_conf,
        "medium_conf": medium_conf,
        "low_conf": low_conf,
        "high_count": len(high_conf),
        "medium_count": len(medium_conf),
        "low_count": len(low_conf),

        "top_folders": top_folders,
        "folder_counts": folder_counts,
    }


# ── Report generation ──────────────────────────────────────────

def build_report_prompt(stats):
    """Build prompt for DeepSeek to generate the weekly report."""
    week_label = f"最近 {stats['weeks']} 周" if stats["weeks"] > 1 else "本周"

    # Format new videos
    new_lines = []
    for v in stats["new_this_week"][:30]:  # cap at 30 for prompt
        title = v.get("title", "未知")
        uploader = v.get("upper", v.get("uploader", ""))
        ft = ts_to_datetime(v.get("fav_time", 0))
        time_str = ft.strftime("%m-%d %H:%M") if ft else ""
        new_lines.append(f"- {v['bvid']} | {title[:60]} | {uploader} | {time_str}")

    # Format downloaded
    dl_lines = []
    for d in stats["downloaded"][:30]:
        dl_lines.append(f"- {d['bvid']} | {d.get('title', '')[:60]} | 状态: {d.get('status', '?')}")

    # Format dead links
    dead_lines = []
    for d in stats["dead"][:30]:
        title = d.get("title", d.get("title_original", "未知"))
        dead_lines.append(f"- {d.get('bvid', '?')} | {title[:60]} | 原因: {d.get('reason', '?')}")

    # Format high-confidence suggestions
    suggest_lines = []
    for c in stats["high_conf"][:20]:
        suggest_lines.append(
            f"- {c['bvid']} | {c.get('title', '')[:60]} → 「{c.get('suggested_folder', '')}」 "
            f"({c.get('reason', '')[:40]})"
        )

    # Format top folders
    folder_lines = []
    for folder, count in stats["top_folders"]:
        folder_lines.append(f"- {folder}: {count} 个视频")

    prompt = f"""你是一个 B站收藏管理助手。请根据以下数据，生成一份{week_label}收藏夹周报 Markdown。

数据汇总:
- 收藏夹总视频数: {stats['total_videos']}
- {week_label}新增: {stats['new_count']} 个
- 已下载/处理: {stats['downloaded_count']} 个
- 失效链接: {stats['dead_count']} 个
- 分类建议总数: {stats['total_classified']} 条（高置信 {stats['high_count']} · 中 {stats['medium_count']} · 低 {stats['low_count']}）

{week_label}新增视频:
{chr(10).join(new_lines) if new_lines else '(无)'}

已下载视频:
{chr(10).join(dl_lines) if dl_lines else '(无)'}

失效链接:
{chr(10).join(dead_lines) if dead_lines else '(无)'}

高置信分类建议:
{chr(10).join(suggest_lines) if suggest_lines else '(无)'}

热门收藏夹 Top 5:
{chr(10).join(folder_lines) if folder_lines else '(无)'}

要求:
1. 用自然的语言概括，不要只是罗列数据
2. 周报标题格式: # 📊 B站收藏夹{week_label}周报 ({stats['week_ago']} ~ {stats['now']})
3. 包含以下章节:
   - 📥 新增收藏（简要介绍，列出 3-5 个重点）
   - 💾 下载进度（已处理多少，还有多少待处理）
   - ⚠️ 失效内容（需要清理的链接数，给出建议）
   - 🏷️ 分类建议（高置信度的建议汇总）
   - 📈 热门分类（Top 文件夹及趋势分析）
4. 结尾给出"下一步建议"（如清理失效链接、确认分类、处理新视频等）
5. 纯 Markdown，不要代码块包裹

只输出 Markdown 周报，不要多余文字。"""

    return prompt


def generate_report_from_stats(stats):
    """Generate Markdown report using DeepSeek."""
    prompt = build_report_prompt(stats)

    messages = [
        {"role": "system", "content": "你是一个专业的 B站收藏管理助手，擅长生成结构化的周报。"},
        {"role": "user", "content": prompt},
    ]

    result = deepseek_chat(messages, temperature=0.6, max_tokens=4000)

    if not result:
        # Fallback: generate a simple report without DeepSeek
        result = generate_fallback_report(stats)

    return result


def generate_fallback_report(stats):
    """Generate a basic report without DeepSeek (fallback)."""
    week_label = f"最近 {stats['weeks']} 周" if stats["weeks"] > 1 else "本周"

    lines = []
    lines.append(f"# 📊 B站收藏夹{week_label}周报 ({stats['week_ago']} ~ {stats['now']})")
    lines.append("")
    lines.append(f"> 自动生成 — 数据截止 {stats['now']}")
    lines.append("")

    # Summary
    lines.append("## 📋 概览")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 收藏夹总视频 | {stats['total_videos']} |")
    lines.append(f"| {week_label}新增 | {stats['new_count']} |")
    lines.append(f"| 已下载/处理 | {stats['downloaded_count']} |")
    lines.append(f"| 失效链接 | {stats['dead_count']} |")
    lines.append(f"| 分类建议 | {stats['total_classified']} 条（高 {stats['high_count']} · 中 {stats['medium_count']} · 低 {stats['low_count']}）|")
    lines.append("")

    # New videos
    if stats["new_this_week"]:
        lines.append("## 📥 新增收藏")
        lines.append("")
        for v in stats["new_this_week"][:15]:
            title = v.get("title", "未知")[:60]
            uploader = v.get("upper", v.get("uploader", ""))
            ft = ts_to_datetime(v.get("fav_time", 0))
            time_str = ft.strftime("%m-%d") if ft else ""
            lines.append(f"- `{v['bvid']}` {title} — {uploader} ({time_str})")
        lines.append("")

    # Downloaded
    if stats["downloaded"]:
        lines.append("## 💾 下载进度")
        lines.append("")
        lines.append(f"已处理 {stats['downloaded_count']} / {stats['total_videos']} 个视频")
        lines.append("")
        for d in stats["downloaded"][:10]:
            lines.append(f"- `{d['bvid']}` {d.get('title', '')[:50]}")
        lines.append("")

    # Dead links
    if stats["dead"]:
        lines.append("## ⚠️ 失效内容")
        lines.append("")
        lines.append(f"发现 {stats['dead_count']} 个失效视频，建议及时清理。")
        lines.append("")
        for d in stats["dead"][:10]:
            title = d.get("title", d.get("title_original", "未知"))
            lines.append(f"- `{d.get('bvid', '?')}` {title[:50]} — {d.get('reason', '?')}")
        lines.append("")

    # Classification
    if stats["high_conf"]:
        lines.append("## 🏷️ 高置信分类建议")
        lines.append("")
        for c in stats["high_conf"][:10]:
            lines.append(
                f"- `{c['bvid']}` → 「{c.get('suggested_folder', '')}」 "
                f"({c.get('reason', '')[:50]})"
            )
        lines.append("")

    # Top folders
    if stats["top_folders"]:
        lines.append("## 📈 热门分类 Top 5")
        lines.append("")
        for folder, count in stats["top_folders"]:
            lines.append(f"1. {folder}: {count} 个视频")
        lines.append("")

    # Next steps
    lines.append("## 🔜 下一步建议")
    lines.append("")
    suggestions = []
    if stats["dead_count"] > 0:
        suggestions.append(f"- 清理 {stats['dead_count']} 个失效链接: `fav-deadlink.py <media_id> --delete`")
    if stats["high_count"] > 0:
        suggestions.append(f"- 确认 {stats['high_count']} 条高置信分类建议，移动到对应收藏夹")
    if stats["new_count"] > 0:
        suggestions.append(f"- 处理 {stats['new_count']} 个新收藏视频: `fav-monitor.py` 创建下载 pipeline")
    if not suggestions:
        suggestions.append("- 收藏夹状态良好，暂无紧急事项 🎉")
    lines.extend(suggestions)
    lines.append("")

    return "\n".join(lines)


# ── Output ─────────────────────────────────────────────────────

def write_to_vault(content, stats):
    """Write report to Obsidian vault."""
    week_label = f"week-{stats['weeks']}" if stats["weeks"] > 1 else "weekly"
    date_str = stats["now"].replace(" ", "_").replace(":", "-")

    # Create reports folder
    report_dir = os.path.join(VAULT, "收藏夹周报")
    os.makedirs(report_dir, exist_ok=True)

    filename = f"收藏夹周报_{week_label}_{date_str}.md"
    filepath = os.path.join(report_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


# ── Main ───────────────────────────────────────────────────────

def main():
    week_label = f"最近 {NUM_WEEKS} 周" if NUM_WEEKS > 1 else "本周"

    print(f"📊 生成{week_label}收藏夹周报...", file=sys.stderr)
    print(f"   时间范围: {WEEK_AGO.strftime('%Y-%m-%d')} ~ {NOW.strftime('%Y-%m-%d')}", file=sys.stderr)

    # 1. Load data
    raw_videos = load_raw_videos()
    classify   = load_classify_data()
    dead_data, dead_ts = load_deadlink_data()
    state      = load_monitor_state()

    classify_len = len(classify) if isinstance(classify, (dict, list)) else 0
    print(f"   数据加载: 视频={len(raw_videos) if isinstance(raw_videos, list) else 0} "
          f"分类={classify_len} "
          f"失效扫描={len(dead_data)} "
          f"状态={len(state.get('videos', {}))}",
          file=sys.stderr)

    # 2. Compute stats
    stats = compute_stats(raw_videos, classify, dead_data, state)

    print(f"   {week_label}新增: {stats['new_count']}", file=sys.stderr)
    print(f"   已下载: {stats['downloaded_count']}", file=sys.stderr)
    print(f"   失效: {stats['dead_count']}", file=sys.stderr)
    print(f"   分类建议: {stats['total_classified']} 条 (高 {stats['high_count']})", file=sys.stderr)

    # 3. Generate report
    if DRY_RUN:
        report = generate_fallback_report(stats)
    else:
        print(f"   🤖 调用 DeepSeek 生成周报...", file=sys.stderr)
        report = generate_report_from_stats(stats)

    if not report:
        print("❌ 报告生成失败", file=sys.stderr)
        sys.exit(1)

    # 4. Output
    if STDOUT:
        print("\n" + report)
        out_path = "(stdout)"
    else:
        out_path = write_to_vault(report, stats)
        print(f"\n✅ 周报已保存 → {out_path}", file=sys.stderr)

    # Summary to stdout for piping
    print(json.dumps({
        "output": out_path,
        "new_count": stats['new_count'],
        "downloaded_count": stats['downloaded_count'],
        "dead_count": stats['dead_count'],
        "classified_count": stats['total_classified'],
        "high_conf_count": stats['high_count'],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
