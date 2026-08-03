"""Parsing and execution of tool calls (dispatch of [[TOOL]] blocks).

Takes the memory root and code root as arguments (no global dependencies).
In Phase C this will be extended as a conversion layer with MCP client/server.
"""

import importlib.util
import re
import sys
import traceback
from pathlib import Path


def _tool_candidates(name_lower: str, memory_root: Path, code_root: Path) -> list[Path]:
    return [
        memory_root / "tools" / f"{name_lower}.py",  # SAKU's own tools
        code_root / "system_tools" / f"{name_lower}.py",  # system tools
    ]


def _load_tool(name_lower: str, tool_file: Path):
    """Dynamically load the tool module and return its run function."""
    module_name = f"_saku_tool_{name_lower}"
    if module_name in sys.modules:
        del sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, tool_file)
    if spec is None:
        raise RuntimeError(f"failed to load tool: {tool_file.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _run_mcp_tool(name: str, args_str: str, body: str) -> str | None:
    """Route a tool call to an external MCP server. Returns None if not an MCP tool."""
    try:
        from .mcp import call_tool, get_tool_registry

        registry = get_tool_registry()
        server = registry.get(name) or registry.get(name.lower())
        if server is None:
            return None
        args = dict(re.findall(r'(\w+)="(.*?)"', args_str))
        result = call_tool(server, name.lower(), args)
        return f"[{name}] {result}"
    except Exception as e:
        return f"[{name}] [ERROR] MCP call failed: {e}"


def exec_tools(raw: str, memory_root: Path, code_root: Path) -> list[str]:
    """Parse and execute [[TOOL ...]] blocks.

    Also validates that valid tool start tags are properly closed with [[END]].
    Tool search order: memory/tools/ → system_tools/ → MCP tools (external servers)
    """
    results: list[str] = []

    start_pattern = r"\[\[([A-Z_]+)\s*(.*?)\]\]"
    starts = list(re.finditer(start_pattern, raw))
    parsed_ranges = []

    pattern = r"\[\[(\w+)\s*(.*?)\]\]\s*\n(.*?)\n?\[\[END\]\]"
    for m in re.finditer(pattern, raw, re.DOTALL):
        name, args_str, body = m.group(1), m.group(2), m.group(3)
        start_idx, end_idx = m.start(), m.end()
        parsed_ranges.append((start_idx, end_idx))

        name_lower = name.lower()

        tool_file = next((p for p in _tool_candidates(name_lower, memory_root, code_root) if p.exists()), None)
        if tool_file is None:
            # Fall back to MCP tools (external servers)
            mcp_result = _run_mcp_tool(name, args_str, body)
            if mcp_result is None:
                results.append(f"[ERROR] unknown tool: {name}")
            else:
                results.append(mcp_result)
            continue

        args = dict(re.findall(r'(\w+)="(.*?)"', args_str))
        path = args.get("path", "")

        try:
            module = _load_tool(name_lower, tool_file)
            extra_kwargs = {k: v for k, v in args.items() if k != "path"}
            result = module.run(memory_root, path, body.strip(), **extra_kwargs)
        except Exception as e:
            result = f"[ERROR] {e}\n{traceback.format_exc()}"

        results.append(f"[{name}] {result}")

    # Validate unclosed/invalid tool calls
    for start_match in starts:
        name = start_match.group(1)
        name_lower = name.lower()
        candidates = _tool_candidates(name_lower, memory_root, code_root)
        is_known = any(p.exists() for p in candidates)
        if not is_known:
            try:
                from .mcp import get_tool_registry

                is_known = name in get_tool_registry()
            except Exception:
                pass
        if not is_known:
            continue

        start_pos = start_match.start()
        inside_parsed = any(p_start <= start_pos < p_end for p_start, p_end in parsed_ranges)

        if not inside_parsed:
            has_end = "[[END]]" in raw[start_pos:]
            if not has_end:
                results.append(
                    f"[ERROR] Tool [[{name}]] was not closed with [[END]]. Every tool call block must end with [[END]] on its own line."
                )
            else:
                results.append(
                    f"[ERROR] Tool [[{name}]] has invalid syntax. Ensure a newline after the start tag and before [[END]]. Example:\n[[{name} path=\"...\"]]\ncontent\n[[END]]"
                )

    return results
