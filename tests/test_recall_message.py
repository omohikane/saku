#!/usr/bin/env python3
"""
Regression tests for saku.core.build_recall_message (refs #69).

The recall message is appended after the system prompt, so it must not use
the system role: llama.cpp's Jinja chat template rejects a system message
that is not at the beginning ("System message must be at the beginning",
500 error in the Web UI chat). It is a user message tagged "[recall]".

No LLM: recall is a pure keyword search over a temporary memory root.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import core

MEMORY_MD = """# MEMORY — 長期記憶

## 進行中のプロジェクト
- 2026-08-25: ローカルLLMのベンチマークを進行中 <!-- s:1 last:2026-08-25 -->
"""


def _make_root():
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "MEMORY.md").write_text(MEMORY_MD, encoding="utf-8")
    (root / "principles").mkdir()
    (root / "wiki").mkdir()
    return root


def _patched(root):
    """Point core at the temp memory root with default (enabled) recall config."""
    old = (core.MEMORY_ROOT, core._cfg)
    core.MEMORY_ROOT = root
    core._cfg = {}
    return old


def _restore(old):
    core.MEMORY_ROOT, core._cfg = old


def test_recall_message_is_user_role_with_recall_prefix():
    root = _make_root()
    old = _patched(root)
    try:
        msg = core.build_recall_message("ローカルLLMの状況は？")
    finally:
        _restore(old)
    assert msg is not None
    assert msg["role"] == "user", msg
    assert msg["role"] != "system"
    assert msg["content"].startswith("[recall]")
    assert "ローカルLLM" in msg["content"]


def test_recall_message_none_when_nothing_matches():
    root = _make_root()
    old = _patched(root)
    try:
        msg = core.build_recall_message("まったく無関係な話題xyz")
    finally:
        _restore(old)
    assert msg is None


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} recall message tests PASSED!")


if __name__ == "__main__":
    run_tests()
