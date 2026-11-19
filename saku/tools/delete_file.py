"""Delete a file within the memory root (restricted paths only, policy from config)."""

from pathlib import Path

from saku.config import get_path_policy, normalize_memory_path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    policy = get_path_policy()

    if not path:
        return "[ERROR] path is empty"

    rel = normalize_memory_path(path)
    if rel is None:
        return f"[DENY] cannot delete: {path}"

    if rel in policy.delete_denied_exact:
        return f"[DENY] cannot delete: {path}"

    if not policy.is_delete_allowed(rel):
        return f"[DENY] cannot delete from: {path}"

    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    if not target.exists():
        return f"[ERROR] not found: {path}"

    if target.is_dir():
        return f"[ERROR] cannot delete directory: {path}"

    target.unlink()
    return f"[OK] deleted {path}"
