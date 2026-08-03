#!/usr/bin/env python3
"""Compare two longitudinal snapshots and report how SAKU's state changed.

Usage: python evals/longitudinal/compare.py snapshot_before.json snapshot_after.json

Reports deltas for memory size, per-section item counts, and file counts, so you
can see whether SAKU is growing (and what grew) between two points in time.
"""

import json
import sys
from pathlib import Path


def _flatten_sections(sections: dict[str, int]) -> dict[str, int]:
    return {f"{k} ({v} items)": v for k, v in sections.items()}


def compare(before: dict, after: dict) -> list[str]:
    lines: list[str] = []
    b, a = before, after
    lines.append(f"captured: {b.get('captured_at')} -> {a.get('captured_at')}")
    lines.append("")

    # memory / meta size
    for key, label in (("memory", "MEMORY.md"), ("meta", "meta.md")):
        db = b.get(key, {}).get("chars", 0)
        da = a.get(key, {}).get("chars", 0)
        lines.append(f"{label}: {db} -> {da} chars ({da - db:+d})")
        # per-section item deltas
        bs = b.get(key, {}).get("items_by_section", {})
        as_ = a.get(key, {}).get("items_by_section", {})
        for section in sorted(set(bs) | set(as_)):
            delta = as_.get(section, 0) - bs.get(section, 0)
            if delta:
                lines.append(f"  {section}: {bs.get(section, 0)} -> {as_.get(section, 0)} ({delta:+d})")
    lines.append("")

    # file counts
    bf, af = b.get("files", {}), a.get("files", {})
    for key in sorted(set(bf) | set(af)):
        db, da = bf.get(key, 0), af.get(key, 0)
        if da != db:
            lines.append(f"{key}: {db} -> {da} ({da - db:+d})")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("Usage: compare.py snapshot_before.json snapshot_after.json")
        return 1
    before = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    after = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    for line in compare(before, after):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
