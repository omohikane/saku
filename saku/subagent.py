"""Sub-agent (child) support: spawn and delegate.

Children are defined as Markdown files in ``memory/children/<name>.md`` (written by
SPAWN_CHILD). DELEGATE runs the shared agent loop with the child's identity and
returns its result. A delegation depth limit prevents infinite recursion.
"""

import threading
from pathlib import Path

MAX_DELEGATION_DEPTH = 3

_depth_lock = threading.Lock()
_delegation_depth = 0


def spawn_child(memory_root: Path, name: str, genome: str) -> str:
    """Create a child agent definition at memory/children/<name>.md."""
    name = name.strip()
    if not name:
        return "[ERROR] child name required"
    if not genome.strip():
        return "[ERROR] child role/genome required"
    children_dir = memory_root / "children"
    children_dir.mkdir(parents=True, exist_ok=True)
    p = children_dir / f"{name}.md"
    p.write_text(f"# {name}\n\n{genome.strip()}\n", encoding="utf-8")
    return f"[OK] child agent created: {p.name}"


def load_child_genome(memory_root: Path, name: str) -> str | None:
    p = memory_root / "children" / f"{name}.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def build_child_prompt(name: str, genome: str) -> str:
    """Compact system prompt for a child sub-agent (minimal, like OpenClaw's promptMode)."""
    return f"""You are a sub-agent of SAKU named {name}.

{genome}

Rules:
- Respond in the same language as the delegated task (default Japanese).
- You may use the memory tools ([[READ_FILE]], [[WRITE_FILE]], [[APPEND_FILE]],
  [[LIST_DIR]], [[SEARCH_NOTES]], [[WEB_SEARCH]]) to accomplish the task.
- Tool calls use the format: [[TOOL_NAME args]]\\n...\\n[[END]].
- Do not hallucinate. If you cannot do something, say so clearly.
- Output only your final result.
"""


def delegate(memory_root: Path, code_root: Path, name: str, task: str, llm_cfg=None) -> str:
    """Run a child agent on the delegated task and return its visible result."""
    global _delegation_depth
    with _depth_lock:
        if _delegation_depth >= MAX_DELEGATION_DEPTH:
            return "[ERROR] delegation depth limit exceeded"
        _delegation_depth += 1
    try:
        genome = load_child_genome(memory_root, name)
        if genome is None:
            return f"[ERROR] child agent not found: {name}"
        if not task.strip():
            return "[ERROR] task is empty"

        from saku import core as agent
        from saku.agent_loop import run_agent_loop

        # Use sub LLM instance if configured (separate VRAM/queue for sub-tasks)
        if llm_cfg is None:
            try:
                from saku.config import load_config, load_llm_instance

                cfg, _ = load_config()
                sub_cfg = load_llm_instance(cfg, "sub")
                # Only use sub instance if it has a distinct api_url
                if sub_cfg.api_url and sub_cfg.api_url != agent._current_llm.api_url:
                    llm_cfg = sub_cfg
                else:
                    llm_cfg = agent._current_llm
            except Exception:
                llm_cfg = agent._current_llm

        system_prompt = build_child_prompt(name, genome)
        history = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task.strip()},
        ]
        result = run_agent_loop(
            history,
            llm_cfg or agent._current_llm,
            agent.context_config,
            memory_root,
            code_root,
            max_turns=5,
        )
        return result.visible or "(child returned no output)"
    finally:
        with _depth_lock:
            _delegation_depth -= 1
