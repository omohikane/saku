"""Read a file within _saku/."""

from pathlib import Path

ALLOWED_PREFIXES = [
    "blog/",
    "journal/",
    "monologue/",
    "principles/",
    "skills/",
    "identity/",
    "children/",
    "genome.md",
    "meta.md",
    "tools/",
    "src/",
    "state/",
    "study/",
    "chat.md",
    "request_list.md",
]


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot read from: {path}"

    vault_root = base.parent if base.name == "_saku" else base
    target = (base / path).resolve()
    if not target.is_relative_to(vault_root.resolve()):
        return "[DENY] scope outside vault"

    if not target.exists():
        return f"[ERROR] not found: {path}"

    content = target.read_text(encoding="utf-8")
    if len(content) > 4000:
        content = content[:4000] + "\n\n[... truncated at 4000 chars]"

    return content
