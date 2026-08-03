"""DELEGATE tool: hand a task to a child (sub-agent) and return its result.

Usage: [[DELEGATE child="mei"]]
task description here
[[END]]
"""

from pathlib import Path

_CODE_ROOT = Path(__file__).resolve().parent.parent


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    from saku.subagent import delegate

    name = kwargs.get("child", "") or path
    task = kwargs.get("task", "") or body
    return delegate(base, _CODE_ROOT, name, task)
