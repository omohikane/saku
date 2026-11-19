#!/usr/bin/env python3
"""Tests for #124: path policy decisions on normalized paths, and child names.

The vulnerability: tools checked the raw string the model wrote and resolved it
afterwards, so ``study/../meta.md`` passed the allow-list (starts with
``study/``) and the deny-list (is not ``meta.md``), then overwrote meta.md.
Same shape allowed deleting ``state/STOP`` and spawned children outside
``children/``.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku import subagent
from saku.tools import append_file, delete_file, move_file, read_file, write_file


def _root(tmp: Path) -> Path:
    root = tmp / "memory"
    (root / "state").mkdir(parents=True)
    (root / "study").mkdir()
    (root / "meta.md").write_text("## 現在の状態\n- original\n", encoding="utf-8")
    (root / "state" / "STOP").write_text("stop", encoding="utf-8")
    (root / "study" / "note.md").write_text("note", encoding="utf-8")
    return root


def _use_default_policy():
    original = cfg_mod._policy_cache
    cfg_mod._policy_cache = cfg_mod.load_path_policy({})
    return original


def test_normalize_memory_path_collapses_dot_segments():
    n = cfg_mod.normalize_memory_path
    assert n("study/../meta.md") == "meta.md"
    assert n("./journal//x/./y.md") == "journal/x/y.md"
    assert n("journal\\2026-11-18.md") == "journal/2026-11-18.md"
    assert n("study/..") is None
    assert n("../outside.md") is None
    assert n("/etc/passwd") is None
    assert n('"meta.md"') is None
    assert n("") is None


def test_policy_treats_equivalent_paths_the_same():
    policy = cfg_mod.load_path_policy({})
    assert policy.is_write_allowed("journal/x.md")
    assert not policy.is_write_allowed("meta.md")
    assert not policy.is_write_allowed("study/../meta.md"), "dot segment bypassed the deny list"
    assert not policy.is_write_allowed("../escape.md")
    assert policy.is_read_allowed("study/../meta.md"), "meta.md is readable"
    assert not policy.is_delete_allowed("study/../state/STOP")
    assert not policy.is_delete_allowed("state/../state/STOP")
    assert policy.is_delete_allowed("journal/x.md")


def test_write_file_cannot_reach_meta_via_dot_segment():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        original = _use_default_policy()
        try:
            out = write_file.run(root, path="study/../meta.md", body="HACKED")
        finally:
            cfg_mod._policy_cache = original
        assert out.startswith("[DENY]"), out
        assert "HACKED" not in (root / "meta.md").read_text(encoding="utf-8")


def test_delete_file_cannot_remove_the_kill_switch_via_dot_segment():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        original = _use_default_policy()
        try:
            out = delete_file.run(root, path="study/../state/STOP")
            out2 = delete_file.run(root, path="state/STOP")
        finally:
            cfg_mod._policy_cache = original
        assert out.startswith("[DENY]"), out
        assert out2.startswith("[DENY]"), out2
        assert (root / "state" / "STOP").is_file()


def test_append_and_move_cannot_escape():
    """Appending to meta.md is allowed by design; the heading rule must still apply."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        original = _use_default_policy()
        try:
            no_heading = append_file.run(
                root, path="study/../meta.md", body="- injected"
            )
            with_heading = append_file.run(
                root, path="study/../meta.md", body="- injected", heading="現在の状態"
            )
            moved = move_file.run(
                root, **{"from": "study/../state/STOP", "to": "study/stolen"}
            )
        finally:
            cfg_mod._policy_cache = original

        # One file, one rule: the dot-segment form is meta.md, so APPEND_FILE
        # requires the same heading parameter and lands in the same file.
        assert no_heading.startswith("[ERROR]"), no_heading
        assert with_heading.startswith("[OK]"), with_heading
        assert "injected" in (root / "meta.md").read_text(encoding="utf-8")
        assert moved.startswith("[DENY]"), moved
        assert (root / "state" / "STOP").is_file()


def test_normal_operations_still_work():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        original = _use_default_policy()
        try:
            wrote = write_file.run(root, path="study/new.md", body="hello")
            read = read_file.run(root, path="study/new.md")
            deleted = delete_file.run(root, path="study/note.md")
        finally:
            cfg_mod._policy_cache = original
        assert wrote.startswith("[OK]"), wrote
        assert read == "hello"
        assert deleted.startswith("[OK]"), deleted


def test_child_names_are_identifiers_not_paths():
    assert subagent.valid_child_name("mei")
    assert subagent.valid_child_name("テスト")
    assert not subagent.valid_child_name("../meta")
    assert not subagent.valid_child_name("nested/child")
    assert not subagent.valid_child_name("..")
    assert not subagent.valid_child_name(".hidden")
    assert not subagent.valid_child_name("")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "memory"
        (root / "children").mkdir(parents=True)
        (root / "meta.md").write_text("secret", encoding="utf-8")

        out = subagent.spawn_child(root, "../evil", "悪意")
        assert out.startswith("[ERROR]"), out
        assert not (root.parent / "evil.md").exists()

        assert subagent.load_child_genome(root, "../meta") is None

        ok = subagent.spawn_child(root, "mei", "検証役")
        assert ok.startswith("[OK]"), ok
        assert (root / "children" / "mei.md").is_file()
        assert subagent.load_child_genome(root, "mei") is not None


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} path-policy tests PASSED!")


if __name__ == "__main__":
    run_tests()
