"""Growth status: a read-only summary of memory / wiki growth (no LLM, no writes).

``saku status`` exists so growth is visible at a glance: how many durable memories
exist, how many carry importance scores, what decayed into the archive section, and
how big the knowledge base is. It reads files only -- nothing is ever modified.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import re

_DATE_STEM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def memory_stats(memory_root: Path, archive_section: str = "Archive") -> dict:
    """Count MEMORY.md entries: total, scored, archived, and the score spread."""
    from saku.dreaming import parse_entry

    stats = {"total": 0, "scored": 0, "archived": 0, "scores": []}
    path = memory_root / "MEMORY.md"
    if not path.exists():
        return stats

    in_archive = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_archive = line[3:].strip() == archive_section
            continue
        parsed = parse_entry(line)
        if not parsed:
            continue
        stats["total"] += 1
        if in_archive:
            stats["archived"] += 1
        if parsed["score"] is not None:
            stats["scored"] += 1
            stats["scores"].append(parsed["score"])
    return stats


def _count_md(d: Path) -> int:
    return len(list(d.rglob("*.md"))) if d.is_dir() else 0


def _latest_date(d: Path) -> str:
    if not d.is_dir():
        return "-"
    dates = sorted(p.stem for p in d.glob("*.md") if _DATE_STEM_RE.match(p.stem))
    return dates[-1] if dates else "-"


def status_lines(memory_root: Path, wiki_root: Path | None = None) -> list[str]:
    """Human-readable growth summary (counts only; no personal content)."""
    stats = memory_stats(memory_root)
    scores = stats["scores"]
    score_note = (
        f"min {min(scores)} / max {max(scores)} / avg {sum(scores) / len(scores):.1f}"
        if scores
        else "no scored entries yet"
    )

    lines = [
        f"MEMORY.md      : {stats['total']} entries "
        f"({stats['scored']} scored, {stats['archived']} archived)",
        f"scores         : {score_note}",
    ]
    if wiki_root is not None:
        lines.append(f"wiki           : {_count_md(wiki_root)} note(s)")
    lines.append(f"principles     : {_count_md(memory_root / 'principles')} file(s)")
    lines.append(f"skills         : {_count_md(memory_root / 'skills')} file(s)")
    lines.append(f"children       : {_count_md(memory_root / 'children')} file(s)")
    lines.append(
        f"journal        : {_count_md(memory_root / 'journal')} day(s), "
        f"latest {_latest_date(memory_root / 'journal')}"
    )
    lines.append(
        f"monologue      : {_count_md(memory_root / 'monologue')} day(s), "
        f"latest {_latest_date(memory_root / 'monologue')}"
    )
    return lines


def killswitch_line(memory_root: Path) -> str:
    """One line on the kill switch (#76): the stopped state must be
    confirmable at a glance, here as everywhere else."""
    from saku import config as cfg_mod
    from saku import killswitch

    cfg, _base = cfg_mod.load_config()
    flag = cfg_mod.resolve_killswitch_flag(cfg, memory_root)
    return f"kill switch    : {killswitch.describe(flag)}"


def _resolve_roots() -> tuple[Path, Path]:
    from saku import config as cfg_mod

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    return memory_root, cfg_mod.resolve_wiki_root(cfg, base, memory_root)


def main(argv: list[str] | None = None) -> int:
    memory_root, wiki_root = _resolve_roots()
    print(f"SAKU status ({memory_root})")
    for line in status_lines(memory_root, wiki_root):
        print(f"  {line}")
    print(f"  {killswitch_line(memory_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
