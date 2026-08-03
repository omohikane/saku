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


def test_resolve_inbox_dir():
    # default: memory_root.parent/00_Inbox
    mem = Path("/vault/_saku/memory")
    cfg_default = {}
    assert str(config.resolve_inbox_dir(cfg_default, mem, Path("/base"))) == "/vault/_saku/00_Inbox"
    # configured absolute
    cfg_abs = {"memory": {"inbox_dir": "/vault/00_Inbox"}}
    assert str(config.resolve_inbox_dir(cfg_abs, mem, Path("/base"))) == "/vault/00_Inbox"
    # configured relative to config base
    cfg_rel = {"memory": {"inbox_dir": "relative_inbox"}}
    assert str(config.resolve_inbox_dir(cfg_rel, mem, Path("/base"))) == "/base/relative_inbox"
    # configured env var
    os.environ["SAKU_INBOX"] = "/vault/00_Inbox"
    cfg_env = {"memory": {"inbox_dir": "${SAKU_INBOX}"}}
    assert str(config.resolve_inbox_dir(cfg_env, mem, Path("/base"))) == "/vault/00_Inbox"


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
        # genome master is created in the vault (from repo template fallback)
        assert (root / "identity" / "genome.md").exists()
        # idempotent: running twice must not fail or overwrite
        init_memory(root)


def test_genome_priority_vault_root():
    """When the vault root has identity/genome.md, setup copies it (not the repo template)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        vault_root = base / "_saku"
        (vault_root / "identity").mkdir(parents=True)
        real = vault_root / "identity" / "genome.md"
        real.write_text("# Real master genome", encoding="utf-8")

        memory_root = vault_root / "memory"
        init_memory(memory_root)

        copied = memory_root / "identity" / "genome.md"
        assert copied.exists()
        assert "Real master genome" in copied.read_text(encoding="utf-8")


def test_load_path_policy():
    # defaults
    p = config.load_path_policy({})
    assert p.is_read_allowed("journal/x.md")
    assert p.is_write_allowed("blog/x.md")
    assert not p.is_write_allowed("meta.md")
    assert not p.is_read_allowed("../outside.md")
    assert "meta.md" in p.write_denied_exact
    assert "chat.md" in p.delete_denied_exact
    # custom config
    p2 = config.load_path_policy(
        {"paths": {"read_allowed": ["a/"], "write_allowed": ["b/"], "write_denied_exact": ["x.md"]}}
    )
    assert p2.is_read_allowed("a/f.md")
    assert p2.is_write_allowed("b/f.md")
    assert not p2.is_write_allowed("a/f.md")
    assert "x.md" in p2.write_denied_exact


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} setup tests PASSED!")


if __name__ == "__main__":
    run_tests()
