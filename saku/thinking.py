"""Separate thinking (<think>), visible parts, and tool-call syntax from the response text."""

import re

TOOL_BLOCK_RE = re.compile(r"\[\[[A-Z_]+\s*[^\]]*\]\]\s*\n?.*?\[\[END\]\]", re.DOTALL)


def split_thinking(text: str) -> tuple[str, str]:
    """Separate the response into (thinking, visible)."""
    think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    thinking = "\n\n".join(block.strip() for block in think_blocks if block.strip())

    visible = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    visible = re.sub(r"\[thinking\.{0,3}\]\s*", "", visible)
    visible = visible.strip()

    return thinking, visible


def strip_tool_blocks(text: str) -> str:
    """Remove [[TOOL ...]] ... [[END]] blocks from visible text.

    Tool calls are execution instructions, not conversation content. They should
    not be shown to the user, stored in the journal, or echoed back into history.
    """
    return TOOL_BLOCK_RE.sub("", text).strip()
