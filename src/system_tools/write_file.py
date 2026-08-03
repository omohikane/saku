"""Write a file within the memory root (restricted paths only, policy from config)."""

from pathlib import Path

from saku.config import get_path_policy


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    policy = get_path_policy()

    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    if path in policy.write_denied_exact:
        return f"[DENY] {path} cannot be overwritten with WRITE_FILE. Use APPEND_FILE to add entries."

    if not policy.is_write_allowed(path):
        return f"[DENY] cannot write to: {path}"

    target = (base / path).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    return f"[OK] wrote {path} ({len(body)} bytes)"
