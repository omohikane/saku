#!/usr/bin/env python3
"""
Tests for saku.setup (memory root initialization) and config env expansion.
"""

import os
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config
from saku.setup import init_memory


def test_expand_env():
    os.environ["SAKU_TEST_VAR"] = "/tmp/abc"
    assert config.expand_env("x/${SAKU_TEST_VAR}/y") == "x//tmp/abc/y"
    assert config.expand_env("a/${NOT_SET_XYZ}/b") == "a/${NOT_SET_XYZ}/b"


def test_resolve_memory_root_env():
    os.environ["SAKU_MEM_ROOT"] = "/tmp/vault/memory"
    cfg = {"memory": {"root": "${SAKU_MEM_ROOT}"}}
    assert str(config.resolve_memory_root(cfg, Path("/tmp/base"))) == "/tmp/vault/memory"


def test_init_memory():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "memory"
        init_memory(root)
        for d in (
            "journal",
            "monologue",
            "principles",
            "blog",
            "skills",
            "children",
            "study",
            "tools",
            "state",
            "identity",
            "wiki",
        ):
            assert (root / d).is_dir(), f"missing {d}"
        assert (root / "meta.md").exists()
        assert (root / "chat.md").exists()
        assert (root / "request_list.md").exists()
        # idempotent: running twice must not fail or overwrite
        init_memory(root)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} setup tests PASSED!")


if __name__ == "__main__":
    run_tests()
