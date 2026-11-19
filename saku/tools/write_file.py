"""Write a file within the memory root (restricted paths only, policy from config).

meta.md is special-cased: even when the path policy allows overwriting it, the
body must keep the canonical `## ` section structure (see
`config.META_SECTION_HEADINGS` and `memory/meta.template.md`). The model used to
drop or rename sections when overwriting, so the rule is enforced here in code
instead of relying on the prompt.
"""

import re
from pathlib import Path

from saku.config import META_SECTION_HEADINGS, get_path_policy, normalize_memory_path


def _section_headings(text: str) -> list[str]:
    """Return the `## ` section headings of text, in document order."""
    return [m.group(1).strip() for m in re.finditer(r"^##[ \t]+(.+?)[ \t]*$", text, re.MULTILINE)]


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    policy = get_path_policy()

    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    rel = normalize_memory_path(path)
    if rel is None:
        return f"[DENY] invalid path: {path}"

    if rel in policy.write_denied_exact:
        return f"[DENY] {rel} cannot be overwritten with WRITE_FILE. Use APPEND_FILE to add entries."

    if not policy.is_write_allowed(rel):
        return f"[DENY] cannot write to: {path}"

    if rel == "meta.md":
        headings = _section_headings(body)
        if headings != META_SECTION_HEADINGS:
            expected = "、".join(f"## {h}" for h in META_SECTION_HEADINGS)
            return (
                "[DENY] meta.md の見出し構造は変更できません。"
                f"期待する見出し（この順序）: {expected}。"
                "内容の追記は [[APPEND_FILE path=\"meta.md\" heading=\"...\"]] を使ってください。"
            )

    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    return f"[OK] wrote {rel} ({len(body)} bytes)"
