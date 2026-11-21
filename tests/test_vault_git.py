#!/usr/bin/env python3
"""Tests for saku.vault_git (memory repository sync).

Everything runs against a local bare repository used as ``origin``, so no network
and no GitHub credentials are needed.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

CODE_ROOT = Path(__file__).parent
if str(CODE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT.parent))

from saku import vault_git
from saku.config import GitConfig


def _git(*args: str, cwd: Path) -> str:
    res = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return res.stdout


def _git_cfg(**overrides) -> GitConfig:
    cfg = GitConfig(
        enabled=True,
        debounce_seconds=0,
        remote="origin",
        timeout_seconds=20,
        max_log_bytes=200_000,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class Sandbox:
    """A bare origin plus a working clone with a memory root inside it."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.remote = self.base / "origin.git"
        self.repo = self.base / "vault"
        self.memory_root = self.repo / "99_System" / "saku"

        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        _git("config", "user.email", "saku@test.local", cwd=self.repo)
        _git("config", "user.name", "Saku Test", cwd=self.repo)
        _git("remote", "add", "origin", str(self.remote), cwd=self.repo)
        self.memory_root.mkdir(parents=True)
        (self.repo / "README.md").write_text("vault\n", encoding="utf-8")
        _git("add", "-A", cwd=self.repo)
        _git("commit", "-q", "-m", "init", cwd=self.repo)
        _git("push", "-q", "-u", "origin", "HEAD:main", cwd=self.repo)

    def write_memory(self, rel: str, text: str) -> Path:
        p = self.memory_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def close(self) -> None:
        self._tmp.cleanup()


def _sandbox() -> Sandbox:
    return Sandbox()


def test_disabled_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        res = vault_git.sync(_git_cfg(enabled=False), root, root)
        assert res["action"] == "skipped"
        assert res["ok"] is False


def test_commit_and_push():
    sb = _sandbox()
    try:
        sb.write_memory("journal/2026-11-15.md", "# journal\n")
        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res
        assert res["action"] == "commit+push"
        assert len(res["files"]) == 1

        remote_log = _git("log", "--oneline", "main", cwd=sb.remote)
        assert "saku: memory update" in remote_log
        # the memory root is the pathspec, so the vault stays untouched otherwise
        assert "99_System/saku/journal/2026-11-15.md" in _git(
            "show", "--name-only", "--format=", "main", cwd=sb.remote
        )
    finally:
        sb.close()


def test_nothing_to_commit():
    sb = _sandbox()
    try:
        sb.write_memory("MEMORY.md", "# memory\n")
        assert vault_git.sync(_git_cfg(), sb.memory_root, sb.base)["ok"]
        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"]
        assert res["action"] == "nothing"
    finally:
        sb.close()


def test_nothing_to_commit_still_pushes_a_stranded_commit():
    """A push that failed must not be stranded behind "nothing to commit"."""
    sb = _sandbox()
    try:
        sb.write_memory("MEMORY.md", "# memory\n")
        assert vault_git.sync(_git_cfg(), sb.memory_root, sb.base)["ok"]

        # Simulate a commit whose push failed (network outage): local-only.
        sb.write_memory("journal/2026-11-21.md", "pending\n")
        _git("add", "-A", cwd=sb.repo)
        _git("commit", "-q", "-m", "pending memory", cwd=sb.repo)
        remote_before = _git("log", "--oneline", "main", cwd=sb.remote)
        assert "pending memory" not in remote_before

        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res
        assert res["action"] == "push", res
        remote_after = _git("log", "--oneline", "main", cwd=sb.remote)
        assert "pending memory" in remote_after
    finally:
        sb.close()


def test_scope_does_not_sweep_owner_edits():
    sb = _sandbox()
    try:
        (sb.repo / "00_Inbox").mkdir()
        (sb.repo / "00_Inbox" / "owner-note.md").write_text("draft\n", encoding="utf-8")
        sb.write_memory("journal/2026-11-15.md", "entry\n")

        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res

        status = _git("status", "--porcelain", cwd=sb.repo)
        assert "00_Inbox/" in status  # still untracked, not committed
        assert "journal/2026-11-15.md" not in status
    finally:
        sb.close()


def test_memory_root_outside_repository_is_skipped():
    sb = _sandbox()
    try:
        outside = Path(tempfile.mkdtemp()) / "elsewhere"
        outside.mkdir()
        # The repository is pinned to the sandbox, but the memory root is not in it.
        res = vault_git.sync(_git_cfg(root=str(sb.repo)), outside, sb.base)
        assert res["ok"] is False
        assert "outside repository" in res["message"]
    finally:
        sb.close()


