"""共通エージェントループ。

朔・デーモン・reflect・Web UI が共用する対話ループ。
LLM設定・コンテキスト設定は引数で受け取る（グローバル非依存）。

フロー:
1. 履歴の古いツール結果を縮小（pruning）
2. 作業予算超過なら履歴を圧縮（コンパクション）
3. LLM呼び出し（ストリーミング）
4. 思考と可視部分の分離
5. ツール実行 → 結果を履歴へ
6. ツールが無ければ終了、あれば 1〜5 を最大 max_turns まで繰り返す
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import ContextConfig, DEFAULT_WORKING_BUDGET_TOKENS, LlmConfig
from .context import needs_compaction, trim_old_tool_results, truncate_history
from .llm import chat_stream
from .thinking import split_thinking
from .transport import exec_tools


@dataclass
class LoopResult:
    visible: str  # 統合された可視出力
    thinking: str  # 統合された思考
    last_raw: str  # 最後の LLM 生応答
    history: list[dict]  # ループ後の履歴（呼び出し元が引き継ぐ用）
    action_taken: bool
    turns: int = 0


def run_agent_loop(
    history: list[dict],
    llm_cfg: LlmConfig,
    ctx: ContextConfig,
    memory_root: Path,
    code_root: Path,
    *,
    max_turns: int = 5,
    on_visible: Optional[Callable[[str], None]] = None,
    on_tool_result: Optional[Callable[[str], None]] = None,
    no_action_markers: Optional[list[str]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> LoopResult:
    """共通エージェントループを1回実行する。

    history は index 0 が system、末尾が直近のユーザー/アシスタントメッセージ。
    ループ中はコピーを操作し、結果の ``history`` に最終状態を返す。
    """
    budget = llm_cfg.working_budget_tokens or DEFAULT_WORKING_BUDGET_TOKENS
    work = list(history)

    current_visible: list[str] = []
    current_thinking: list[str] = []
    last_raw = ""
    action_taken = False
    turn = 0

    while turn < max_turns:
        # 1. 古いツール結果の縮小
        work = trim_old_tool_results(work, ctx)

        # 2. 作業予算超過なら履歴を圧縮
        if needs_compaction(work, budget, ctx):
            work, dropped = truncate_history(work, ctx.keep_recent_tokens)
            if log:
                log(f"context compacted: dropped {dropped} chars, history={len(work)} msgs")

        # 3. LLM 呼び出し
        raw_reply = chat_stream(work, llm_cfg, on_token=on_visible)
        last_raw = raw_reply

        if raw_reply.startswith("[ERROR]"):
            break

        # 4. 思考と可視部分の分離
        if no_action_markers:
            if not any(m in raw_reply for m in no_action_markers):
                action_taken = True
        else:
            action_taken = True

        thinking, visible = split_thinking(raw_reply)
        if visible:
            current_visible.append(visible)
        if thinking:
            current_thinking.append(thinking)

        work.append({"role": "assistant", "content": visible})

        # 5. ツール実行
        tool_results = exec_tools(raw_reply, memory_root, code_root)
        if not tool_results:
            break
        action_taken = True

        tool_output = "\n".join(tool_results)
        if on_tool_result is not None:
            on_tool_result(tool_output)
        else:
            print(f"\n[tool] {tool_output}", flush=True)

        work.append({"role": "user", "content": f"[system] tool results:\n{tool_output}"})
        turn += 1

    return LoopResult(
        visible="\n\n".join(current_visible).strip(),
        thinking="\n\n".join(current_thinking).strip(),
        last_raw=last_raw,
        history=work,
        action_taken=action_taken,
        turns=turn + 1,
    )
