#!/usr/bin/env python3
"""Capture a longitudinal snapshot of SAKU's growth state.

Usage: python evals/longitudinal/snapshot.py [output.json]

Snapshots are JSON files capturing the size/structure of the memory at a point in
time. Run periodically (e.g. weekly) and compare with ``compare.py`` to measure
whether SAKU is actually growing (more knowledge, more consistent memory) over time.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _memory_root() -> Path:
    from saku import core

    return core.MEMORY_ROOT


def _count_md(d: Path) -> int:
    return len(list(d.glob("*.md"))) if d.is_dir() else 0


def _section_items(content: str) -> dict[str, int]:
    """Count list items under each ## section in a Markdown file."""
    counts: dict[str, int] = {}
    current = None
    for line in content.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            counts[current] = 0
        elif current and line.strip().startswith("- "):
            counts[current] = counts.get(current, 0) + 1
    return counts


def capture(memory_root: Path) -> dict:
    memory = memory_root / "MEMORY.md"
    meta = memory_root / "meta.md"
    mem_content = memory.read_text(encoding="utf-8") if memory.exists() else ""
    meta_content = meta.read_text(encoding="utf-8") if meta.exists() else ""

    wiki_notes = [p for p in (memory_root / "wiki").glob("*.md")] if (memory_root / "wiki").is_dir() else []

    return {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "memory": {
            "chars": len(mem_content),
            "items_by_section": _section_items(mem_content),
        },
        "meta": {
            "chars": len(meta_content),
            "items_by_section": _section_items(meta_content),
        },
        "files": {
            "principles": _count_md(memory_root / "principles"),
            "skills": _count_md(memory_root / "skills"),
            "wiki_notes": len(wiki_notes),
            "journal": _count_md(memory_root / "journal"),
            "monologue": _count_md(memory_root / "monologue"),
            "children": _count_md(memory_root / "children"),
            "tools": _count_md(memory_root / "tools"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[0]) if args else Path("snapshot.json")
    snapshot = capture(_memory_root())
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
