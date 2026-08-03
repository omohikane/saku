#!/usr/bin/env python3
"""
Resilience tests:

- context compaction keeps the system prompt (important instructions)
- a corrupted/empty MEMORY.md is recovered by dreaming
- a corrupted meta.md does not crash the prompt builder
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import context, dreaming


def test_compaction_preserves_system_instructions():
    """After compaction the system prompt (the critical instructions) is fully preserved."""
    system = "IMPORTANT: These are critical rules that must never be lost. " + "X" * 500
    history = [{"role": "system", "content": system}] + [
        {"role": "user", "content": f"message {i} " + "y" * 100} for i in range(50)
    ]
    out, dropped = context.truncate_history(history, keep_recent_tokens=200)
    assert dropped > 0
    assert out[0]["content"] == system
    assert "IMPORTANT" in out[0]["content"]


def test_memory_recovery_from_empty():
    """A corrupted (empty) MEMORY.md is re-initialized by dreaming's append."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        path.write_text("", encoding="utf-8")
        added = dreaming.append_items(path, [("好み・傾向", "朝型")], "2026-08-03")
        assert len(added) == 1
        content = path.read_text(encoding="utf-8")
        assert "# MEMORY" in content
        assert "朝型" in content


def test_memory_recovery_from_garbage():
    """A MEMORY.md containing garbage still accepts appends."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        path.write_text("garbage that is not valid markdown\n", encoding="utf-8")
        added = dreaming.append_items(path, [("進行中プロジェクト", "新しいプロジェクト")], "2026-08-03")
        assert len(added) == 1
        content = path.read_text(encoding="utf-8")
        assert "新しいプロジェクト" in content
        assert "## 進行中のプロジェクト" in content


def test_meta_corruption_does_not_crash_prompt():
    """A corrupted meta.md is tolerated by the prompt builder."""
    import saku.core as core

    original = core.load_file
    core.load_file = lambda p: original(p) if p.name != "meta.md" else "\x00\x00 broken \x00"
    try:
        sp = core.build_system_prompt()
        assert isinstance(sp, str) and len(sp) > 0
    finally:
        core.load_file = original


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} resilience tests PASSED!")


if __name__ == "__main__":
    run_tests()
