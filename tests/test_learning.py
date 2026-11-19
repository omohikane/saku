#!/usr/bin/env python3
"""Tests for `saku learn` (#68): daily notes -> MEMORY.md.

Temporary directories and a stubbed extractor only; the real vault is never
read or written.
"""

import json
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku import learning

NOTE = """---
tags: [daily]
date: "2026-11-18"
---

# 2026-11-18

## Sandbox

### OFF
静かな朝のほうが集中できると分かった。
"""


def _vault(tmp: Path) -> tuple[Path, Path, Path]:
    vault = tmp / "vault"
    memory = vault / "_saku" / "memory"
    daily = vault / "01_Notes" / "Daily_Note"
    memory.mkdir(parents=True)
    daily.mkdir(parents=True)
    return vault, memory, daily


def _cfg(daily: Path, **learning_cfg) -> dict:
    cfg = {"learning": {"dir": str(daily), "min_chars": 10}}
    cfg["learning"].update(learning_cfg)
    return cfg


def _install_extract(items):
    learning.dreaming._saved_extract = learning.dreaming.extract_items_from_text
    learning._saved_concepts = learning.build_concepts
    calls = []

    def _extract(text, *, date, kind="daily", llm_cfg=None):
        calls.append({"text": text, "date": date, "kind": kind})
        return list(items)

    learning.dreaming.extract_items_from_text = _extract
    # These tests are about the memory stage; the wiki stage is covered in
    # test_learn_wiki.py and must not reach the network here.
    learning.build_concepts = lambda *a, **k: []
    return calls


def _restore_extract():
    learning.dreaming.extract_items_from_text = learning.dreaming._saved_extract
    learning.build_concepts = learning._saved_concepts


def test_resolve_dir_defaults_to_the_vault_convention():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory, _daily = _vault(Path(tmp))
        assert learning.resolve_dir({}, Path(tmp), memory) == (
            vault / "01_Notes" / "Daily_Note"
        ).resolve()


def test_strip_frontmatter_and_normalize():
    body = learning.normalize(NOTE)
    assert "tags:" not in body and "date:" not in body
    assert body.startswith("# 2026-11-18")


def test_redact_masks_contacts_only():
    text = "連絡先は 03-1234-5678 / 090-1111-2222、mail は someone@example.com。記事 https://example.com/x は残す"
    masked = learning.redact(text)
    assert "03-1234-5678" not in masked
    assert "090-1111-2222" not in masked
    assert "someone@example.com" not in masked
    assert "[redacted-phone]" in masked and "[redacted-email]" in masked
    assert "https://example.com/x" in masked


def test_redact_leaves_dates_alone():
    text = "2026-11-18 に 2026-11-17 の話をした。"
    assert learning.redact(text) == text
    assert learning.has_contact_details("2026-11-18 の記録") is False
    assert learning.has_contact_details("03-1234-5678 に電話") is True


def test_list_notes_only_accepts_date_named_files_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, _memory, daily = _vault(Path(tmp))
        (daily / "2026-11-17.md").write_text(NOTE, encoding="utf-8")
        (daily / "2026-11-18.md").write_text(NOTE, encoding="utf-8")
        (daily / "README.md").write_text("not a note", encoding="utf-8")
        (daily / "Pasted image 1.png").write_bytes(b"x")
        names = [n.name for n in learning.list_notes(daily)]
        assert names == ["2026-11-18.md", "2026-11-17.md"]


def test_select_notes_skips_unchanged_and_honours_filters():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, _memory, daily = _vault(Path(tmp))
        for day in ("2026-11-16", "2026-11-17", "2026-11-18"):
            (daily / f"{day}.md").write_text(NOTE, encoding="utf-8")
        notes = learning.list_notes(daily)
        state = {"version": 1, "notes": {notes[0].name: {"sha256": notes[0].sha256}}}
        picked = learning.select_notes(notes, state)
        assert [n.name for n in picked] == ["2026-11-17.md", "2026-11-16.md"]

        assert [n.name for n in learning.select_notes(notes, state, limit=1)] == [
            "2026-11-17.md"
        ]
        assert [
            n.name for n in learning.select_notes(notes, state, since="2026-11-16")
        ] == ["2026-11-17.md", "2026-11-16.md"]
        assert [
            n.name for n in learning.select_notes(notes, state, only="2026-11-16")
        ] == ["2026-11-16.md"]
        assert len(learning.select_notes(notes, state, rebuild=True)) == 3


