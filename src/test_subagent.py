#!/usr/bin/env python3
"""
Tests for saku.subagent (child agent spawn + delegate).
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import subagent


def test_spawn_child():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        res = subagent.spawn_child(root, "mei", "リサーチ担当")
        assert "[OK]" in res
        p = root / "children" / "mei.md"
        assert p.exists()
        assert "リサーチ担当" in p.read_text(encoding="utf-8")


def test_spawn_child_requires_name():
    res = subagent.spawn_child(Path("/tmp"), "", "role")
    assert "[ERROR]" in res


def test_build_child_prompt():
    p = subagent.build_child_prompt("mei", "リサーチ担当")
    assert "sub-agent of SAKU" in p
    assert "mei" in p
    assert "リサーチ担当" in p


def test_delegate_missing_child():
    with tempfile.TemporaryDirectory() as tmp:
        res = subagent.delegate(Path(tmp), CODE_ROOT, "nobody", "task")
        assert "not found" in res


def test_delegate_runs_child_loop():
    import saku.agent_loop as al

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subagent.spawn_child(root, "mei", "リサーチ担当")

        calls = {"n": 0}

        def fake_chat_stream(history, llm_cfg, on_token=None):
            calls["n"] += 1
            return "調査結果: 完了"

        original = al.chat_stream
        al.chat_stream = fake_chat_stream
        try:
            res = subagent.delegate(root, CODE_ROOT, "mei", "調べて")
        finally:
            al.chat_stream = original

        assert "調査結果" in res
        assert calls["n"] >= 1


def test_delegation_depth_guard():
    subagent._delegation_depth = subagent.MAX_DELEGATION_DEPTH
    try:
        res = subagent.delegate(Path("/tmp"), CODE_ROOT, "x", "task")
        assert "depth limit" in res
    finally:
        subagent._delegation_depth = 0


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} sub-agent tests PASSED!")


if __name__ == "__main__":
    run_tests()
