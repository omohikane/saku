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
from saku.agent_loop import run_agent_loop as _run_agent_loop

# Resolve SAKU_ROOT from agent config
SAKU_ROOT = agent.SAKU_ROOT


def build_reflection_prompt(
    target_date: str,
    journal_content: str,
    monologue_content: str,
    *,
    skill_review: bool = False,
) -> str:
    """Build the reflection user prompt.

    The meta.md rule comes from ``agent.meta_update_rule()`` so it always agrees
    with the system prompt and the path policy (it used to be hardcoded here,
    which contradicted the system prompt and let the model overwrite meta.md
    wholesale).

    ``skill_review`` (#104) adds the learned-skills revision task. It is only
    included while skill learning is enabled, so with the feature off this
    prompt is byte-identical to what it was before the skill loop existed.
    """
    skill_task = ""
    if skill_review:
        skill_task = f"""
3. 学習したスキル（skills/ 配下で「> source: task/」から始まる出典を持つファイル）について、今日の日記にその使用・成否・より良い手順が記録されていた場合は、該当ファイルを [[WRITE_FILE path="skills/ファイル名.md"]] で改訂してください。元の「> source:」行は残し、新しい出典はカンマの後に空白を入れずに末尾へ足してください（例: 「> source: task/2026-08-01-1,journal/{target_date}」）。重複したスキルがあれば統合してよいです。日記に該当する記録が無ければ、このタスクは何もしないでください。
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
{skill_task}
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

    # Build prompt
    system_prompt = agent.build_system_prompt()

    # #104: learned skills are revised here, in reflection, never at
    # distillation time -- and only while the learning loop is enabled.
    skill_review = saku_config.load_skills_config(agent._cfg).learning_enabled
    user_prompt = build_reflection_prompt(
        target_date, journal_content, monologue_content, skill_review=skill_review
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
    else:
        print("[!] Reflection failed or skipped due to errors.")

if __name__ == "__main__":
    # Support specifying date as arg, e.g. python3 reflect.py 2026-06-16
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    run_reflection(arg_date)
