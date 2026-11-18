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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import ContextConfig, DEFAULT_WORKING_BUDGET_TOKENS, LlmConfig
from .context import needs_compaction, trim_old_tool_results, truncate_history
from .llm import chat_stream
from .thinking import internal_markers_in, split_thinking, strip_tool_blocks
from .transport import exec_tools, list_tool_calls
from .verify import unbacked_writes


@dataclass
class LoopResult:
    visible: str  # combined visible output
    thinking: str  # combined thinking
    last_raw: str  # last raw LLM response
    history: list[dict]  # history after the loop (for the caller to take over)
    action_taken: bool
    turns: int = 0
    # #52: paths the reply claims it wrote but that the turn never touched.
    unbacked_claims: list[str] = field(default_factory=list)


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
    on_status: Optional[Callable[[str], None]] = None,
    no_action_markers: Optional[list[str]] = None,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> LoopResult:
    """Run the common agent loop once.

    In history, index 0 is the system prompt and the tail holds the most recent
    user/assistant messages. The loop operates on a copy and returns the final
    state via the ``history`` field of the result.

    ``on_status`` reports coarse state changes ("thinking", "tool: NAME",
    "compacting context") so a UI can show that the agent is working rather than
    frozen. It is optional and silent when not given.

    ``should_stop`` (#76) is consulted before every turn: when it returns
    True the loop halts without another LLM call, so an external stop
    signal (the kill switch) takes effect at the next turn boundary
    instead of only when the whole run finishes. Silent when not given.
    """
    budget = llm_cfg.working_budget_tokens or DEFAULT_WORKING_BUDGET_TOKENS
    started_at = time.time()
    work = list(history)

    def _status(text: str) -> None:
        if on_status is not None:
            on_status(text)

    # #54: what the caller shows is the text of the *last* turn that produced any.
    # A turn that also calls a tool usually carries only a preamble, and joining every
    # turn posted the same answer twice ("要点は…" followed by "了解しました。要点は…").
    final_visible = ""
    current_thinking: list[str] = []
    last_raw = ""
    last_tool_output = ""
    action_taken = False
    turn = 0

    while turn < max_turns:
        # 0. External stop signal (#76): halt before spending another turn.
        if should_stop is not None and should_stop():
            if log:
                log("agent loop halted by the kill switch")
            break

        # 1. Shrink old tool results
        work = trim_old_tool_results(work, ctx)

        # 2. Compact history if the working budget is exceeded
        if needs_compaction(work, budget, ctx):
            work, dropped = truncate_history(work, ctx.keep_recent_tokens)
            # Announce only a compaction that actually removed something. keep_recent_tokens
            # can be larger than the budget, in which case this is a no-op — logging
            # "compacted: dropped 0 chars" on every turn of a chat was pure noise.
            if dropped:
                _status("compacting context")
                if log:
                    log(f"context compacted: dropped {dropped} chars, history={len(work)} msgs")

        # 3. LLM call
        _status("thinking")
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
        # #51: warn (without failing the turn) when the reply echoes prompt scaffolding.
        if log:
            leaked = internal_markers_in(visible)
            if leaked:
                log(f"visible text contained internal prompt fragments: {leaked}")
        clean_visible = strip_tool_blocks(visible)
        if clean_visible:
            final_visible = clean_visible
        if thinking:
            current_thinking.append(thinking)

        # Store the cleaned text in history so tool-call syntax is not echoed back
        # (which would make the model repeat the same tool calls).
        work.append({"role": "assistant", "content": clean_visible or visible})

        # 5. Execute tools
        tool_names = list_tool_calls(raw_reply)
        if tool_names:
            _status("tool: " + ", ".join(tool_names))
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

    # Final-answer rescue: a research-style turn can spend every turn on tools (or the
    # repeat guard halts the loop), leaving the caller with nothing to show. Ask once
    # more, explicitly without tools, and use that as the visible answer. A halted
    # loop (#76) skips the rescue: the stop signal means no more LLM calls either.
    if (
        not final_visible
        and not last_raw.startswith("[ERROR]")
        and not (should_stop is not None and should_stop())
    ):
        _status("writing answer")
        rescue_history = list(work) + [
            {
                "role": "user",
                "content": (
                    "[system] ツールはもう使えません。これまでに得た結果だけを使って、"
                    "最終的な回答を書いてください。"
                ),
            }
        ]
        raw_final = chat_stream(rescue_history, llm_cfg, on_token=on_visible)
        if raw_final and not raw_final.startswith("[ERROR]"):
            _thinking, visible_final = split_thinking(raw_final)
            clean_final = strip_tool_blocks(visible_final).strip()
            if clean_final:
                final_visible = clean_final
                work.append({"role": "assistant", "content": clean_final})
                last_raw = raw_final

    # Verification (#52): a reply that says it wrote a file must be backed by a file the
    # turn actually touched. Report the mismatch instead of trusting the text — with a
    # single-bracket tool call the write never happened and the Owner was told it did.
    unbacked = unbacked_writes(final_visible, memory_root, started_at)
    if unbacked and log:
        log(f"reply claims writes that nothing backed: {', '.join(unbacked)}")

    # Persist the turn's writes to the memory repository. No-op unless [git] is
    # enabled, debounced, and queued in a background thread so a push never delays
    # the reply.
    try:
        from .vault_git import pull_if_due, sync_if_due

        pull_if_due(memory_root)
        sync_if_due(memory_root)
    except Exception:
        pass

    return LoopResult(
        visible=final_visible.strip(),
        thinking="\n\n".join(current_thinking).strip(),
        last_raw=last_raw,
        history=work,
        action_taken=action_taken,
        turns=turn + 1,
        unbacked_claims=unbacked,
    )
