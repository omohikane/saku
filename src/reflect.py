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

# Ensure CODE_ROOT is in path so we can import saku_core
CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))

import saku_core as agent

# Resolve SAKU_ROOT from agent config
SAKU_ROOT = agent.SAKU_ROOT

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
    
    user_prompt = f"""[system] 今日一日の活動を振り返り、自己修正と成長を行う時間です。
以下に、今日の日記（Journal）と独り言（Monologue）を提示します。

【今日の日記 ({target_date})】
{journal_content if journal_content else "(空)"}

【今日の独り言 ({target_date})】
{monologue_content if monologue_content else "(空)"}

これらを分析し、以下のタスクを「自律的ツール実行」によって処理してください。
1. 新たに得られた知見、教訓、Ownerから指摘されたルールがあれば、[[WRITE_FILE path="principles/ファイル名.md"]] を使用して新規保存、または既存の教訓ファイルを修正・削除・統合して整理（メモリのデフラグ）を行ってください。
2. 自己モデル（meta.md）の各セクションに今日の活動結果を追記する。
   ※ meta.mdは WRITE_FILE での上書きが禁止。[[APPEND_FILE path="meta.md" heading="最近の出来事"]] などを使い、
   「## 最近の出来事」「## 次にやりたいこと」などの見出し直後にリスト項目を追加する形式でのみ更新してください。
   見出し構造（## で始まる行）は絶対に変更・削除しないでください。
   
終了時は「[REFLECTION_DONE]」と出力して回答を終了してください。
"""

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    print("[*] Contacting LLM for reflection...")
    
    max_turns = 5
    turn = 0
    current_visible = []
    current_thinking = []
    last_raw = ""

    while turn < max_turns:
        print(f"[*] Reflection turn {turn + 1}/{max_turns}...")
        raw_reply = agent.chat_stream(history)
        last_raw = raw_reply

        if raw_reply.startswith("[ERROR]"):
            print(f"[!] LLM Error: {raw_reply}")
            break

        thinking, visible = agent.split_thinking(raw_reply)
        if visible:
            current_visible.append(visible)
        if thinking:
            current_thinking.append(thinking)

        history.append({"role": "assistant", "content": visible})

        # Process any tools SAKU outputted during reflection
        tool_results = agent.exec_tools(raw_reply)
        if not tool_results:
            break

        tool_output = "\n".join(tool_results)
        print(f"\n[tool] {tool_output}")

        history.append(
            {
                "role": "user",
                "content": f"[system] tool results:\n{tool_output}",
            }
        )
        turn += 1

    # Log to journal
    if last_raw and not last_raw.startswith("[ERROR]"):
        merged_visible = "\n\n".join(current_visible).strip()
        merged_thinking = "\n\n".join(current_thinking).strip()
        agent.save_autonomous_log("自律振り返り", merged_visible, thinking=merged_thinking)
        print("[*] Reflection successfully completed and logged.")
    else:
        print("[!] Reflection failed or skipped due to errors.")

if __name__ == "__main__":
    # Support specifying date as arg, e.g. python3 reflect.py 2026-06-16
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    run_reflection(arg_date)
