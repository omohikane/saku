"""``saku do`` — route a natural-language request to a known command (#72).

The point is not to guess well; it is to guess *safely*. Matching is plain
keyword work (no LLM call), only an allow-list of commands can be reached, and
an unrecognised request returns "no match" so the caller can fall back to chat.
Every routed command keeps its own dry-run default: ``--apply`` is forwarded
only when the Owner asked for it.
"""

from __future__ import annotations

import re

# Order matters: the first rule that matches wins. Japanese, romaji and English
# are all listed because the Owner types all three.
#
# Each entry: (command, [(pattern, weight)]). Kept deliberately small — a rule
# is a promise that this phrase means this command, and a false match is worse
# than falling back to chat.
RULES: list[tuple[str, list[str]]] = [
    (
        "sort",
        [
            r"クリッピング",
            r"くりっぴんぐ",
            r"clip+ing",
            r"\bsort\b",
            r"ぶんるい",
            r"bunrui",
            r"分類",
        ],
    ),
    (
        "tidy",
        [
            r"\btidy\b",
            r"掃除",
            r"そうじ",
            r"しょうじ",
            r"メモリ.*整理",
            r"重複.*(直|消|まとめ)",
        ],
    ),
    (
        "status",
        [
            r"\bstatus\b",
            r"状態",
            r"成長.*(確認|見|知り)",
            r"調子",
        ],
    ),
]

ALLOWED = {name for name, _ in RULES}


def resolve(text: str) -> str | None:
    """Return the command a request names, or None when nothing matches."""
    if not text or not text.strip():
        return None
    lowered = text.strip().lower()
    for name, patterns in RULES:
        for pattern in patterns:
            if re.search(pattern, lowered, re.IGNORECASE):
                return name
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="saku do",
        description="Run a known command from a natural-language request (dry-run default)",
    )
    parser.add_argument("request", nargs="+", help="what to do, in words")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="forward --apply to the routed command (writes only then)",
    )
    args = parser.parse_args(argv)

    text = " ".join(args.request)
    command = resolve(text)
    if command is None:
        print(f"[?] no command matched: {text!r}")
        print("    known: " + ", ".join(sorted(ALLOWED)))
        print("    nothing was run; ask SAKU in chat instead")
        return 2

    print(f"[*] {command}: {text}")
    if command == "sort":
        from saku.sort import main as sort_main

        return sort_main(["--apply"] if args.apply else [])
    if command == "tidy":
        from saku.tidy import main as tidy_main

        return tidy_main(["--apply"] if args.apply else [])
    if command == "status":
        from saku.status import main as status_main

        return status_main([])

    # Unreachable while ALLOWED mirrors RULES, but never fall through silently.
    print(f"[!] {command} has no runner")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
