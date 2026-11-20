#!/usr/bin/env python3
"""Tests for SEARCH_NOTES covering correspondence logs (#120 follow-up)."""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku.tools import search_notes


def _root(tmp: Path) -> Path:
    root = tmp / "memory"
    (root / "journal").mkdir(parents=True)
    (root / "mail" / "inbox").mkdir(parents=True)
    (root / "journal" / "2026-11-19.md").write_text("今日は静かな日", encoding="utf-8")
    (root / "mail" / "talk.log").write_text(
        "[2026-11-19 21:00:00] [talk] 小夜: こんばんは\n", encoding="utf-8"
    )
    (root / "mail" / "inbox" / "0001.md").write_text(
        "---\nfrom: 小夜\nid: 0001\n---\n手紙の本文\n", encoding="utf-8"
    )
    return root


def test_search_finds_a_name_in_the_talk_log():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        out = search_notes.run(root, body="小夜")
        assert "Found" in out, out
        assert "mail/talk.log" in out or "talk.log" in out
        assert "mail/inbox/0001.md" in out or "0001.md" in out


def test_search_still_finds_markdown():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        out = search_notes.run(root, body="静かな日")
        assert "journal/2026-11-19.md" in out, out


def test_search_is_case_insensitive_and_reports_no_match():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        assert "No matches found" in search_notes.run(root, body="存在しない語")
        assert "Found" in search_notes.run(root, body="小夜")


def test_hidden_files_are_not_searched():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        (root / ".git").mkdir()
        (root / ".git" / "secret.md").write_text("hiddenword", encoding="utf-8")
        out = search_notes.run(root, body="hiddenword")
        assert "No matches found" in out, out


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} search-notes tests PASSED!")


if __name__ == "__main__":
    run_tests()
