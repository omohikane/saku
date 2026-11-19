#!/usr/bin/env python3
"""Compare two longitudinal snapshots and report how SAKU's state changed.

Usage: python evals/longitudinal/compare.py snapshot_before.json snapshot_after.json

Reports deltas for memory size, per-section item counts, and file counts, so you
can see whether SAKU is growing (and what grew) between two points in time.

The comparison logic lives in ``saku.growth`` (issue #103), shared with the
weekly growth digest the daemon maintains; this script is the manual route.
"""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku.growth import compare_snapshots as compare  # noqa: E402  (re-exported)


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
