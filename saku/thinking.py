"""Separate thinking (<think>), visible parts, and tool-call syntax from the response text."""

import re

from .transport import strip_single_bracket_calls

# Complete blocks may carry an MCP tool's case-sensitive or qualified name
# (`svc/getState`), so the name class is not uppercase-only here. The unclosed
# variant below stays uppercase-only on purpose: `[[some link]]` prose must not
# be stripped, and an unclosed block is never executed anyway (#123).
TOOL_BLOCK_RE = re.compile(
    r"\[\[[A-Za-z0-9_./:-]+\s*[^\]]*\]\]\s*\n?.*?\[\[END\]\]", re.DOTALL
)
UNCLOSED_TOOL_START_RE = re.compile(r"\[\[[A-Z_]+\s*[^\]]*\]\]", re.MULTILINE)

# Prompt scaffolding the model sometimes echoes into its reply (#51): "[system] ..."
# lines with their bullet list, the recall block, and a step-by-step narration line.
# None of it belongs in chat.md, the journal, or the Web UI.
INTERNAL_MARKER_RES = (
    re.compile(r"^[ \t]*\[system\][^\n]*(?:\n(?=[ \t]*(?:[-*]|$))[^\n]*)*", re.MULTILINE),
    re.compile(
        r"^[ \t]*#{0,3}[ \t]*関連する記憶（自動想起）[^\n]*(?:\n(?=[ \t]*(?:[-*]|$))[^\n]*)*",
        re.MULTILINE,
    ),
    re.compile(
        r"^[ \t]*必要なら[ \t]*`?SEARCH_NOTES`?[^\n]*$", re.MULTILINE
    ),
    re.compile(
        r"^[ \t]*(?:Owner|オーナー)のメッセージは[^\n]*?(?:指示です|という指示)[。．]?[ \t]*$",
        re.MULTILINE,
    ),
)


def internal_markers_in(text: str) -> list[str]:
    """Return the internal-prompt fragments found in ``text`` (for warning logs)."""
    found: list[str] = []
    for rx in INTERNAL_MARKER_RES:
        for m in rx.finditer(text):
            fragment = m.group(0).strip().splitlines()[0][:60]
            if fragment not in found:
                found.append(fragment)
    return found


def sanitize_visible(text: str) -> str:
    """Remove echoed prompt scaffolding from visible text (issue #51)."""
    for rx in INTERNAL_MARKER_RES:
        text = rx.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text)


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

    Unclosed start tags (e.g. a bare ``[[READ_FILE path="meta.md"]]`` left when
    the model forgot ``[[END]]``) are removed as well, so tool syntax never leaks
    into the visible output. Single-bracket calls (``[READ_FILE path="meta.md"]``,
    issue #47) are removed too — they used to leak into chat.md and journal.
    Echoed prompt scaffolding ("[system] ...", the recall block) is removed last
    (issue #51).
    """
    cleaned = TOOL_BLOCK_RE.sub("", text)
    cleaned = UNCLOSED_TOOL_START_RE.sub("", cleaned)
    cleaned = strip_single_bracket_calls(cleaned)
    cleaned = sanitize_visible(cleaned)
    return cleaned.strip()
