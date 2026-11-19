#!/usr/bin/env python3
"""
SAKU Self-Reflection (Sleep/Reflection Loop)

Reads today's journal and monologues, runs an LLM session to digest
experiences, consolidate principles, update the self-model (meta.md),
and logs the results back into today's journal.
"""

import sys
from datetime import datetime
from pathlib import Path

from saku import config as saku_config
from saku import core as agent
from saku import followups as saku_followups
from saku.agent_loop import run_agent_loop as _run_agent_loop

# Resolve SAKU_ROOT from agent config
SAKU_ROOT = agent.SAKU_ROOT


def build_followups_section(open_rendered: str) -> str:
    """Build the follow-up digestion task for the reflection prompt (#103).

    The still-open follow-ups are shown verbatim from the structured record;
    the model answers in the [FOLLOWUPS] block, which code parses and
    applies after the session (see saku/followups.py). Journal prose is
    never the record -- this section is how findings stop evaporating.
    """
    return f"""
3. フォローアップの消化確認と新規登録をおこなう。
   以下は、過去の振り返りで登録され、まだ消化されていない課題・弱点の一覧です。
【未消化のフォローアップ】
{open_rendered if open_rendered else "(なし)"}
   - 上記のうち、今日の活動で解決・解消したものがあれば、その番号を done として報告してください。
   - 今日新たに見つけた弱点・課題・次に試すべきことがあれば、new として1件1行で登録してください（最大3件まで。本当に重要なものだけに絞ること）。
   - 報告は、回答の最後に必ず次の形式のブロックで出力してください（コードが読み取って記録します。このブロック以外の場所に書いても記録されません）。

[FOLLOWUPS]
done: 3
new: （新しい課題・弱点を1件1行で）
[/FOLLOWUPS]
"""


def build_reflection_prompt(target_date: str, journal_content: str, monologue_content: str,
                            followups_section: str = "") -> str:
    """Build the reflection user prompt.

    The meta.md rule comes from ``agent.meta_update_rule()`` so it always agrees
    with the system prompt and the path policy (it used to be hardcoded here,
    which contradicted the system prompt and let the model overwrite meta.md
    wholesale).
    """
    return f"""[system] 今日一日の活動を振り返り、自己修正と成長を行う時間です。
以下に、今日の日記（Journal）と独り言（Monologue）を提示します。

【今日の日記 ({target_date})】
{journal_content if journal_content else "(空)"}

【今日の独り言 ({target_date})】
{monologue_content if monologue_content else "(空)"}

これらを分析し、以下のタスクを「自律的ツール実行」によって処理してください。
1. 新たに得られた知見、教訓、Ownerから指摘されたルールがあれば、[[WRITE_FILE path="principles/ファイル名.md"]] を使用して新規保存、または既存の教訓ファイルを修正・削除・統合して整理（メモリのデフラグ）を行ってください。
   ※ 新規に作成する principles ファイルの先頭には、出典として「> source: journal/{target_date}」の行を必ず入れてください（記憶の出典記録のため）。
2. 自己モデル（meta.md）の各セクションに今日の活動結果を追記する。
   ※ {agent.meta_update_rule()}
   「## 最近の出来事」「## 次にやりたいこと」などの見出し直後にリスト項目を追加する形式で更新し、
   見出し構造（## で始まる行）は絶対に変更・削除しないでください。
{followups_section}
終了時は「[REFLECTION_DONE]」と出力して回答を終了してください。
"""


def _apply_followup_report(store: dict, path: Path, result, target_date: str) -> None:
    """Apply the session's [FOLLOWUPS] block to the record (#103).

    The block is looked for in the visible reply first, then in the raw
    reply (a marker the guard/normalizer trimmed from the visible text
    must not silently lose the report). Applying it can never fail the
    reflection: a malformed block simply leaves the record unchanged,
    and the open items are presented again tomorrow.
    """
    try:
        report = saku_followups.parse_followup_block(result.visible or "")
        if not report["done"] and not report["new"]:
            report = saku_followups.parse_followup_block(result.last_raw or "")
        saku_followups.record_check(store, report["done"], target_date)
        added = saku_followups.add_items(store, report["new"], target_date)
        saku_followups.save_store(path, store)
        print(
            f"[*] Follow-ups: {len(report['done'])} closed, {len(added)} new, "
            f"{len(saku_followups.open_items(store))} still open."
        )
    except Exception as e:
        print(f"[!] Follow-up bookkeeping failed (record left unchanged): {e}")


def run_reflection(target_date: str = None) -> None:
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
        
    print(f"[*] Starting reflection for date: {target_date}")
    
    # Load daily journal and monologue
    journal_path = SAKU_ROOT / "journal" / f"{target_date}.md"
    monologue_path = SAKU_ROOT / "monologue" / f"{target_date}.md"
    
    journal_content = agent.load_file(journal_path)
    monologue_content = agent.load_file(monologue_path)
    
    if not journal_content and not monologue_content:
        print(f"[!] No journal or monologue found for {target_date}. Nothing to reflect on.")
        return

    # Follow-ups (#103): load the structured record so this reflection can
    # check the digestion of earlier findings instead of re-deriving them.
    gcfg = saku_config.load_growth_config(agent._cfg)
    followups_path = None
    followups_store = None
    followups_section = ""
    if gcfg.followups_enabled:
        followups_path = saku_config.resolve_followups_file(agent._cfg, agent.MEMORY_ROOT)
        followups_store = saku_followups.load_store(followups_path)
        followups_section = build_followups_section(
            saku_followups.render_open_items(followups_store, target_date)
        )

    # Build prompt
    system_prompt = agent.build_system_prompt()

    user_prompt = build_reflection_prompt(
        target_date, journal_content, monologue_content,
        followups_section=followups_section,
    )

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    print("[*] Contacting LLM for reflection...")

    result = _run_agent_loop(
        history,
        agent._current_llm,
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=5,
    )

    # Log to journal
    if result.last_raw and not result.last_raw.startswith("[ERROR]"):
        agent.save_autonomous_log("自律振り返り", result.visible, thinking=result.thinking)
        print("[*] Reflection successfully completed and logged.")
        if followups_store is not None and followups_path is not None:
            _apply_followup_report(followups_store, followups_path, result, target_date)
    else:
        print("[!] Reflection failed or skipped due to errors.")

if __name__ == "__main__":
    # Support specifying date as arg, e.g. python3 reflect.py 2026-06-16
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    run_reflection(arg_date)
