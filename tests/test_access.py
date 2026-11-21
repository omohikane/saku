#!/usr/bin/env python3
"""Tests for read-only vault access (#73 follow-up)."""

import os
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import access, config as cfg_mod
from saku.tools import list_dir, read_file, search_notes, write_file


def _vault(tmp: Path) -> tuple[Path, Path]:
    vault = tmp / "vault"
    memory = vault / "_saku" / "memory"
    memory.mkdir(parents=True)
    (vault / "01_Notes").mkdir()
    (vault / "01_Notes" / "note.md").write_text("needle in the vault", encoding="utf-8")
    (vault / "Private").mkdir()
    (vault / "Private" / "secret.md").write_text("needle but private", encoding="utf-8")
    return vault, memory


def _cfg(**access_cfg) -> dict:
    cfg = {"access": {"vault_read": ["01_Notes/"]}}
    cfg["access"].update(access_cfg)
    return cfg


def _use(cfg: dict):
    original = access._cfg_cache
    access._cfg_cache = cfg
    return original


def test_no_configuration_means_memory_only():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory = _vault(Path(tmp))
        assert access.resolve_vault_path(memory, "01_Notes/note.md", {}) is None
        assert "Memory root only" in access.describe(memory, {})


def test_allowed_prefix_is_readable_and_others_are_not():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        cfg = _cfg()
        target = access.resolve_vault_path(memory, "01_Notes/note.md", cfg)
        assert target == (vault / "01_Notes" / "note.md").resolve()
        assert access.resolve_vault_path(memory, "01_Notes", cfg) == (vault / "01_Notes").resolve()
        # Prefix boundary: 01_NotesX is not 01_Notes.
        assert access.resolve_vault_path(memory, "01_NotesX/note.md", cfg) is None
        assert access.resolve_vault_path(memory, "Private/secret.md", cfg) is None


def test_escapes_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        cfg = _cfg()
        assert access.resolve_vault_path(memory, "../01_Notes/note.md", cfg) is None
        assert access.resolve_vault_path(memory, "/etc/passwd", cfg) is None
        assert access.resolve_vault_path(memory, "01_Notes/../Private/secret.md", cfg) is None
        assert access.resolve_vault_path(memory, "01_Notes/.hidden.md", cfg) is None
        assert access.resolve_vault_path(memory, '"01_Notes/note.md"', cfg) is None


def test_symlink_escape_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        outside = Path(tmp) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        os.symlink(outside, vault / "01_Notes" / "link.md")
        cfg = _cfg()
        assert access.resolve_vault_path(memory, "01_Notes/link.md", cfg) is None


def test_read_and_list_tools_use_the_scope():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory = _vault(Path(tmp))
        original = _use(_cfg())
        try:
            assert read_file.run(memory, path="01_Notes/note.md") == "needle in the vault"
            listing = list_dir.run(memory, path="01_Notes/")
            assert "note.md" in listing
            denied = read_file.run(memory, path="Private/secret.md")
            assert denied.startswith("[DENY]")
        finally:
            access._cfg_cache = original


def test_writes_are_unaffected_by_read_access():
    with tempfile.TemporaryDirectory() as tmp:
        _vault_dir, memory = _vault(Path(tmp))
        original_access = _use(_cfg())
        original_policy = cfg_mod._policy_cache
        cfg_mod._policy_cache = cfg_mod.load_path_policy({})
        try:
            out = write_file.run(memory, path="01_Notes/note.md", body="clobber")
            assert out.startswith("[DENY]"), out
            assert (memory.parent.parent / "01_Notes" / "note.md").read_text(encoding="utf-8") == (
                "needle in the vault"
            )
        finally:
            cfg_mod._policy_cache = original_policy
            access._cfg_cache = original_access


def test_search_covers_memory_and_allowed_vault_dirs_only():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        (memory / "journal").mkdir()
        (memory / "journal" / "day.md").write_text("needle in memory", encoding="utf-8")
        original = _use(_cfg())
        try:
            out = search_notes.run(memory, body="needle")
        finally:
            access._cfg_cache = original
        assert "journal/day.md" in out
        assert "01_Notes/note.md" in out
        assert "Private/secret.md" not in out


def test_read_is_truncated_to_the_configured_limit():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        (vault / "01_Notes" / "long.md").write_text("x" * 100, encoding="utf-8")
        original = _use(_cfg(max_file_chars=20))
        try:
            out = read_file.run(memory, path="01_Notes/long.md")
        finally:
            access._cfg_cache = original
        assert out.startswith("x" * 20) and "truncated" in out


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} access tests PASSED!")


if __name__ == "__main__":
    run_tests()
