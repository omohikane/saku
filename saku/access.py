"""Read-only access to the Owner's vault outside the memory root (#73 follow-up).

The tool path policy deliberately keeps writes inside the memory root, but
reads had no equivalent: `[paths] read_allowed` is memory-relative, and since
#124 any `../` path is refused, so `READ_FILE ../01_Notes/...` fails even
though the prompt claimed the whole vault was readable.

This module adds an explicit, minimal scope for *reads*:

```toml
[access]
vault_root = ""                      # "" = the vault that owns the memory root
vault_read = ["01_Notes/", "02_Reference/"]   # empty = memory only
```

Only the listed prefixes are visible, only for reads, and every candidate is
resolved and checked to stay inside the vault (symlinks included). Writes are
untouched: nothing here grants a write.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

from saku import config as saku_config


@dataclass
class AccessConfig:
    vault_root: str = ""
    vault_read: list[str] = field(default_factory=list)
    max_file_chars: int = 4000
    max_search_results: int = 10


def load_access_config(cfg: dict) -> AccessConfig:
    a = cfg.get("access", {})
    return AccessConfig(
        vault_root=str(a.get("vault_root", "") or ""),
        vault_read=[str(p) for p in a.get("vault_read", []) if str(p).strip()],
        max_file_chars=int(a.get("max_file_chars", 4000)),
        max_search_results=int(a.get("max_search_results", 10)),
    )


_cfg_cache: dict | None = None


def cached_cfg() -> dict:
    """Load config once per process (same lifetime as the path policy cache)."""
    global _cfg_cache
    if _cfg_cache is None:
        cfg, _ = saku_config.load_config()
        _cfg_cache = cfg
    return _cfg_cache


def reset_cache() -> None:
    global _cfg_cache
    _cfg_cache = None


def vault_root(memory_root: Path, cfg: dict | None = None) -> Path:
    acfg = load_access_config(cfg if cfg is not None else cached_cfg())
    value = saku_config.expand_env(acfg.vault_root).strip()
    if value:
        p = Path(value).expanduser()
        return (p if p.is_absolute() else Path(memory_root) / p).resolve()
    # <vault>/_saku/memory -> <vault>
    return Path(memory_root).resolve().parent.parent


def _clean(raw: str) -> str | None:
    """Lexically normalize a vault-relative path; None when it is unsafe."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith("/") or text.startswith('"'):
        return None
    rel = posixpath.normpath(text)
    if rel in (".", "..") or rel.startswith("../"):
        return None
    return rel


def allowed_roots(memory_root: Path, cfg: dict | None = None) -> list[Path]:
    """Resolved directories that reads may reach (empty when not configured)."""
    acfg = load_access_config(cfg if cfg is not None else cached_cfg())
    root = vault_root(memory_root, cfg)
    out = []
    for entry in acfg.vault_read:
        rel = _clean(entry)
        if rel is None:
            continue
        out.append((root / rel).resolve())
    return out


def resolve_vault_path(
    memory_root: Path, raw: str, cfg: dict | None = None
) -> Path | None:
    """Return an absolute path when ``raw`` is inside an allowed read root."""
    rel = _clean(raw)
    if rel is None:
        return None
    acfg = load_access_config(cfg if cfg is not None else cached_cfg())
    if not acfg.vault_read:
        return None
    root = vault_root(memory_root, cfg)
    # Prefix check is lexical first (so a file that does not exist is still
    # classified correctly); the resolved check below stops symlink escapes.
    if not any(
        rel == e or rel.startswith(e + "/")
        for e in (
            _clean(entry) or ""
            for entry in acfg.vault_read
        )
    ):
        return None
    if any(part.startswith(".") for part in rel.split("/")):
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not any(
        candidate == allowed or allowed in candidate.parents
        for allowed in allowed_roots(memory_root, cfg)
    ):
        return None
    return candidate


def search_roots(memory_root: Path, cfg: dict | None = None) -> list[Path]:
    """Roots SEARCH_NOTES may scan (memory first, then the allowed vault dirs)."""
    roots = [Path(memory_root)]
    roots.extend(p for p in allowed_roots(memory_root, cfg) if p.is_dir())
    return roots


def describe(memory_root: Path, cfg: dict | None = None) -> str:
    """One prompt-ready line describing what reads are allowed."""
    acfg = load_access_config(cfg if cfg is not None else cached_cfg())
    if not acfg.vault_read:
        return "Memory root only（Vault は読めない。許可するには [access] vault_read を設定）"
    dirs = ", ".join(acfg.vault_read)
    return f"Memory root + 読み取り専用の Vault ディレクトリ（{dirs}）"
