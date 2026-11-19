#!/usr/bin/env python3
"""Capture a longitudinal snapshot of SAKU's growth state.

Usage: python evals/longitudinal/snapshot.py [output.json]

Snapshots are JSON files capturing the size/structure of the memory at a point in
time. Run periodically (e.g. weekly) and compare with ``compare.py`` to measure
whether SAKU is actually growing (more knowledge, more consistent memory) over time.

The capture logic lives in ``saku.growth`` (issue #103): the daemon now takes
the same snapshot weekly on its own, and both routes must produce the exact
same format or their snapshots would not be comparable. This script remains
the manual route (e.g. an ad-hoc snapshot before a big change).
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku.growth import capture_snapshot as capture  # noqa: E402  (re-exported)


def _memory_root() -> Path:
    from saku import core

    return core.MEMORY_ROOT


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = Path(args[0]) if args else Path("snapshot.json")
    snapshot = capture(_memory_root())
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
