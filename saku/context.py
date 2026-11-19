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
    ``prune_tool_results=False`` disables this proactive pruning (#126); the
    budget pass in :func:`enforce_budget` still shrinks anything oversized.
    """
    if not ctx.prune_tool_results:
        return history
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


# ── Final budget guarantee (#126) ───────────────────────
# Compaction is a heuristic: it counts the working budget, can keep more than
# the budget in "recent" tokens (keep_recent_tokens defaulted larger than the
# budget), and says nothing about one huge message. enforce_budget() is the
# last check before a request leaves the process: the whole history -- system
# prompt, request and recall included -- must fit.

_MIN_MESSAGE_CHARS = 80


def _shrink_text(text: str, target_tokens: int) -> str:
    """Cut ``text`` to roughly ``target_tokens``, keeping its head and tail."""
    if target_tokens <= 0:
        return ""
    current = estimate_tokens(text)
    if current <= target_tokens:
        return text
    keep_chars = max(_MIN_MESSAGE_CHARS, int(len(text) * target_tokens / current))
    if keep_chars >= len(text):
        return text
    head = int(keep_chars * 0.7)
    tail = max(0, keep_chars - head - 44)
    marker = "\n[...truncated to fit the context budget...]\n"
    return text[:head] + marker + (text[-tail:] if tail else "")


def enforce_budget(
    history: list[dict], budget_tokens: int, ctx: ContextConfig | None = None
) -> tuple[list[dict], int]:
    """Return a history that fits ``budget_tokens`` and the chars removed.

    Order of sacrifice (least damaging first):
    1. shrink the largest message (later messages shrink first within a pass,
       so the newest request keeps as much as possible),
    2. drop the oldest non-system messages,
    3. shrink the system prompt — only if it alone overflows.
    """
    if budget_tokens <= 0 or not history:
        return history, 0

    work = [dict(m) for m in history]
    before_chars = sum(len(str(m.get("content", ""))) for m in work)

    def _total() -> int:
        return total_tokens(work)

    # 1. Shrink oversized messages, largest first.
    for _ in range(64):
        total = _total()
        if total <= budget_tokens:
            break
        candidates = list(range(1, len(work))) or [0]
        idx = max(candidates, key=lambda i: estimate_tokens(str(work[i].get("content", ""))))
        content = str(work[idx].get("content", ""))
        current = estimate_tokens(content)
        target = max(1, current - (total - budget_tokens))
        shrunk = _shrink_text(content, target)
        if estimate_tokens(shrunk) >= current:
            break
        work[idx] = {**work[idx], "content": shrunk}

    # 2. Drop the oldest exchanges (never the system prompt, never the last one).
    while _total() > budget_tokens and len(work) > 2:
        del work[1]

    # 3. Last resort: the system prompt itself.
    if _total() > budget_tokens and work:
        others = total_tokens(work[1:])
        work[0] = {
            **work[0],
            "content": _shrink_text(
                str(work[0].get("content", "")), max(1, budget_tokens - others)
            ),
        }

    after_chars = sum(len(str(m.get("content", ""))) for m in work)
    return work, max(0, before_chars - after_chars)
