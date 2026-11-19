#!/usr/bin/env python3
"""
Tests for memory provenance (#79): durable entries record where they came
from, and the record survives promotion, reinforcement, decay and tidy.

Covers: the ref vocabulary (saku.provenance), dreaming's src field on
MEMORY.md entries, run_dreaming stamping the records it read, tidy
consolidation/merge keeping origins, recall surfacing provenance, and the
prompt-level source lines for the LLM-driven inbox/reflection flows.
No LLM is involved: extraction is stubbed where a run needs it.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import dreaming, provenance, recall, reflection, tidy
from saku import daemon as daemon_mod
from _isolate_logging import isolate_daemon_logging

# Daemon sets up file logging at import time; keep it out of the real vault.
isolate_daemon_logging(daemon_mod.logger)


# ── provenance vocabulary ────────────────────────────────────────


def test_make_ref_and_sanitize():
    assert provenance.make_ref("journal", "2026-08-30") == "journal/2026-08-30"
    assert provenance.make_ref("inbox", "notes/report.md") == "inbox/notes/report.md"
    assert provenance.make_ref("web") == "web"
    # Filenames are user-controlled: spaces and comment terminators cannot survive.
    assert provenance.make_ref("inbox", "my doc--v2.md") == "inbox/my_doc-v2.md"
    assert provenance.sanitize_ref("  journal/2026-08-30  ") == "journal/2026-08-30"
    assert provenance.sanitize_ref("***") == "unknown"


def test_daily_is_a_known_kind():
    """#68: daily notes are a provenance origin like journal/monologue."""
    assert provenance.KIND_DAILY == "daily"
    assert provenance.KIND_DAILY in provenance.KNOWN_KINDS
    assert provenance.make_ref(provenance.KIND_DAILY, "2026-11-18") == "daily/2026-11-18"


def test_join_split_union_refs():
    assert provenance.join_refs(["journal/2026-08-30", "monologue/2026-08-30"]) == (
        "journal/2026-08-30,monologue/2026-08-30"
    )
    assert provenance.join_refs([]) is None
    assert provenance.split_refs("journal/2026-08-30,monologue/2026-08-30") == [
        "journal/2026-08-30",
        "monologue/2026-08-30",
    ]
    assert provenance.split_refs(None) == []
    # Union dedupes and keeps first-seen order.
    assert provenance.union_srcs("journal/2026-08-30", "monologue/2026-08-30,journal/2026-08-30") == (
        "journal/2026-08-30,monologue/2026-08-30"
    )
    assert provenance.union_srcs(None, None) is None


def test_source_line_roundtrip():
    line = provenance.source_line("inbox/report.md")
    assert line == "> source: inbox/report.md"
    assert provenance.has_source_line(f"# Title\n\n{line}\n\nbody\n")
    assert provenance.recorded_ref(f"{line}\n") == "inbox/report.md"
    assert not provenance.has_source_line("# Title\n\nno provenance here\n")
    assert provenance.recorded_ref("nothing\n") is None


def test_extract_src_only_from_comments():
    assert provenance.extract_src("- 2026-08-03: x <!-- s:1 last:2026-08-03 src:web -->") == "web"
    # "src:" in the entry text itself is not provenance.
    assert provenance.extract_src("- 2026-08-03: src:web と書かれた本文") is None
    assert provenance.extract_src("- 2026-08-03: x <!-- s:1 last:2026-08-03 -->") is None


# ── dreaming: the src field on MEMORY.md entries ────────────────


def test_format_and_parse_entry_with_src():
    line = dreaming.format_entry(
        "2026-08-03", "Ownerは麦茶を好む", 2, "2026-08-30", src="journal/2026-08-03"
    )
    assert line == (
        "- 2026-08-03: Ownerは麦茶を好む <!-- s:2 last:2026-08-30 src:journal/2026-08-03 -->"
    )
    parsed = dreaming.parse_entry(line)
    assert parsed["score"] == 2
    assert parsed["last"] == "2026-08-30"
    assert parsed["src"] == "journal/2026-08-03"
    assert parsed["text"] == "Ownerは麦茶を好む"


def test_format_entry_without_src_is_the_legacy_shape():
    line = dreaming.format_entry("2026-08-03", "Ownerは麦茶を好む", 2, "2026-08-30")
    assert line == "- 2026-08-03: Ownerは麦茶を好む <!-- s:2 last:2026-08-30 -->"


