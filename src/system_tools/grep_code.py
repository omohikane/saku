"""Search Python source files in system_tools/ and user tools/."""

import os
import re as re_module
from pathlib import Path

MAX_RESULTS = 20
MAX_LINE_LENGTH = 150

SKIP_SUFFIXES = (".pyc",)
SKIP_DIRS = ("__pycache__",)


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    query = body.strip()
    if not query:
        return "[ERROR] search query is empty"

    pattern_str = kwargs.get("pattern", "") or query
    try:
        pattern = re_module.compile(pattern_str, re_module.IGNORECASE)
    except re_module.error as e:
        return f"[ERROR] invalid regex: {e}"

    code_root = Path(__file__).resolve().parent.parent  # _saku/src/
    vault_root = code_root.parent                       # _saku/

    search_targets = [
        (code_root / "system_tools", vault_root),
        (base / "tools", base),
    ]

    results = []
    for search_path, display_base in search_targets:
        if not search_path.is_dir():
            continue

        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            for fname in files:
                if not fname.endswith(".py") or fname.endswith(SKIP_SUFFIXES):
                    continue

                fpath = Path(root) / fname
                try:
                    rel = fpath.relative_to(display_base)
                except ValueError:
                    continue

                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                for lineno, line in enumerate(content.splitlines(), 1):
                    if pattern.search(line):
                        display = line.strip()[:MAX_LINE_LENGTH]
                        results.append(f"  {rel}:{lineno}  {display}")
                        if len(results) >= MAX_RESULTS:
                            break
                if len(results) >= MAX_RESULTS:
                    break
            if len(results) >= MAX_RESULTS:
                break

    if not results:
        return f"No matches found for: {pattern_str}"

    header = f"Found {len(results)} match(es) for '{pattern_str}':"
    return "\n".join([header] + results[:MAX_RESULTS])
