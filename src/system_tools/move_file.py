"""Move / rename a file within _saku/ (restricted paths only)."""

from pathlib import Path

ALLOWED_SOURCE_PREFIXES = [
    "blog/",
    "journal/",
    "monologue/",
    "principles/",
    "skills/",
    "study/",
    "tools/",
    "drafts/",
]

ALLOWED_TARGET_PREFIXES = ALLOWED_SOURCE_PREFIXES.copy()

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
    src = kwargs.get("from", "").strip()
    dst = kwargs.get("to", "").strip()

    if not src or not dst:
        return "[ERROR] 'from' and 'to' parameters are required"

    clean_src = src.lstrip("./")
    clean_dst = dst.lstrip("./")

    for name, clean in [("source", clean_src), ("target", clean_dst)]:
        if clean in DENIED_EXACT:
            return f"[DENY] cannot move {name}: {clean}"

    if not any(clean_src.startswith(p) for p in ALLOWED_SOURCE_PREFIXES):
        return f"[DENY] cannot move from: {src}"
    if not any(clean_dst.startswith(p) for p in ALLOWED_TARGET_PREFIXES):
        return f"[DENY] cannot move to: {dst}"

    target_src = (base / clean_src).resolve()
    target_dst = (base / clean_dst).resolve()

    if not target_src.is_relative_to(base.resolve()):
        return "[DENY] source scope outside memory directory"
    if not target_dst.is_relative_to(base.resolve()):
        return "[DENY] target scope outside memory directory"

    if not target_src.exists():
        return f"[ERROR] source not found: {src}"
    if target_dst.exists():
        return f"[ERROR] target already exists: {dst}"

    target_dst.parent.mkdir(parents=True, exist_ok=True)
    target_src.rename(target_dst)
    return f"[OK] moved {src} -> {dst}"