def test_dry_run_writes_nothing_but_reports_items():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text(NOTE, encoding="utf-8")
        calls = _install_extract([("好み・傾向", "静かな朝のほうが集中できる")])
        try:
            report = learning.run_cli(
                _cfg(daily), Path(tmp), memory, apply=False, verbose=False
            )
        finally:
            _restore_extract()

        assert report["dry_run"] is True
        assert report["items"] == 1
        assert report["added"] == 0
        assert not (memory / "MEMORY.md").exists()
        assert not (memory / "state" / "learning.json").exists()
        assert calls[0]["date"] == "2026-11-18"
        assert calls[0]["kind"] == "daily"


def test_apply_writes_memory_with_daily_provenance_and_saves_state():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text(NOTE, encoding="utf-8")
        _install_extract([("好み・傾向", "静かな朝のほうが集中できる")])
        try:
            report = learning.run_cli(
                _cfg(daily), Path(tmp), memory, apply=True, verbose=False
            )
        finally:
            _restore_extract()

        assert report["added"] == 1
        text = (memory / "MEMORY.md").read_text(encoding="utf-8")
        assert "静かな朝のほうが集中できる" in text
        assert "src:daily/2026-11-18" in text

        state = json.loads((memory / "state" / "learning.json").read_text(encoding="utf-8"))
        assert state["notes"]["2026-11-18.md"]["items"] == 1
        assert len(state["notes"]["2026-11-18.md"]["sha256"]) == 64


def test_second_run_skips_unchanged_notes():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text(NOTE, encoding="utf-8")
        _install_extract([("好み・傾向", "静かな朝のほうが集中できる")])
        try:
            learning.run_cli(_cfg(daily), Path(tmp), memory, apply=True, verbose=False)
            calls = _install_extract([("好み・傾向", "静かな朝のほうが集中できる")])
            report = learning.run_cli(_cfg(daily), Path(tmp), memory, apply=True, verbose=False)
        finally:
            _restore_extract()

        assert calls == []  # nothing extracted: the hash did not change
        assert report["processed"] == []


def test_edited_note_is_reprocessed_and_deduplicated():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        note = daily / "2026-11-18.md"
        note.write_text(NOTE, encoding="utf-8")
        _install_extract([("好み・傾向", "静かな朝のほうが集中できる")])
        try:
            learning.run_cli(_cfg(daily), Path(tmp), memory, apply=True, verbose=False)
            note.write_text(NOTE + "\n書き足し\n", encoding="utf-8")
            report = learning.run_cli(
                _cfg(daily), Path(tmp), memory, apply=True, verbose=False
            )
        finally:
            _restore_extract()

        assert [n for n, _items in report["processed"]] == ["2026-11-18.md"]
        # The same fact is reinforced, not duplicated: append_items dedupes.
        assert report["added"] == 0
        text = (memory / "MEMORY.md").read_text(encoding="utf-8")
        assert text.count("静かな朝のほうが集中できる") == 1


def test_template_only_note_is_skipped_and_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text("---\ntags: [daily]\n---\n\n# 2026-11-18\n", encoding="utf-8")
        calls = _install_extract([])
        try:
            report = learning.run_cli(
                _cfg(daily, min_chars=50), Path(tmp), memory, apply=True, verbose=False
            )
        finally:
            _restore_extract()

        assert report["processed"] == []
        assert report["skipped"][0][1].startswith("below min_chars")
        assert calls == []
        state = json.loads((memory / "state" / "learning.json").read_text(encoding="utf-8"))
        assert state["notes"]["2026-11-18.md"]["skipped"] == "min_chars"


def test_write_memory_false_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text(NOTE, encoding="utf-8")
        _install_extract([("好み・傾向", "静かな朝")])
        try:
            report = learning.run_cli(
                _cfg(daily, write_memory=False),
                Path(tmp),
                memory,
                apply=True,
                verbose=False,
            )
        finally:
            _restore_extract()
        assert report["items"] == 1
        assert not (memory / "MEMORY.md").exists()


def test_redaction_reaches_the_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory, daily = _vault(Path(tmp))
        (daily / "2026-11-18.md").write_text(
            NOTE + "\n電話 090-1111-2222 に掛けた。\n", encoding="utf-8"
        )
        calls = _install_extract([])
        try:
            learning.run_cli(_cfg(daily), Path(tmp), memory, verbose=False)
        finally:
            _restore_extract()
        assert "090-1111-2222" not in calls[0]["text"]
        assert "[redacted-phone]" in calls[0]["text"]


def test_load_learning_config_defaults():
    lcfg = cfg_mod.load_learning_config({})
    assert lcfg.min_chars == 200
    assert lcfg.redact is True
    assert lcfg.write_memory is True
    assert lcfg.max_notes_per_run == 20


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} learning tests PASSED!")


if __name__ == "__main__":
    run_tests()
