"""Read a file within the memory root (path policy from config)."""

from pathlib import Path

from saku.config import get_path_policy, normalize_memory_path

_CODE_ROOT = Path(__file__).resolve().parent.parent


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    rel = normalize_memory_path(path)
    if rel is None or not get_path_policy().is_read_allowed(rel):
        return f"[DENY] cannot read from: {path}"

    if rel.startswith("saku/"):
        vault = _CODE_ROOT.parent  # repo root
        target = (vault / rel).resolve()
        if not target.is_relative_to(vault.resolve()):
            return "[DENY] scope outside vault"
    else:
        vault = base.parent if base.name == "_saku" else base
        target = (base / rel).resolve()
        if not target.is_relative_to(vault.resolve()):
            return "[DENY] scope outside vault"

    if not target.exists():
        return f"[ERROR] not found: {path}"

    content = target.read_text(encoding="utf-8")
    if len(content) > 4000:
        content = content[:4000] + "\n\n[... truncated at 4000 chars]"

    return content