def test_rejected_push_rebases_and_retries():
    sb = _sandbox()
    try:
        # Another machine (the Owner's Obsidian) pushes first.
        other = sb.base / "other"
        subprocess.run(["git", "clone", "-q", str(sb.remote), str(other)], check=True)
        _git("checkout", "-q", "-B", "main", "origin/main", cwd=other)
        _git("config", "user.email", "owner@test.local", cwd=other)
        _git("config", "user.name", "Owner", cwd=other)
        (other / "01_Notes").mkdir()
        (other / "01_Notes" / "owner.md").write_text("owner edit\n", encoding="utf-8")
        _git("add", "-A", cwd=other)
        _git("commit", "-q", "-m", "owner edit", cwd=other)
        _git("push", "-q", "origin", "HEAD:main", cwd=other)

        # The agent commits locally without knowing about the remote move.
        sb.write_memory("principles/rule.md", "rule\n")
        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res

        log = _git("log", "--oneline", "main", cwd=sb.remote)
        assert "owner edit" in log
        assert "saku: memory update" in log
    finally:
        sb.close()


def test_debounce_blocks_second_call():
    sb = _sandbox()
    original = vault_git._settings
    try:
        cfg = _git_cfg(debounce_seconds=3600)
        vault_git._settings = lambda: (cfg, sb.memory_root, sb.base)
        vault_git.reset_debounce()
        sb.write_memory("MEMORY.md", "x\n")

        first = vault_git.sync_if_due(background=False)
        assert first["ok"], first

        second = vault_git.sync_if_due(background=False)
        assert second["action"] == "debounced"
    finally:
        vault_git._settings = original
        vault_git.reset_debounce()
        sb.close()


def test_lock_prevents_concurrent_sync():
    sb = _sandbox()
    try:
        lock = sb.repo / ".git" / "saku-sync.lock"
        lock.write_text("999999", encoding="utf-8")
        sb.write_memory("MEMORY.md", "x\n")
        res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"] is False
        assert "another sync" in res["message"]
        lock.unlink()
    finally:
        sb.close()


def test_log_is_written():
    sb = _sandbox()
    try:
        sb.write_memory("MEMORY.md", "x\n")
        vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        log = sb.repo / ".git" / "saku-git.log"
        assert log.exists()
        assert "pushed" in log.read_text(encoding="utf-8") or "commit" in log.read_text(
            encoding="utf-8"
        )
    finally:
        sb.close()


def test_pull_brings_owner_changes():
    sb = _sandbox()
    try:
        other = sb.base / "other"
        subprocess.run(["git", "clone", "-q", str(sb.remote), str(other)], check=True)
        _git("checkout", "-q", "-B", "main", "origin/main", cwd=other)
        _git("config", "user.email", "owner@test.local", cwd=other)
        _git("config", "user.name", "Owner", cwd=other)
        (other / "01_Notes").mkdir()
        (other / "01_Notes" / "owner.md").write_text("owner edit\n", encoding="utf-8")
        _git("add", "-A", cwd=other)
        _git("commit", "-q", "-m", "owner edit", cwd=other)
        _git("push", "-q", "origin", "HEAD:main", cwd=other)

        res = vault_git.pull(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res
        assert res["action"] == "pulled"
        assert res["behind"] >= 1
        assert (sb.repo / "01_Notes" / "owner.md").exists()
    finally:
        sb.close()


def test_pull_up_to_date():
    sb = _sandbox()
    try:
        res = vault_git.pull(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res
        assert res["action"] == "up-to-date"
    finally:
        sb.close()


def test_pull_keeps_uncommitted_work():
    sb = _sandbox()
    try:
        file = sb.write_memory("MEMORY.md", "local draft\n")
        res = vault_git.pull(_git_cfg(), sb.memory_root, sb.base)
        assert res["ok"], res
        assert file.read_text(encoding="utf-8") == "local draft\n"
        status = _git("status", "--porcelain", cwd=sb.repo)
        assert "99_System/" in status  # still uncommitted, not swallowed by the pull
    finally:
        sb.close()


def test_pull_if_due_is_debounced():
    sb = _sandbox()
    original = vault_git._settings
    saved_pull = vault_git._last_pull
    try:
        cfg = _git_cfg(pull_interval_seconds=3600)
        vault_git._settings = lambda: (cfg, sb.memory_root, sb.base)
        first = vault_git.pull_if_due(background=False)
        assert first["ok"], first
        second = vault_git.pull_if_due(background=False)
        assert second["action"] == "debounced"
    finally:
        vault_git._settings = original
        vault_git._last_pull = saved_pull
        sb.close()


def test_readonly_git_dir_is_reported_not_raised():
    sb = _sandbox()
    try:
        gitdir = sb.repo / ".git"
        gitdir.chmod(0o500)
        try:
            res = vault_git.sync(_git_cfg(), sb.memory_root, sb.base)
        finally:
            gitdir.chmod(0o700)
        assert res["ok"] is False
        assert "cannot take the sync lock" in res["message"]
    finally:
        sb.close()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} vault_git tests PASSED!")


if __name__ == "__main__":
    run_tests()
