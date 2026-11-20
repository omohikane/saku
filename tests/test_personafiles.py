#!/usr/bin/env python3
"""Tests for editable identity files (#73 follow-up): resolution and safety."""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku import personafiles
from saku.tools import append_file, delete_file, move_file, write_file


def _root(tmp: Path) -> Path:
    root = tmp / "memory"
    (root / "identity").mkdir(parents=True)
    for name in ("soul", "genome", "persona"):
        (root / "identity" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def test_resolution_uses_config_and_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        files = personafiles.resolve({}, root)
        assert files.soul == root / "identity" / "soul.md"
        assert files.genome == root / "identity" / "genome.md"
        assert files.persona == root / "identity" / "persona.md"

        cfg = {"identity": {"soul": "custom/soul.md", "genome": "custom/genome.md"},
               "persona": {"file": "custom/persona.md"}}
        files = personafiles.resolve(cfg, root)
        assert files.soul == root / "custom" / "soul.md"
        assert files.persona == root / "custom" / "persona.md"


def test_genome_falls_back_when_the_configured_file_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        memory = Path(tmp) / "vault" / "_saku" / "memory"
        (memory / "identity").mkdir(parents=True)
        (Path(tmp) / "vault" / "identity").mkdir()
        (Path(tmp) / "vault" / "identity" / "genome.md").write_text("vault", encoding="utf-8")
        files = personafiles.resolve({"identity": {"genome": "identity/missing.md"}}, memory)
        assert files.genome.read_text(encoding="utf-8") == "vault"


def test_validate_and_write_keep_a_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        path = root / "identity" / "persona.md"
        assert personafiles.validate("unknown", "x")
        assert personafiles.validate("persona", "x" * (personafiles.MAX_CHARS + 1))
        assert personafiles.validate("persona", "ok") is None

        personafiles.write(path, "new content")
        assert path.read_text(encoding="utf-8") == "new content"
        assert path.with_suffix(".md.bak").read_text(encoding="utf-8") == "# persona\n"
        assert not list(path.parent.glob("*.tmp"))


def test_agent_tools_cannot_touch_identity_files():
    """The Owner edits identity; SAKU's tools never do, even with a widened policy."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        original = cfg_mod._policy_cache
        # A policy that would otherwise allow the whole identity directory.
        cfg_mod._policy_cache = cfg_mod.load_path_policy(
            {"paths": {"write_allowed": ["identity/", "study/"]}}
        )
        try:
            for name in ("soul", "genome", "persona"):
                target = f"identity/{name}.md"
                assert write_file.run(root, path=target, body="HACKED").startswith("[DENY]")
                assert append_file.run(root, path=target, body="- HACKED").startswith("[DENY]")
                assert delete_file.run(root, path=target).startswith("[DENY]")
                moved = move_file.run(root, **{"from": target, "to": "study/stolen.md"})
                assert moved.startswith("[DENY]")
                assert "HACKED" not in (root / target).read_text(encoding="utf-8")
        finally:
            cfg_mod._policy_cache = original


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} identity-file tests PASSED!")


if __name__ == "__main__":
    run_tests()
