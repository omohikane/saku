"""Write a file within _saku/ (restricted paths only)."""

from pathlib import Path

ALLOWED_PREFIXES = [
    "blog/",
    "monologue/",
    "principles/",
    "skills/",
    "tools/",
    "chat.md",
    "study/",
    "request_list.md",
    "journal/",
]

DENIED_EXACT = ["meta.md"]


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    if path in DENIED_EXACT:
        return f"[DENY] {path} cannot be overwritten with WRITE_FILE. Use APPEND_FILE to add entries."

    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot write to: {path}"

    # tools/ now writes to base/tools/ (= SAKU's user tools), not src/system_tools/
    target = (base / path).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    return f"[OK] wrote {path} ({len(body)} bytes)"
