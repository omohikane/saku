"""List files and directories within _saku/."""

import os
from pathlib import Path

ALLOWED_PREFIXES = [
    "blog/",
    "journal/",
    "monologue/",
    "principles/",
    "skills/",
    "identity/",
    "children/",
    "src/",
    "tools/",
    "state/",
    "study/",
    "",
]

_CODE_ROOT = Path(__file__).resolve().parent.parent


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    if path.startswith("src/"):
        vault = _CODE_ROOT.parent
        target = (vault / path).resolve() if path else vault.resolve()
        if not target.is_relative_to(vault.resolve()):
            return "[DENY] scope outside vault"
    else:
        vault = base.parent if base.name == "_saku" else base
        target = (base / path).resolve() if path else base.resolve()
        if not target.is_relative_to(vault.resolve()):
            return "[DENY] scope outside vault"

    if not target.exists():
        return f"[ERROR] not found: {path or '.'}"

    if not target.is_dir():
        return f"[ERROR] not a directory: {path}"

    entries = sorted(target.iterdir())
    lines = []
    for e in entries:
        if e.name.startswith("."):
            continue
        prefix = "d" if e.is_dir() else "f"
        rel = os.path.relpath(e, base)
        lines.append(f"  {prefix} {rel}")

    if not lines:
        return f"(empty directory: {path or '.'})"

    return "\n".join(lines)
