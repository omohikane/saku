"""Run limited git commands in the project root."""

import subprocess
from pathlib import Path

ALLOWED_COMMANDS = {
    "status",
    "diff",
    "add",
    "commit",
    "log",
    "branch",
}

BLOCKED_SUBSTRINGS = [
    "push",
    "pull",
    "fetch",
    "reset",
    "checkout",
    "rebase",
    "merge",
    "remote",
    "tag",
    "stash",
    "clean",
    "submodule",
]

_TIMEOUT = 15


def _validate_command(cmd_parts: list[str]) -> bool:
    if not cmd_parts:
        return False
    if cmd_parts[0] not in ALLOWED_COMMANDS:
        return False
    for part in cmd_parts:
        for blocked in BLOCKED_SUBSTRINGS:
            if blocked in part.lower():
                return False
    return True


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    git_args = body.strip()
    if not git_args:
        return "[ERROR] git command is required (put in body, e.g. status)"

    cmd_parts = git_args.split()
    if not _validate_command(cmd_parts):
        return f"[DENY] git command not allowed: {git_args}"

    project_root = base.parent.resolve()
    git_root = project_root / ".git"

    if not git_root.exists():
        return "[ERROR] not a git repository"

    full_cmd = ["git"] + cmd_parts
    print(f"[*] git {' '.join(cmd_parts)}")

    try:
        proc = subprocess.run(
            full_cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        output = []
        if proc.stdout:
            output.append(proc.stdout.strip())
        if proc.stderr:
            output.append(f"[STDERR]\n{proc.stderr.strip()}")
        result = "\n".join(output).strip()
        if proc.returncode != 0:
            result = f"[exit: {proc.returncode}]\n{result}"
        return result or f"(git {git_args} completed with no output)"

    except subprocess.TimeoutExpired:
        return "[ERROR] git command timed out"
    except FileNotFoundError:
        return "[ERROR] git executable not found"
    except Exception as e:
        return f"[ERROR] git failed: {e}"
