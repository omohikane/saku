"""Delete a file within the memory root (restricted paths only, policy from config)."""

from pathlib import Path

from saku.config import get_path_policy


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    policy = get_path_policy()

    if not path:
        return "[ERROR] path is empty"

    # Normalise any leading ./ but keep the path relative
    clean = path.lstrip("./")

    if clean in policy.delete_denied_exact:
        return f"[DENY] cannot delete: {path}"

    if not policy.is_write_allowed(clean):
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
