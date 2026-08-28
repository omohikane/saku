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
        code_root / "tools" / f"{name_lower}.py",  # built-in tools (saku/tools)
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


def _normalize_alternative_calls(raw: str) -> str:
    """Convert common hallucinated tool syntaxes to [[TOOL]] form (refs #28).

    Handles: <|tool_call>call:WEB_SEARCH{query: \"...\"}, call:WEB_SEARCH, etc.
    """
    # <|tool_call>call:WEB_SEARCH{query: "foo"} or {"query": "foo"}
    def _repl_websearch(m):
        q = m.group(1) or m.group(2) or ""
        q = q.strip().strip('"').strip("'")
        return f"[[WEB_SEARCH]]\n{q}\n[[END]]"

    # Pattern 1: call:WEB_SEARCH{query: "..."}  or  call:saku:WEB_SEARCH{query: "..."}
    raw = re.sub(
        r"(?:<\|tool_call\|>)?\s*call:\s*(?:saku:)?WEB_SEARCH\s*\{\s*query\s*:\s*\"([^\"]+)\"\s*\}",
        _repl_websearch,
        raw,
    )
    raw = re.sub(
        r"(?:<\|tool_call\|>)?\s*call:\s*(?:saku:)?WEB_SEARCH\s*\{\s*query\s*:\s*'([^']+)'\s*\}",
        _repl_websearch,
        raw,
    )
    # Pattern 1b: without query key, direct string {\"...\"}
    raw = re.sub(
        r"(?:<\|tool_call\|>)?\s*call:\s*(?:saku:)?WEB_SEARCH\s*\{\s*\"([^\"]+)\"\s*\}",
        _repl_websearch,
        raw,
    )
    # Pattern 2: WEB_SEARCH(query="...") or WEB_SEARCH{\"query\": \"...\"}
    raw = re.sub(
        r"WEB_SEARCH\s*\(\s*query\s*=\s*\"([^\"]+)\"\s*\)",
        _repl_websearch,
        raw,
    )
    raw = re.sub(
        r"WEB_SEARCH\s*\{\s*\"query\"\s*:\s*\"([^\"]+)\"\s*\}",
        _repl_websearch,
        raw,
    )
    # Bare <|tool_call|> markers to visible
    raw = raw.replace("<|tool_call|>", "").replace("<tool_call>", "")
    return raw


def exec_tools(raw: str, memory_root: Path, code_root: Path) -> list[str]:
    """Parse and execute [[TOOL ...]] blocks.

    Also validates that valid tool start tags are properly closed with [[END]].
    Tool search order: memory/tools/ → saku/tools/ → MCP tools (external servers)
    """
    # Normalize hallucinated formats before parsing (refs #28)
    raw = _normalize_alternative_calls(raw)
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
        args_str = start_match.group(2)
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

        if inside_parsed:
            continue

        has_end = "[[END]]" in raw[start_pos:]
        if has_end:
            results.append(
                f"[ERROR] Tool [[{name}]] has invalid syntax. Ensure a newline after the start tag and before [[END]]. Example:\n[[{name} path=\"...\"]]\ncontent\n[[END]]"
            )
            continue

        # The model forgot the closing [[END]] tag. Auto-close it: treat everything
        # after the start tag (up to the next [[ ) as the body and run the tool.
        # This prevents the "not closed with [[END]]" error from being fed back into
        # history, which previously made the model retry the same tool forever.
        tail = raw[start_match.end():]
        next_start = tail.find("[[")
        body = tail[: next_start if next_start != -1 else None].strip()

        tool_file = next((p for p in candidates if p.exists()), None)
        try:
            if tool_file is not None:
                args = dict(re.findall(r'(\w+)="(.*?)"', args_str))
                path = args.get("path", "")
                extra_kwargs = {k: v for k, v in args.items() if k != "path"}
                module = _load_tool(name_lower, tool_file)
                result = module.run(memory_root, path, body, **extra_kwargs)
            else:
                mcp_result = _run_mcp_tool(name, args_str, body)
                if mcp_result is None:
                    result = f"[ERROR] unknown tool: {name}"
                else:
                    result = mcp_result
            results.append(f"[{name}] {result}")
        except Exception as e:
            results.append(f"[{name}] [ERROR] {e}\n{traceback.format_exc()}")

    return results
