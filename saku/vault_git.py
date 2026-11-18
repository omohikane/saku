"""Commit and push SAKU's memory into a git repository (e.g. an Obsidian vault).

SAKU's memory is plain Markdown, so the natural durability story is git: when the
memory root sits inside a repository, every batch of writes can be committed and
pushed, and the vault follows by pulling. This module is the opt-in glue for that
(`[git] enabled = true`).

Design constraints (they all come from real failure modes):

* **Scope** — only the configured pathspec (by default the memory root itself) is
  staged. ``git add -A`` would sweep the Owner's in-progress Obsidian edits into
  the agent's commit.
* **Never force** — the Owner may push from another machine at the same time, so a
  rejected push is resolved with ``pull --rebase --autostash`` and a retry, never
  with ``--force``.
* **Never fatal** — a failed sync is logged to ``state/git.log`` and retried in the
  next debounce window. The chat/daemon loop must not die because the network did.
* **Debounced** — a sync attempt runs at most once per ``debounce_seconds`` so a
  burst of tool writes becomes one commit, not twenty.
* **Opt-in** — the default memory root lives inside the SAKU code checkout, so
  committing there would mix agent memory into the code history.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import (
    GitConfig,
    expand_env,
    load_config,
    load_git_config,
    resolve_memory_root,
)

# Debounce state is module-level: the daemon, the Web UI and the CLI all share one
# process in practice (``saku ui`` starts the daemon in-process).
_last_attempt = 0.0
_debounce_lock = threading.Lock()


def _now() -> float:
    return time.time()


def reset_debounce() -> None:
    """Forget the last attempt (tests, and `saku sync` which forces a run)."""
    global _last_attempt
    with _debounce_lock:
        _last_attempt = 0.0


def _settings() -> tuple[GitConfig, Path, Path]:
    """Return (git config, memory root, config base) from config.toml."""
    cfg, base = load_config()
    git_cfg = load_git_config(cfg)
    memory_root = resolve_memory_root(cfg, base)
    return git_cfg, memory_root, base


def _env(git_cfg: GitConfig) -> dict[str, str]:
    env = os.environ.copy()
    if git_cfg.ssh_command:
        # `/etc/ssh/ssh_config.d` is not always readable/owned correctly (the
        # "Bad owner or permissions" failure); the Owner's own config can be used
        # explicitly instead of touching system files.
        env["GIT_SSH_COMMAND"] = git_cfg.ssh_command
    env.setdefault("GIT_TERMINAL_PROMPT", "0")  # never block on a credential prompt
    return env


def _git(root: Path, args: list[str], git_cfg: GitConfig) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=git_cfg.timeout_seconds,
        env=_env(git_cfg),
    )


def _repo_root(memory_root: Path, git_cfg: GitConfig, base: Path) -> Path | None:
    if git_cfg.root:
        configured = Path(expand_env(git_cfg.root))
        if not configured.is_absolute():
            configured = (base / configured).resolve()
        return configured
    res = _git(memory_root, ["rev-parse", "--show-toplevel"], git_cfg)
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip()).resolve()


def _pathspecs(memory_root: Path, repo: Path, git_cfg: GitConfig) -> list[str]:
    """Repo-relative pathspecs to stage.

    ``[git] paths`` may name several subtrees of the memory root; with no explicit
    paths the memory root itself is the scope.
    """
    if git_cfg.paths:
        return list(git_cfg.paths)
    try:
        rel = memory_root.resolve().relative_to(repo)
    except ValueError:
        return ["."]
    return [rel.as_posix() or "."]


def _git_dir(repo: Path, git_cfg: GitConfig) -> Path:
    """Absolute .git directory (works for worktrees, where .git is a file)."""
    res = _git(repo, ["rev-parse", "--absolute-git-dir"], git_cfg)
    if res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip())
    return repo / ".git"


def _log(repo: Path, git_cfg: GitConfig, line: str) -> None:
    """Append to the repository's own log, keeping it bounded.

    Deliberately outside the worktree: a log that changes on every sync would be
    committed to the vault on the next run and show up in Obsidian as noise.
    """
    try:
        log = _git_dir(repo, git_cfg) / "saku-git.log"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {line}\n")
        if log.stat().st_size > git_cfg.max_log_bytes:
            tail = log.read_text(encoding="utf-8")[-git_cfg.max_log_bytes // 2 :]
            log.write_text(tail, encoding="utf-8")
    except OSError:
        pass  # logging must never be the thing that breaks a sync


class _Lock:
    """Tiny exclusive lock file so the UI thread and the daemon cannot race."""

    def __init__(self, path: Path, stale_after: float):
        self.path = path
        self.stale_after = stale_after
        self.held = False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    age = _now() - self.path.stat().st_mtime
                except OSError:
                    age = 0
                if age > self.stale_after:  # a crashed process left it behind
                    try:
                        self.path.unlink()
                    except OSError:
                        return False
                    continue
                return False
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(str(os.getpid()))
                self.held = True
                return True
        return False

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


def _branch(repo: Path, git_cfg: GitConfig) -> str:
    """The remote branch to talk to.

    Prefer the upstream name: a local branch can track a differently named remote
    branch (``git push -u origin HEAD:main`` from a ``master`` clone), and pushing
    ``HEAD:master`` would create a branch the vault ignores.
    """
    if git_cfg.branch:
        return git_cfg.branch
    upstream = _git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], git_cfg)
    if upstream.returncode == 0 and "/" in upstream.stdout:
        return upstream.stdout.strip().split("/", 1)[1]
    head = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], git_cfg)
    return head.stdout.strip() if head.returncode == 0 else ""


def _pull_and_push(repo: Path, git_cfg: GitConfig) -> tuple[bool, str]:
    """Rebase onto the remote branch and push, retrying a rejected push."""
    if not git_cfg.push:
        return True, "push disabled"

    branch = _branch(repo, git_cfg)
    if not branch:
        return False, "cannot determine branch"

    remote_heads = _git(repo, ["ls-remote", "--heads", git_cfg.remote, branch], git_cfg)
    has_remote_branch = remote_heads.returncode == 0 and bool(remote_heads.stdout.strip())

    last_error = ""
    for attempt in range(3):
        if has_remote_branch:
            pull = _git(repo, ["pull", "--rebase", "--autostash", git_cfg.remote, branch], git_cfg)
            if pull.returncode != 0:
                last_error = (pull.stderr or pull.stdout).strip()
                _git(repo, ["rebase", "--abort"], git_cfg)  # leave no half-finished rebase
                _log(repo, git_cfg, f"pull failed (attempt {attempt + 1}): {last_error[:300]}")
                time.sleep(2 * (attempt + 1))
                continue
        push = _git(repo, ["push", git_cfg.remote, f"HEAD:{branch}"], git_cfg)
        if push.returncode == 0:
            return True, f"pushed to {git_cfg.remote}/{branch}"
        last_error = (push.stderr or push.stdout).strip()
        _log(repo, git_cfg, f"push failed (attempt {attempt + 1}): {last_error[:300]}")
        has_remote_branch = True  # next round: pull first, the remote moved
        time.sleep(2 * (attempt + 1))

    return False, last_error[:500]


def sync(git_cfg: GitConfig | None = None, memory_root: Path | None = None, base: Path | None = None) -> dict:
    """Commit the configured pathspec and push (synchronous, never raises).

    Returns ``{"ok": bool, "action": str, "message": str, "commit": str, "files": [...]}``.
    """
    if git_cfg is None or memory_root is None or base is None:
        git_cfg, memory_root, base = _settings()

    result = {"ok": False, "action": "skipped", "message": "", "commit": "", "files": []}
    if not git_cfg.enabled:
        result["message"] = "git sync disabled ([git] enabled = false)"
        return result

    repo = _repo_root(memory_root, git_cfg, base)
    if repo is None:
        result["message"] = f"not a git repository: {memory_root}"
        _log(memory_root, git_cfg, result["message"])
        return result

    if not memory_root.resolve().is_relative_to(repo):
        result["message"] = f"memory root {memory_root} is outside repository {repo}"
        _log(memory_root, git_cfg, result["message"])
        return result

    lock_path = _git_dir(repo, git_cfg) / "saku-sync.lock"
    with _Lock(lock_path, stale_after=git_cfg.timeout_seconds * 3) as acquired:
        if not acquired:
            result["message"] = "another sync is running"
            return result
        try:
            specs = _pathspecs(memory_root, repo, git_cfg)

            add = _git(repo, ["add", "-A", "--", *specs], git_cfg)
            if add.returncode != 0:
                result["message"] = f"git add failed: {(add.stderr or add.stdout).strip()[:300]}"
                _log(repo, git_cfg, result["message"])
                return result

            cached = _git(repo, ["diff", "--cached", "--quiet", "--", *specs], git_cfg)
            if cached.returncode == 0:
                result.update(ok=True, action="nothing", message="no changes to commit")
                return result

            names = _git(repo, ["diff", "--cached", "--name-only", "--", *specs], git_cfg)
            files = [line for line in names.stdout.splitlines() if line.strip()]
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            shown = ", ".join(files[:10]) + (f" (+{len(files) - 10} more)" if len(files) > 10 else "")
            subject = f"{git_cfg.commit_prefix} memory update {stamp}"
            body = f"- {len(files)} file(s): {shown}" if files else ""
            commit = _git(repo, ["commit", "-m", subject, "-m", body, "--", *specs], git_cfg)
            if commit.returncode != 0:
                result["message"] = f"git commit failed: {(commit.stderr or commit.stdout).strip()[:300]}"
                _log(repo, git_cfg, result["message"])
                return result

            sha = _git(repo, ["rev-parse", "--short", "HEAD"], git_cfg)
            result.update(
                ok=True,
                action="commit",
                commit=sha.stdout.strip(),
                files=files,
                message=f"committed {len(files)} file(s)",
            )

            pushed, push_message = _pull_and_push(repo, git_cfg)
            if git_cfg.push:
                result["action"] = "commit+push" if pushed else "commit"
                result["ok"] = pushed
                result["message"] = f"{result['message']}; {push_message}"
                _log(repo, git_cfg, f"{result['message']} ({sha.stdout.strip()})")
            else:
                result["message"] = f"{result['message']}; push disabled"
                _log(repo, git_cfg, result["message"])
            return result
        except Exception as e:  # subprocess timeout, permission error, ...
            result["message"] = f"sync error: {e}"
            _log(repo, git_cfg, result["message"])
            return result


def sync_if_due(memory_root: Path | None = None, background: bool = True) -> dict:
    """Run :func:`sync` only if the debounce window has elapsed.

    ``background=True`` (the default for the agent loop) returns immediately and
    lets a daemon thread do the network work, so a reply is never held up by a
    push. ``background=False`` is for the CLI and tests.
    """
    global _last_attempt
    git_cfg, configured_root, base = _settings()
    if not git_cfg.enabled:
        return {"ok": False, "action": "disabled", "message": "git sync disabled"}

    root = memory_root or configured_root
    with _debounce_lock:
        if _now() - _last_attempt < git_cfg.debounce_seconds:
            return {"ok": False, "action": "debounced", "message": "debounce window"}
        _last_attempt = _now()

    if background:
        threading.Thread(
            target=sync, args=(git_cfg, root, base), name="saku-vault-git", daemon=True
        ).start()
        return {"ok": True, "action": "queued", "message": "sync queued"}

    return sync(git_cfg, root, base)


_last_pull = 0.0


def pull(
    git_cfg: GitConfig | None = None, memory_root: Path | None = None, base: Path | None = None
) -> dict:
    """Fetch and rebase onto the remote branch (synchronous, never raises).

    The push path only pulls when it has something to commit. A server that runs
    for weeks without writing (or an Obsidian terminal that writes while SAKU is
    idle) needs the other direction too: human edits must arrive even when the
    agent has nothing to say.
    """
    if git_cfg is None or memory_root is None or base is None:
        git_cfg, memory_root, base = _settings()

    result = {"ok": False, "action": "skipped", "message": "", "behind": 0}
    if not git_cfg.enabled:
        result["message"] = "git sync disabled ([git] enabled = false)"
        return result

    repo = _repo_root(memory_root, git_cfg, base)
    if repo is None:
        result["message"] = f"not a git repository: {memory_root}"
        _log(memory_root, git_cfg, result["message"])
        return result
    if not memory_root.resolve().is_relative_to(repo):
        result["message"] = f"memory root {memory_root} is outside repository {repo}"
        _log(memory_root, git_cfg, result["message"])
        return result

    branch = _branch(repo, git_cfg)
    if not branch:
        result["message"] = "cannot determine branch"
        return result

    lock_path = _git_dir(repo, git_cfg) / "saku-sync.lock"
    with _Lock(lock_path, stale_after=git_cfg.timeout_seconds * 3) as acquired:
        if not acquired:
            result["message"] = "another sync is running"
            return result
        try:
            fetch = _git(repo, ["fetch", "--quiet", git_cfg.remote, branch], git_cfg)
            if fetch.returncode != 0:
                result["message"] = f"fetch failed: {(fetch.stderr or fetch.stdout).strip()[:300]}"
                _log(repo, git_cfg, result["message"])
                return result

            behind = _git(repo, ["rev-list", "--count", "HEAD..FETCH_HEAD"], git_cfg)
            try:
                count = int(behind.stdout.strip() or 0)
            except ValueError:
                count = 0

            if count == 0:
                result.update(ok=True, action="up-to-date", message="already up to date")
                return result

            rebase = _git(
                repo, ["pull", "--rebase", "--autostash", git_cfg.remote, branch], git_cfg
            )
            if rebase.returncode != 0:
                _git(repo, ["rebase", "--abort"], git_cfg)
                result["message"] = (rebase.stderr or rebase.stdout).strip()[:300]
                _log(repo, git_cfg, f"pull failed: {result['message']}")
                return result

            result.update(
                ok=True,
                action="pulled",
                behind=count,
                message=f"pulled {count} commit(s) from {git_cfg.remote}/{branch}",
            )
            _log(repo, git_cfg, result["message"])
            return result
        except Exception as e:
            result["message"] = f"pull error: {e}"
            _log(repo, git_cfg, result["message"])
            return result


def pull_if_due(memory_root: Path | None = None, background: bool = True) -> dict:
    """Fetch remote changes if ``pull_interval_seconds`` has elapsed."""
    global _last_pull
    git_cfg, configured_root, base = _settings()
    if not git_cfg.enabled:
        return {"ok": False, "action": "disabled", "message": "git sync disabled"}

    with _debounce_lock:
        if _now() - _last_pull < max(1, git_cfg.pull_interval_seconds):
            return {"ok": False, "action": "debounced", "message": "pull interval"}
        _last_pull = _now()

    root = memory_root or configured_root
    if background:
        threading.Thread(
            target=pull, args=(git_cfg, root, base), name="saku-vault-pull", daemon=True
        ).start()
        return {"ok": True, "action": "queued", "message": "pull queued"}

    return pull(git_cfg, root, base)
