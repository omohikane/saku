"""List files and directories within the memory root (policy from config)."""

import os
from pathlib import Path

from saku.config import get_path_policy

_CODE_ROOT = Path(__file__).resolve().parent.parent


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    # Root listing (path="") is always allowed; subdirectories must be read-allowed.
    if path and not get_path_policy().is_read_allowed(path):
        return f"[DENY] cannot list: {path}"

    if path.startswith("saku/"):
        vault = _CODE_ROOT.parent  # repo root
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