def test_parse_entry_legacy_and_src_only():
    # No metadata at all (pre-scoring legacy).
    parsed = dreaming.parse_entry("- 2026-06-01: 昔の項目")
    assert parsed["src"] is None and parsed["score"] is None
    # Score/last metadata, no provenance (pre-#79).
    parsed = dreaming.parse_entry("- 2026-06-01: 昔の項目 <!-- s:2 last:2026-06-02 -->")
    assert parsed["src"] is None and parsed["score"] == 2
    # Provenance without a score (tidy re-render of a scoreless entry).
    parsed = dreaming.parse_entry("- 2026-06-01: 昔の項目 <!-- src:inbox/old.md -->")
    assert parsed["src"] == "inbox/old.md" and parsed["score"] is None
    assert parsed["text"] == "昔の項目"


def test_append_items_records_source():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        added = dreaming.append_items(
            path,
            [("好み・傾向", "Ownerは麦茶を好む")],
            "2026-08-30",
            source="journal/2026-08-30",
        )
        assert added == ["Ownerは麦茶を好む"]
        content = path.read_text(encoding="utf-8")
        assert "src:journal/2026-08-30" in content
        entry_line = next(l for l in content.splitlines() if "麦茶" in l)
        assert dreaming.parse_entry(entry_line)["src"] == "journal/2026-08-30"


def test_append_items_without_source_writes_no_src():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        dreaming.append_items(path, [("好み・傾向", "Ownerは麦茶を好む")], "2026-08-30")
        content = path.read_text(encoding="utf-8")
        assert "src:" not in content


def test_reinforcement_preserves_original_src():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        items = [("好み・傾向", "Ownerは麦茶を好む")]
        dreaming.append_items(path, items, "2026-08-30", source="journal/2026-08-30")
        # Re-promoted a week later from a different record: score/last move,
        # the origin does not.
        dreaming.append_items(path, items, "2026-09-06", source="monologue/2026-09-06")
        content = path.read_text(encoding="utf-8")
        entry_line = next(l for l in content.splitlines() if "麦茶" in l)
        parsed = dreaming.parse_entry(entry_line)
        assert parsed["src"] == "journal/2026-08-30"
        assert parsed["score"] == 3  # 2 (category) + 1 reinforcement
        assert parsed["last"] == "2026-09-06"


def test_review_archive_keeps_src():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        dreaming.append_items(
            path,
            [("進行中プロジェクト", "古いプロジェクトの話")],
            "2026-06-01",
            source="journal/2026-06-01",
        )
        moved = dreaming.review(
            path, today="2026-12-31", ttl_days=90, min_score=2, archive_section="Archive"
        )
        assert moved == ["古いプロジェクトの話"]
        content = path.read_text(encoding="utf-8")
        assert "## Archive" in content
        entry_line = next(l for l in content.splitlines() if "古いプロジェクト" in l)
        assert dreaming.parse_entry(entry_line)["src"] == "journal/2026-06-01"


# ── run_dreaming: provenance is the records the run read ────────


def _run_dreaming_with(tmp: str, *, journal: str, monologue: str) -> str:
    root = Path(tmp)
    (root / "journal").mkdir(parents=True, exist_ok=True)
    (root / "monologue").mkdir(parents=True, exist_ok=True)
    if journal:
        (root / "journal" / "2026-08-30.md").write_text(journal, encoding="utf-8")
    if monologue:
        (root / "monologue" / "2026-08-30.md").write_text(monologue, encoding="utf-8")

    orig_root = dreaming.agent.SAKU_ROOT
    orig_extract = dreaming.extract_items
    dreaming.agent.SAKU_ROOT = root
    dreaming.extract_items = lambda date, j, m: [("好み・傾向", "Ownerは麦茶を好む")]
    try:
        added = dreaming.run_dreaming("2026-08-30")
    finally:
        dreaming.agent.SAKU_ROOT = orig_root
        dreaming.extract_items = orig_extract
    assert added == ["Ownerは麦茶を好む"]
    return (root / "MEMORY.md").read_text(encoding="utf-8")


def test_run_dreaming_journal_only_source():
    with tempfile.TemporaryDirectory() as tmp:
        content = _run_dreaming_with(tmp, journal="今日の記録", monologue="")
        entry_line = next(l for l in content.splitlines() if "麦茶" in l)
        assert dreaming.parse_entry(entry_line)["src"] == "journal/2026-08-30"


def test_run_dreaming_journal_and_monologue_source():
    with tempfile.TemporaryDirectory() as tmp:
        content = _run_dreaming_with(tmp, journal="今日の記録", monologue="独り言")
        entry_line = next(l for l in content.splitlines() if "麦茶" in l)
        # Combined extraction: both records are candidate origins.
        assert dreaming.parse_entry(entry_line)["src"] == "journal/2026-08-30,monologue/2026-08-30"


