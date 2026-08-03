"""Context management.

Helpers that keep the agent's context "small" assuming VRAM/RAM constraints.
The working budget is configured per ``[llm.instances.*]`` and compaction is
decided based on the instance's ``working_budget_tokens``.

- Token estimation (approximation by character count; tokenizer-independent)
- Shrinking/removing old tool results (pruning; no summarization needed)
- History compaction when the budget is exceeded
"""

from .config import ContextConfig


def estimate_tokens(text: str, chars_per_token: int = 3) -> int:
    """Approximate token count estimation.

    A simple approximation independent of model and tokenizer. Since Japanese is
    close to one token per character, ``chars_per_token=3`` provides a safe
    margin for both Latin-centered and Japanese text.
    """
    return max(1, len(text) // chars_per_token)


def total_tokens(history: list[dict], chars_per_token: int = 3) -> int:
    return sum(estimate_tokens(str(m.get("content", "")), chars_per_token) for m in history)


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


def needs_compaction(history: list[dict], budget_tokens: int, ctx: ContextConfig, chars_per_token: int = 3) -> bool:
    """Whether compaction is needed for the working budget."""
    if budget_tokens <= 0:
        return False
    return total_tokens(history, chars_per_token) > int(budget_tokens * ctx.compaction_trigger)


def truncate_history(history: list[dict], keep_recent_tokens: int, chars_per_token: int = 3) -> tuple[list[dict], int]:
    """Keep the system prompt (index 0) and retain keep_recent_tokens from the tail.

    Returns: (new history, dropped character count)
    """
    if not history:
        return history, 0
    system = history[0]
    tail = list(history[1:])

    kept: list[dict] = []
    used = 0
    limit = keep_recent_tokens * chars_per_token
    for msg in reversed(tail):
        used += len(str(msg.get("content", "")))
        kept.append(msg)
        if used >= limit:
            break
    kept.reverse()

    kept_chars = sum(len(str(m.get("content", ""))) for m in kept)
    total_chars = sum(len(str(m.get("content", ""))) for m in tail)
    return [system] + kept, total_chars - kept_chars
