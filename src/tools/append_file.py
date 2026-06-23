"""Append content to a file within _saku/ (restricted paths only)."""

from pathlib import Path

ALLOWED_PREFIXES = [
    "drafts/",
    "monologue/",
    "principles/",
    "skills/",
    "study/",
    "meta.md",
    "chat.md",
    "request_list.md",
    "journal/",
]


def run(base: Path, path: str, body: str = "") -> str:
    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot append to: {path}"

    target = (base / path).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)
    
    # Read existing content to avoid duplicate write if already ends with newline
    exists = target.exists()
    
    with target.open("a", encoding="utf-8") as f:
        # If the file exists and is not empty, ensure we start on a new line
        if exists and target.stat().st_size > 0:
            f.write("\n")
        f.write(body.strip())

    return f"[OK] appended to {path} ({len(body)} bytes)"
