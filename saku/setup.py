"""Initialize a SAKU memory root (or an Obsidian vault) structure.

Creates the standard directories and template files so that a freshly cloned
repo or a new vault is usable by SAKU out of the box.

Usage: saku setup [path]
- With a path: initialize that directory (absolute or relative).
- Without a path: initialize the configured [memory] root.
"""

import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

MEMORY_DIRS = [
    "journal",
    "monologue",
    "principles",
    "blog",
    "skills",
    "children",
    "study",
    "tools",
    "state",
    "identity",
    "wiki",
]

CHAT_HEADER = """# SAKU Chat — 書面対話ノート

ここにメッセージを書いて保存すると、SAKUが返信します。

---

**使い方**
- メッセージを末尾に追記し、最後に `>` を入力して保存してください。
- （例： `こんにちは。最近どう？ >`）
- `>` を検知すると、SAKUが自動的にヘッダーを整理して返信を追記します。

---
"""

REQUEST_HEADER = """# Request List — Owner へのお願いリスト

- ブログ公開承認・新ツール承認・その他Owner確認が必要なことはここに追記する。
- 形式: `- [ ] 依頼内容 (作成日: YYYY-MM-DD)`
"""


def init_memory(memory_root: Path) -> None:
    """Create the standard SAKU memory structure in memory_root."""
    memory_root.mkdir(parents=True, exist_ok=True)

    for d in MEMORY_DIRS:
        (memory_root / d).mkdir(parents=True, exist_ok=True)

    # meta.md (self-model) from template if absent
    meta = memory_root / "meta.md"
    if not meta.exists():
        template = _REPO_ROOT / "memory" / "meta.template.md"
        if template.exists():
            shutil.copy(template, meta)
        else:
            meta.write_text("# SAKU Meta\n\n## 最近の出来事\n\n- 初期状態\n", encoding="utf-8")

    # soul.md (core identity) — keep any existing content
    soul = memory_root / "identity" / "soul.md"
    if not soul.exists():
        soul_template = _REPO_ROOT / "sample" / "identity" / "soul.md"
        if soul_template.exists():
            shutil.copy(soul_template, soul)

    # genome.md (user-authored personality master) — the vault is the master location
    _copy_genome(memory_root)

    # chat.md and request_list.md (never overwrite existing content)
    chat = memory_root / "chat.md"
    if not chat.exists():
        chat.write_text(CHAT_HEADER, encoding="utf-8")

    req = memory_root / "request_list.md"
    if not req.exists():
        req.write_text(REQUEST_HEADER, encoding="utf-8")


def _copy_genome(memory_root: Path) -> None:
    """Ensure memory/identity/genome.md exists as the vault master.

    The repo's identity/genome.md is only a sample; the personal master lives in
    the vault. Source priority:
    1. existing memory/identity/genome.md (keep as-is)
    2. vault root identity/genome.md (e.g. Obsidian layout: _saku/identity/)
    3. repo identity/genome.md (maintainer's copy if present)
    4. repo identity/genome.template.md (fresh clone)
    """
    target = memory_root / "identity" / "genome.md"
    if target.exists():
        return
    sources = [
        memory_root.parent / "identity" / "genome.md",
        _REPO_ROOT / "identity" / "genome.md",
        _REPO_ROOT / "identity" / "genome.template.md",
    ]
    for src in sources:
        if src.exists():
            shutil.copy(src, target)
            return


def main(argv: list[str] | None = None) -> int:
    from saku import config as saku_config

    args = list(sys.argv[1:] if argv is None else argv)
    target = args[0] if args else None

    cfg, config_base = saku_config.load_config()
    if target:
        memory_root = Path(target).expanduser()
        if not memory_root.is_absolute():
            memory_root = (config_base / memory_root).resolve()
    else:
        memory_root = saku_config.resolve_memory_root(cfg, config_base)

    init_memory(memory_root)
    print(f"[OK] SAKU memory root ready: {memory_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
