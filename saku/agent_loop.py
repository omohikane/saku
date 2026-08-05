"""Common agent loop.

The conversation loop shared by Saku, the daemon, reflect, and the Web UI.
LLM settings and context settings are received as arguments (no global dependencies).

Flow:
1. Shrink old tool results in history (pruning)
2. Compact history if the working budget is exceeded (compaction)
3. LLM call (streaming)
4. Separate thinking from visible parts
5. Execute tools → append results to history
6. Exit if there are no tools, otherwise repeat 1-5 up to max_turns
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import ContextConfig, DEFAULT_WORKING_BUDGET_TOKENS, LlmConfig
from .context import needs_compaction, trim_old_tool_results, truncate_history
from .llm import chat_stream
from .thinking import split_thinking, strip_tool_blocks
from .transport import exec_tools


@dataclass
class LoopResult:
    visible: str  # combined visible output
    thinking: str  # combined thinking
    last_raw: str  # last raw LLM response
    history: list[dict]  # history after the loop (for the caller to take over)
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
    """Run the common agent loop once.

    In history, index 0 is the system prompt and the tail holds the most recent
    user/assistant messages. The loop operates on a copy and returns the final
    state via the ``history`` field of the result.
    """
    budget = llm_cfg.working_budget_tokens or DEFAULT_WORKING_BUDGET_TOKENS
    work = list(history)

    current_visible: list[str] = []
    current_thinking: list[str] = []
    last_raw = ""
    last_tool_output = ""
    action_taken = False
    turn = 0

    while turn < max_turns:
        # 1. Shrink old tool results
        work = trim_old_tool_results(work, ctx)

        # 2. Compact history if the working budget is exceeded
        if needs_compaction(work, budget, ctx):
            work, dropped = truncate_history(work, ctx.keep_recent_tokens)
            if log:
                log(f"context compacted: dropped {dropped} chars, history={len(work)} msgs")

        # 3. LLM call
        raw_reply = chat_stream(work, llm_cfg, on_token=on_visible)
        last_raw = raw_reply

        if raw_reply.startswith("[ERROR]"):
            break

        # 4. Separate thinking from visible parts
        if no_action_markers:
            if not any(m in raw_reply for m in no_action_markers):
                action_taken = True
        else:
            action_taken = True

        thinking, visible = split_thinking(raw_reply)
        clean_visible = strip_tool_blocks(visible)
        if clean_visible:
            current_visible.append(clean_visible)
        if thinking:
            current_thinking.append(thinking)

        # Store the cleaned text in history so tool-call syntax is not echoed back
        # (which would make the model repeat the same tool calls).
        work.append({"role": "assistant", "content": clean_visible or visible})

        # 5. Execute tools
        tool_results = exec_tools(raw_reply, memory_root, code_root)
        if not tool_results:
            break
        action_taken = True

        tool_output = "\n".join(tool_results)
        if on_tool_result is not None:
            on_tool_result(tool_output)
        else:
            print(f"\n[tool] {tool_output}", flush=True)

        # Guard against a same-error loop: if the tool output is identical to the
        # previous turn (e.g. an unknown/unparsable tool the model keeps retrying),
        # stop instead of repeating the same exchange until max_turns.
        if tool_output == last_tool_output:
            if log:
                log("halting loop: repeated identical tool output")
            break
        last_tool_output = tool_output

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
