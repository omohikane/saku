"""SPAWN_CHILD tool: create a child (sub-agent) definition.

Usage: [[SPAWN_CHILD name="mei"]]
role/genome content here
[[END]]
"""

from pathlib import Path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    from saku.subagent import spawn_child

    name = kwargs.get("name", "") or path
    genome = kwargs.get("genome", "") or body
    return spawn_child(base, name, genome)
