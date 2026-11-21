"""Read a file within the memory root (path policy from config)."""

from pathlib import Path

from saku import access
from saku.config import get_path_policy, normalize_memory_path

_CODE_ROOT = Path(__file__).resolve().parent.parent


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    # #73 follow-up: an explicitly configured read-only slice of the vault.
    vault_target = access.resolve_vault_path(base, path)
    if vault_target is not None:
        if not vault_target.exists():
            return f"[ERROR] not found: {path}"
        if vault_target.is_dir():
            return f"[ERROR] is a directory: {path} (use LIST_DIR)"
        try:
            content = vault_target.read_text(encoding="utf-8")
        except OSError as e:
            return f"[ERROR] {e}"
        limit = access.load_access_config(access.cached_cfg()).max_file_chars
        if len(content) > limit:
            content = content[:limit] + f"\n\n[... truncated at {limit} chars]"
        return content

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
