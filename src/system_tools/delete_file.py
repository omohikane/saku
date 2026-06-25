"""Delete a file within _saku/ (restricted paths only)."""

from pathlib import Path

ALLOWED_PREFIXES = [
    "blog/",
    "journal/",
    "monologue/",
    "principles/",
    "skills/",
    "study/",
    "tools/",
    "drafts/",
]

DENIED_EXACT = [
    "meta.md",
    "chat.md",
    "request_list.md",
    "genome.md",
    "soul.md",
    "identity/genome.md",
    "identity/soul.md",
]


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    if not path:
        return "[ERROR] path is empty"

    # Normalise any leading ./ but keep the path relative
    clean = path.lstrip("./")

    if clean in DENIED_EXACT:
        return f"[DENY] cannot delete: {path}"

    if not any(clean.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot delete from: {path}"

    target = (base / clean).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    if not target.exists():
        return f"[ERROR] not found: {path}"

    if target.is_dir():
        return f"[ERROR] cannot delete directory: {path}"

    target.unlink()
    return f"[OK] deleted {path}"
