#!/usr/bin/env python3
"""
SAKU Self-Reflection (Sleep/Reflection Loop)

Reads today's journal and monologues, runs an LLM session to digest
experiences, consolidate principles, update the self-model (meta.md),
and logs the results back into today's journal.

Before the LLM session, the day's completion claims are audited against
evidence by the self-verification loop (#102, saku/selfcheck.py) and the
result is appended to the day's journal and shown in the prompt.
"""

import sys
from datetime import datetime
from pathlib import Path

from saku import core as agent
from saku.agent_loop import run_agent_loop as _run_agent_loop

# Resolve SAKU_ROOT from agent config
SAKU_ROOT = agent.SAKU_ROOT


def build_reflection_prompt(target_date: str, journal_content: str, monologue_content: str, audit_content: str = "") -> str:
    """Build the reflection user prompt.

    The meta.md rule comes from ``agent.meta_update_rule()`` so it always agrees
    with the system prompt and the path policy (it used to be hardcoded here,
    which contradicted the system prompt and let the model overwrite meta.md
    wholesale).

    ``audit_content`` is the self-verification result for the day (#102),
    already appended to the day's journal by ``run_reflection``; when present
    the reflection is told to own up to unverified claims and, for kinds
    flagged as recurring, to record the lesson in principles.
    """
    audit_section = ""
    if audit_content:
        audit_section = f"""
【自己検証の結果 ({target_date})】
{audit_content}
自己検証で「未確認」とされた完了報告は、事実として扱わないでください。再発と指摘された種別については、教訓として principles に記録してください（先頭に「> source: journal/{target_date}」の行を入れること）。
"""
    return f"""[system] 今日一日の活動を振り返り、自己修正と成長を行う時間です。
以下に、今日の日記（Journal）と独り言（Monologue）を提示します。

【今日の日記 ({target_date})】
{journal_content if journal_content else "(空)"}

【今日の独り言 ({target_date})】
{monologue_content if monologue_content else "(空)"}
{audit_section}
これらを分析し、以下のタスクを「自律的ツール実行」によって処理してください。
1. 新たに得られた知見、教訓、Ownerから指摘されたルールがあれば、[[WRITE_FILE path="principles/ファイル名.md"]] を使用して新規保存、または既存の教訓ファイルを修正・削除・統合して整理（メモリのデフラグ）を行ってください。
   ※ 新規に作成する principles ファイルの先頭には、出典として「> source: journal/{target_date}」の行を必ず入れてください（記憶の出典記録のため）。
2. 自己モデル（meta.md）の各セクションに今日の活動結果を追記する。
   ※ {agent.meta_update_rule()}
   「## 最近の出来事」「## 次にやりたいこと」などの見出し直後にリスト項目を追加する形式で更新し、
   見出し構造（## で始まる行）は絶対に変更・削除しないでください。

終了時は「[REFLECTION_DONE]」と出力して回答を終了してください。
"""


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

    # Self-verification (#102): audit the day's completion claims against
    # evidence (files, git commits) before the LLM digests the day, so an
    # unverified claim is flagged in the journal and in the prompt instead
    # of being promoted into principles as fact. The audit appends its
    # result to the audited day's journal and updates the recurrence
    # ledger. A failure here must never block reflection itself.
    audit_content = ""
    try:
        from saku import config as cfg_mod
        from saku import selfcheck

        cfg, _ = cfg_mod.load_config()
        sc_cfg = cfg_mod.load_selfcheck_config(cfg)
        if sc_cfg.enabled:
            result = selfcheck.audit_texts(
                target_date,
                journal_content,
                monologue_content,
                agent.MEMORY_ROOT,
                [agent.MEMORY_ROOT, agent.CODE_ROOT.parent],
            )
            recurring = selfcheck.record_and_find_recurring(
                cfg_mod.resolve_selfcheck_ledger(cfg, agent.MEMORY_ROOT),
                target_date,
                result,
                sc_cfg.recurrence_threshold,
            )
            audit_content = selfcheck.render_audit(result, recurring)
            if audit_content:
                selfcheck.append_audit_to_journal(
                    agent.MEMORY_ROOT, target_date, audit_content
                )
                print(
                    f"[*] Self-check: {len(result.claims)} claim(s), "
                    f"{len(result.flagged)} flagged."
                )
    except Exception as e:
        print(f"[!] Self-check audit failed (reflection continues): {e}")

    # Build prompt
    system_prompt = agent.build_system_prompt()

    user_prompt = build_reflection_prompt(target_date, journal_content, monologue_content, audit_content)

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    print("[*] Contacting LLM for reflection...")

    result = _run_agent_loop(
        history,
        agent.llm_for_role("background"),
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=5,
    )

    # Log to journal
    if result.last_raw and not result.last_raw.startswith("[ERROR]"):
        agent.save_autonomous_log("自律振り返り", result.visible, thinking=result.thinking)
        print("[*] Reflection successfully completed and logged.")
    else:
        print("[!] Reflection failed or skipped due to errors.")

if __name__ == "__main__":
    # Support specifying date as arg, e.g. python3 reflect.py 2026-06-16
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    run_reflection(arg_date)
