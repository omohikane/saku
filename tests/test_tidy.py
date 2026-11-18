#!/usr/bin/env python3
"""
Tests for saku.tidy (deterministic cleanup of MEMORY.md and the wiki, #70).

All file-level tests run inside temporary directories; nothing here ever
touches a real memory root.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import tidy


def _make_roots(tmp: str) -> tuple[Path, Path]:
    root = Path(tmp)
    wiki = root / "wiki"
    wiki.mkdir(parents=True)
    return root, wiki


def _note(title: str, body: str, created: str = "2026-01-01", tags: str = "") -> str:
    return (
        f"# {title}\n\n"
        f"> tags: {tags}\n"
        f"> created: {created}\n"
        f"> updated: {created}\n"
        f"\n---\n\n{body}\n"
    )


# ── MEMORY.md ────────────────────────────────────────────


def test_memory_metadata_cleanup_and_consolidation():
    content = (
        "# MEMORY — 長期記憶\n\n"
        "## 好み・傾向\n"
        "- 2026-10-22: 本文です <!-- s:1 --> <!-- s:3 last:2026-10-22 -->\n"
        "- 2026-10-23: 別の項目 <!-- s:2 last:2026-10-25 -->\n"
    )
    out, stats = tidy.tidy_memory_text(content)
    assert stats["cleaned"] == 1
    # leaked comments are gone from the text; score=max, last=newest, one comment
    assert "- 2026-10-22: 本文です <!-- s:3 last:2026-10-22 -->" in out
    assert "<!-- s:1 -->" not in out
    # an already-clean entry is left byte-identical
    assert "- 2026-10-23: 別の項目 <!-- s:2 last:2026-10-25 -->" in out


def test_memory_duplicate_merge_rules():
    content = (
        "# MEMORY — 長期記憶\n\n"
        "## 重要な学び\n"
        "- 2026-08-10: 同じ学び <!-- s:2 last:2026-08-10 -->\n"
        "- 2026-08-03: 同じ学び <!-- s:5 last:2026-08-20 -->\n"
        "- 2026-08-05: 違う学び\n"
        "\n## 好み・傾向\n"
        "- 2026-08-04: 同じ学び\n"  # same text, different section: kept
    )
    out, stats = tidy.tidy_memory_text(content)
    assert stats["merged"] == 1
    lines = [ln for ln in out.splitlines() if "同じ学び" in ln]
    assert len(lines) == 2  # one merged + one in the other section
    # oldest date, max score, newest last; first occurrence is the merged one
    assert lines[0] == "- 2026-08-03: 同じ学び <!-- s:5 last:2026-08-20 -->"
    assert lines[1] == "- 2026-08-04: 同じ学び"
    # merged entry stays at the first occurrence's position (before 違う学び)
    assert out.index("同じ学び") < out.index("違う学び")


def test_memory_duplicate_merge_with_legacy_entry():
    content = (
        "## 好み・傾向\n"
        "- 2026-08-10: レガシーと重複\n"
        "- 2026-08-03: レガシーと重複 <!-- s:4 last:2026-08-09 -->\n"
    )
    out, stats = tidy.tidy_memory_text(content)
    assert stats["merged"] == 1
    # metadata is pulled from the duplicate that has it; nothing is fabricated
    assert "- 2026-08-03: レガシーと重複 <!-- s:4 last:2026-08-09 -->" in out
    assert out.count("レガシーと重複") == 1


def test_memory_blank_line_compression():
    content = "# MEMORY\n\n\n\n\n## A\n- 2026-08-03: x\n\n\n## B\n"
    out, stats = tidy.tidy_memory_text(content)
    assert stats["blank_runs"] == 1
    assert "\n\n\n\n" not in out
    assert "# MEMORY\n\n## A" in out
    # a run of exactly 2 blank lines is left alone
    content2 = "# MEMORY\n\n\n## A\n"
    out2, stats2 = tidy.tidy_memory_text(content2)
    assert stats2["blank_runs"] == 0
    assert out2 == content2


def test_memory_idempotent():
    content = (
        "# MEMORY — 長期記憶\n\n"
        "## 好み・傾向\n"
        "- 2026-10-22: 本文 <!-- s:1 --> <!-- s:3 last:2026-10-22 -->\n"
        "- 2026-08-10: 重複 <!-- s:2 last:2026-08-10 -->\n"
        "- 2026-08-03: 重複 <!-- s:5 last:2026-08-20 -->\n"
    )
    once, _ = tidy.tidy_memory_text(content)
    twice, stats2 = tidy.tidy_memory_text(once)
    assert twice == once
    assert stats2 == {"cleaned": 0, "merged": 0, "blank_runs": 0} or (
        stats2["cleaned"] == 0 and stats2["merged"] == 0 and stats2["blank_runs"] == 0
    )


# ── wiki notes ───────────────────────────────────────────


def test_sanitize_note_removes_tool_syntax_keeps_links():
    content = _note(
        "予測符号化",
        "本文 [[自由エネルギー原理]] へのリンク。\n"
        '[[WRITE_FILE path="x.md"]]\n'
        "書く内容\n"
        "[[END]]\n"
        "読んだ内容。\n"
        '[[READ_FILE path="meta.md"]]\n'
        "[SEARCH_NOTES query=記憶]\n"
        "[[MEMORY]] もリンクとして残る。",
    )
    out = tidy.sanitize_note_text(content)
    assert "[[自由エネルギー原理]]" in out
    assert "[[MEMORY]]" in out
    assert "READ_FILE" not in out
    assert "WRITE_FILE" not in out
    assert "SEARCH_NOTES" not in out
    assert "[[END]]" not in out
    assert "読んだ内容。" in out
    # a complete tool block is removed together with its body argument
    assert "書く内容" not in out


def test_note_line_dedupe():
    content = _note("掃除", "一行目\n重複行\n二行目\n重複行\n\n重複行")
    out, removed = tidy.dedupe_note_lines(content)
    assert removed == 2
    assert out.count("重複行") == 1
    # blank lines are structural and stay
    assert "\n\n" in out


def test_normalize_title():
    assert tidy.normalize_title("Predictive Coding") == tidy.normalize_title("predictive-coding")
    assert tidy.normalize_title("予測　符号化") == tidy.normalize_title("予測符号化")
    assert tidy.normalize_title("Ｃ＋＋") == tidy.normalize_title("C++")
    assert tidy.normalize_title("メモリ管理") != tidy.normalize_title("メモリ")


# ── run_tidy (temp dirs only) ────────────────────────────


def test_run_tidy_dry_run_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root, wiki = _make_roots(tmp)
        mem = root / "MEMORY.md"
        mem.write_text(
            "# MEMORY\n\n## 好み・傾向\n- 2026-10-22: 本文 <!-- s:1 --> <!-- s:3 last:2026-10-22 -->\n",
            encoding="utf-8",
        )
        note = wiki / "predictive-coding.md"
        note.write_text(_note("Predictive Coding", "同じ行\n同じ行"), encoding="utf-8")
        before_mem = mem.read_text(encoding="utf-8")
        before_note = note.read_text(encoding="utf-8")

        report = tidy.run_tidy(root, wiki)  # default: dry-run
        assert report["applied"] is False
        assert report["memory"]["changed"] is True
        assert mem.read_text(encoding="utf-8") == before_mem
        assert note.read_text(encoding="utf-8") == before_note
        assert not (root / "state").exists()
        assert not (wiki / ".archive").exists()


def test_run_tidy_apply_creates_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root, wiki = _make_roots(tmp)
        mem = root / "MEMORY.md"
        original = (
            "# MEMORY\n\n## 好み・傾向\n- 2026-10-22: 本文 <!-- s:1 --> <!-- s:3 last:2026-10-22 -->\n"
        )
        mem.write_text(original, encoding="utf-8")
        note = wiki / "note-a.md"
        note_original = _note("Note A", "同じ行\n同じ行")
        note.write_text(note_original, encoding="utf-8")

        report = tidy.run_tidy(root, wiki, apply=True, timestamp="20260101-000000")
        assert report["applied"] is True
        backup = root / "state" / "backup" / "20260101-000000"
        assert report["backup_dir"] == str(backup)
        # pre-change bytes are preserved in the backup
        assert (backup / "MEMORY.md").read_text(encoding="utf-8") == original
        assert (backup / "wiki" / "note-a.md").read_text(encoding="utf-8") == note_original
        # and the live files were actually tidied
        assert "<!-- s:1 -->" not in mem.read_text(encoding="utf-8")
        assert note.read_text(encoding="utf-8").count("同じ行") == 1


def test_wiki_title_collision_merge_and_archive():
    with tempfile.TemporaryDirectory() as tmp:
        root, wiki = _make_roots(tmp)
        keeper = wiki / "predictive-coding.md"
        keeper.write_text(
            _note("Predictive Coding", "共有の行\nキーパーだけの行", created="2026-01-01"),
            encoding="utf-8",
        )
        donor = wiki / "予測符号化-note.md"
        donor.write_text(
            _note("predictive-coding", "共有の行\nドナーだけの行", created="2026-02-01"),
            encoding="utf-8",
        )
        report = tidy.run_tidy(root, wiki, apply=True, timestamp="20260101-000000")
        assert report["wiki"]["merged"] == [("予測符号化-note.md", "predictive-coding.md")]
        # donor moved, never deleted; keeper gained only the missing line
        assert not donor.exists()
        archived = wiki / ".archive" / "予測符号化-note.md"
        assert archived.exists()
        assert "ドナーだけの行" in archived.read_text(encoding="utf-8")
        merged = keeper.read_text(encoding="utf-8")
        assert merged.count("共有の行") == 1
        assert "キーパーだけの行" in merged
        assert "ドナーだけの行" in merged
        # index regenerated without the donor
        index = (wiki / "_index.md").read_text(encoding="utf-8")
        assert "[[predictive-coding]]" in index
        assert "予測符号化-note" not in index


def test_retire_dumps_only_with_flag():
    with tempfile.TemporaryDirectory() as tmp:
        root, wiki = _make_roots(tmp)
        dump = wiki / "2026-11-18-learnings.md"
        dump.write_text(_note("2026-11-18 Learnings", "その日のまとめ"), encoding="utf-8")
        concept = wiki / "concept.md"
        concept.write_text(_note("Concept", "概念"), encoding="utf-8")

        report = tidy.run_tidy(root, wiki, apply=True, timestamp="20260101-000000")
        assert report["wiki"]["retired"] == []
        assert dump.exists()

        report = tidy.run_tidy(
            root, wiki, apply=True, retire_dumps=True, timestamp="20260101-000001"
        )
        assert report["wiki"]["retired"] == ["2026-11-18-learnings.md"]
        assert not dump.exists()
        assert (wiki / ".archive" / "2026-11-18-learnings.md").exists()
        assert concept.exists()


def test_archive_name_collision_gets_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        root, wiki = _make_roots(tmp)
        archive = wiki / ".archive"
        archive.mkdir()
        (archive / "2026-11-18-learnings.md").write_text("既存のアーカイブ", encoding="utf-8")
        dump = wiki / "2026-11-18-learnings.md"
        dump.write_text(_note("2026-11-18 Learnings", "新しいダンプ"), encoding="utf-8")
        tidy.run_tidy(root, wiki, apply=True, retire_dumps=True, timestamp="20260101-000000")
        # the pre-existing archive file is never overwritten
        assert (archive / "2026-11-18-learnings.md").read_text(encoding="utf-8") == "既存のアーカイブ"
        assert (archive / "2026-11-18-learnings-2.md").exists()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} tidy tests PASSED!")


if __name__ == "__main__":
    run_tests()
