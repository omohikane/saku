"""Context management.

Helpers that keep the agent's context "small" assuming VRAM/RAM constraints.
The working budget is configured per ``[llm.instances.*]`` and compaction is
decided based on the instance's ``working_budget_tokens``.

- Token estimation (approximation by character count; tokenizer-independent)
- Shrinking/removing old tool results (pruning; no summarization needed)
- History compaction when the budget is exceeded
"""

import re

from .config import ContextConfig

# Local LLM tokenizers (llama.cpp-style) consume ~1 token per Japanese/CJK
# character but share ASCII words across ~4 chars per token. Weighting CJK at
# full weight prevents underestimating the prompt size, which previously let the
# context silently overflow far past the server's ``-c`` window (this was the
# cause of "Context size has been exceeded" after long chats).
_CJK_RE = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
_ASCII_TOKENS_PER_CHAR = 1 / 4  # ~4 ASCII chars per token


def estimate_tokens(text: str) -> int:
    """Approximate token count, weighting CJK/wide chars at 1 token each."""
    cjk = len(_CJK_RE.findall(text))
    ascii_weighted = round((len(text) - cjk) * _ASCII_TOKENS_PER_CHAR)
    return max(1, cjk + ascii_weighted)


def total_tokens(history: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content", ""))) for m in history)


def is_tool_result_message(msg: dict) -> bool:
    """Whether the message is a "tool results" message in history (user-role tool results block)."""
    return msg.get("role") == "user" and str(msg.get("content", "")).startswith("[system] tool results:")


def trim_old_tool_results(history: list[dict], ctx: ContextConfig) -> list[dict]:
    """Shrink old tool results. Truncate all but the most recent ``keep_full`` entries to the specified character count.

    Tool results can be shrunk without summarization (same idea as OpenClaw's session pruning).
    """
    keep_full = 2
    seen = 0
    out: list[dict] = []
    for msg in reversed(history):
        if is_tool_result_message(msg):
            seen += 1
            if seen > keep_full:
                content = str(msg["content"])
                if len(content) > ctx.max_tool_result_chars:
                    msg = dict(msg)
                    msg["content"] = content[: ctx.max_tool_result_chars] + "\n\n[... tool output truncated to save context ...]"
        out.append(msg)
    out.reverse()
    return out


def needs_compaction(history: list[dict], budget_tokens: int, ctx: ContextConfig) -> bool:
    """Whether compaction is needed for the working budget."""
    if budget_tokens <= 0:
        return False
    return total_tokens(history) > int(budget_tokens * ctx.compaction_trigger)


def truncate_history(history: list[dict], keep_recent_tokens: int) -> tuple[list[dict], int]:
    """Keep the system prompt (index 0) and retain keep_recent_tokens from the tail.

    Returns: (new history, dropped character count)
    """
    if not history:
        return history, 0
    system = history[0]
    tail = list(history[1:])

    kept: list[dict] = []
    used_tokens = 0
    for msg in reversed(tail):
        used_tokens += estimate_tokens(str(msg.get("content", "")))
        kept.append(msg)
        if used_tokens >= keep_recent_tokens:
            break
    kept.reverse()

    kept_chars = sum(len(str(m.get("content", ""))) for m in kept)
    total_chars = sum(len(str(m.get("content", ""))) for m in tail)
    return [system] + kept, total_chars - kept_chars
