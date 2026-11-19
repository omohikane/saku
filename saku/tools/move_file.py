"""Move / rename a file within the memory root (restricted paths only, policy from config)."""

from pathlib import Path

from saku.config import get_path_policy, normalize_memory_path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    policy = get_path_policy()

    src = kwargs.get("from", "").strip()
    dst = kwargs.get("to", "").strip()

    if not src or not dst:
        return "[ERROR] 'from' and 'to' parameters are required"

    clean_src = normalize_memory_path(src)
    clean_dst = normalize_memory_path(dst)
    if clean_src is None:
        return f"[DENY] invalid source path: {src}"
    if clean_dst is None:
        return f"[DENY] invalid target path: {dst}"

    for name, clean in [("source", clean_src), ("target", clean_dst)]:
        if clean in policy.delete_denied_exact:
            return f"[DENY] cannot move {name}: {clean}"

    if not policy.is_write_allowed(clean_src):
        return f"[DENY] cannot move from: {src}"
    if not policy.is_write_allowed(clean_dst):
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
