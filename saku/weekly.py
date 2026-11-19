"""Weekly trend review: the growth loop's multi-day pass.

The daily reflection (reflection.py) digests one day at a time and only sees
journal/monologue for that date. Nothing looked across days, so the same insight
was re-derived and the same report posted twice within 2.5 hours -- the two
"自発的発話" entries on 2026-10-15/16 were identical apart from the date (issue
#43). This pass reads the last N days at once and asks for:

  1. repeated failures/habits (the same mistake, the same report, stalled work)
  2. progress on the intentions written in meta.md「次にやりたいこと」
  3. notes that should be consolidated (duplicated principles / wiki notes)
  4. one to three verifiable intentions for the coming week

The artifact is a single note (default principles/weekly-YYYY-Www.md). Unlike the
daily reflection this pass writes no raw journal entry: the note is the record.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from saku import config as saku_config
from saku import core as agent
from saku.agent_loop import run_agent_loop as _run_agent_loop

# Resolve SAKU_ROOT from agent config
SAKU_ROOT = agent.SAKU_ROOT


def week_key(day) -> str:
    """ISO week key, e.g. 2026-W42."""
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def collect_recent(days: int, max_chars: int, end=None) -> tuple[str, list[str]]:
    """Collect journal/monologue for the last ``days`` days, newest first.

    Stops adding days once ``max_chars`` is reached (the local model has a small
    context window) and returns the dates actually included, so a report can say
    what was really read.
    """
    end = end or datetime.now().date()
    chunks: list[str] = []
    dates: list[str] = []
    used = 0
    for offset in range(days):
        day = end - timedelta(days=offset)
        date_str = day.strftime("%Y-%m-%d")
        parts = []
        for name, path in (
            ("journal", SAKU_ROOT / "journal" / f"{date_str}.md"),
            ("monologue", SAKU_ROOT / "monologue" / f"{date_str}.md"),
        ):
            content = agent.load_file(path)
            if content:
                parts.append(f"### {name} ({date_str})\n{content}")
        if not parts:
            continue
        block = "\n\n".join(parts)
        if used + len(block) > max_chars and chunks:
            break
        chunks.append(block)
        dates.append(date_str)
        used += len(block)
    return "\n\n".join(reversed(chunks)), dates


def build_weekly_review_prompt(
    week: str,
    dates: list[str],
    records: str,
    meta_excerpt: str,
    target_path: str,
) -> str:
    """Build the weekly review user prompt (no hardcoded destinations)."""
    period = f"{dates[-1]} 〜 {dates[0]}" if dates else "(なし)"
    return f"""[system] 直近の活動を俯瞰する「週次レビュー」の時間です。
日次の振り返りは1日単位ですが、こちらは複数日をまたいだ傾向を見つけるパスです。

【対象期間】 {period}（{len(dates)}日分）
【対象日の記録】
{records if records else "(空)"}

【自己モデル（meta.md）】
{meta_excerpt if meta_excerpt else "(空)"}

次の観点で分析し、1つのノートにまとめてください。
1. 繰り返している失敗・癖（同じミス、同じ内容の報告、手が止まっている作業）
2. 「次にやりたいこと」のうち、実際に進んだもの・進んでいないもの
3. 統合・整理すべきノート（重複した principles / wiki）
4. 次の1週間の意図（1〜3件。検証できる形で書く）

[[WRITE_FILE path="{target_path}"]] で以下の見出し構成のノートとして保存してください。
# 週次レビュー {week}（{period}）

## 繰り返していること

## 意図の進捗

## 統合候補

## 次の1週間の意図

保存できたら「[WEEKLY_REVIEW_DONE]」と出力して終了してください。
"""


def run_weekly_review(now: datetime | None = None) -> dict:
    """Run one weekly review session. Returns what actually happened."""
    now = now or datetime.now()
    cfg = saku_config.load_growth_config(agent._cfg)
    end = now.date()
    week = week_key(end)

    result = {
        "week": week,
        "dates": [],
        "target_path": "",
        "written": False,
        "reason": "",
    }

    records, dates = collect_recent(cfg.weekly_review_days, cfg.weekly_review_max_chars, end)
    result["dates"] = dates
    if not records:
        result["reason"] = "no records"
        print(f"[!] Weekly review: no journal/monologue in the last {cfg.weekly_review_days} days.")
        return result

    target_path = cfg.weekly_review_path.format(week=week)
    target = SAKU_ROOT / target_path
    result["target_path"] = target_path
    before = target.stat().st_mtime_ns if target.exists() else None

    history = [
        {"role": "system", "content": agent.build_system_prompt()},
        {
            "role": "user",
            "content": build_weekly_review_prompt(
                week,
                dates,
                records,
                agent.load_file(SAKU_ROOT / "meta.md"),
                target_path,
            ),
        },
    ]

    print(f"[*] Contacting LLM for the weekly review ({week}, {len(dates)} days)...")
    loop_result = _run_agent_loop(
        history,
        agent.llm_for_role("background"),
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=8,
    )

    if not loop_result.last_raw or loop_result.last_raw.startswith("[ERROR]"):
        result["reason"] = "llm error"
        print("[!] Weekly review failed (LLM error).")
        return result

    after = target.stat().st_mtime_ns if target.exists() else None
    result["written"] = after is not None and after != before
    if not result["written"]:
        result["reason"] = "no note written"
        print(f"[!] Weekly review produced no note at {target_path}.")
    else:
        print(f"[*] Weekly review written to {target_path}.")
    return result


if __name__ == "__main__":
    print(run_weekly_review())
