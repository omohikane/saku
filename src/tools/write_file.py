"""Write a file within _saku/ (restricted paths only)."""

from pathlib import Path

ALLOWED_PREFIXES = [
    "drafts/",
    "monologue/",
    "principles/",
    "skills/",
    "tools/",
    "chat.md",
    "study/",
    "request_list.md",
    "journal/",
]

# meta.md is intentionally excluded from WRITE_FILE — use APPEND_FILE instead
# to prevent SAKU from overwriting the entire structured file.
DENIED_EXACT = ["meta.md"]


def run(base: Path, path: str, body: str = "") -> str:
    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    # Hard block for meta.md — use APPEND_FILE to add entries instead
    if path in DENIED_EXACT:
        return f"[DENY] {path} cannot be overwritten with WRITE_FILE. Use APPEND_FILE to add entries."

    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot write to: {path}"

    if path.startswith("tools/"):
        code_root = Path(__file__).parent.parent
        target = (code_root / path).resolve()
        if not target.is_relative_to(code_root.resolve()):
            return "[DENY] scope outside tools directory"
    else:
        target = (base / path).resolve()
        if not target.is_relative_to(base.resolve()):
            return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    return f"[OK] wrote {path} ({len(body)} bytes)"