# ── tidy: consolidation and merge keep origins ──────────────────


def test_tidy_merge_unions_srcs():
    content = (
        "# MEMORY — 長期記憶\n\n## 好み・傾向\n"
        "- 2026-08-03: Ownerは麦茶を好む <!-- s:2 last:2026-08-10 src:journal/2026-08-03 -->\n"
        "- 2026-08-05: Ownerは麦茶を好む <!-- s:3 last:2026-08-20 src:monologue/2026-08-05 -->\n"
    )
    out, stats = tidy.tidy_memory_text(content)
    assert stats["merged"] == 1
    entry_lines = [l for l in out.splitlines() if "麦茶" in l]
    assert len(entry_lines) == 1
    parsed = dreaming.parse_entry(entry_lines[0])
    assert parsed["src"] == "journal/2026-08-03,monologue/2026-08-05"
    assert parsed["score"] == 3 and parsed["last"] == "2026-08-20" and parsed["date"] == "2026-08-03"


def test_tidy_consolidates_leaked_src_comment():
    content = (
        "# MEMORY — 長期記憶\n\n## 重要な学び\n"
        "- 2026-08-03: 何かを学んだ <!-- src:web --> <!-- s:1 last:2026-08-03 -->\n"
    )
    out, _stats = tidy.tidy_memory_text(content)
    entry_line = next(l for l in out.splitlines() if "学んだ" in l)
    assert entry_line == "- 2026-08-03: 何かを学んだ <!-- s:1 last:2026-08-03 src:web -->"


def test_tidy_keeps_src_on_canonical_entry():
    content = (
        "# MEMORY — 長期記憶\n\n## 好み・傾向\n"
        "- 2026-08-03: Ownerは麦茶を好む <!-- s:2 last:2026-08-10 src:journal/2026-08-03 -->\n"
    )
    out, _stats = tidy.tidy_memory_text(content)
    entry_line = next(l for l in out.splitlines() if "麦茶" in l)
    assert dreaming.parse_entry(entry_line)["src"] == "journal/2026-08-03"


# ── recall: provenance is surfaced, legacy is untouched ─────────


def _recall_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "MEMORY.md").write_text(
        "# MEMORY — 長期記憶\n\n## 好み・傾向\n"
        "- 2026-08-20: Ownerは麦茶を好む <!-- s:2 last:2026-08-20 src:journal/2026-08-20 -->\n"
        "- 2026-06-01: Ownerは読書を好む\n",
        encoding="utf-8",
    )
    return root


def test_recall_surfaces_src():
    with tempfile.TemporaryDirectory() as tmp:
        block = recall.recall(_recall_root(tmp), "麦茶について教えて")
        assert "Ownerは麦茶を好む" in block
        assert "出典: journal/2026-08-20" in block


def test_recall_legacy_entry_has_no_src_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        block = recall.recall(_recall_root(tmp), "読書について教えて")
        assert "Ownerは読書を好む" in block
        assert "出典" not in block


# ── LLM-driven flows: the prompts carry the source line ─────────


def test_inbox_prompt_records_the_document():
    prompt = daemon_mod.build_inbox_prompt("notes/report.md", "本文")
    assert "> source: inbox/notes/report.md" in prompt
    assert "notes/report.md" in prompt
    # A user-controlled filename is sanitized before it reaches the prompt.
    prompt = daemon_mod.build_inbox_prompt("my doc.md", "本文")
    assert "> source: inbox/my_doc.md" in prompt


def test_reflection_prompt_records_the_journal():
    prompt = reflection.build_reflection_prompt("2026-08-30", "日記", "独り言")
    assert "> source: journal/2026-08-30" in prompt


def test_unsourced_writes_flags_only_new_files_without_source():
    with tempfile.TemporaryDirectory() as tmp:
        principles = Path(tmp) / "principles"
        principles.mkdir()
        legacy = principles / "legacy.md"
        legacy.write_text("# 昔の原則\n", encoding="utf-8")
        before = daemon_mod._snapshot_markdown([principles])

        # An append to a pre-existing file is out of scope (not flagged).
        legacy.write_text("# 昔の原則\n追記\n", encoding="utf-8")
        # A new file with a source line is fine.
        (principles / "sourced.md").write_text(
            "> source: inbox/report.md\n\n# 原則\n", encoding="utf-8"
        )
        # A new file without one is flagged.
        (principles / "unsourced.md").write_text("# 原則\n", encoding="utf-8")

        missing = daemon_mod._unsourced_writes(before, [principles])
        assert [p.name for p in missing] == ["unsourced.md"]


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} provenance tests PASSED!")


if __name__ == "__main__":
    run_tests()
