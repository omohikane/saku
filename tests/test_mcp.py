#!/usr/bin/env python3
"""
Tests for saku.mcp (MCP client) against a local stdio MCP server.
"""

import sys
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import mcp


def _test_server() -> mcp.McpServer:
    return mcp.McpServer(
        name="test",
        command=sys.executable,
        args=[str(CODE_ROOT / "test_mcp_server.py")],
        cwd=str(CODE_ROOT),
    )


def test_list_tools():
    server = _test_server()
    tools = mcp.list_tools(server)
    names = {t.name for t in tools}
    assert "add" in names
    assert "echo" in names


def test_call_tool_add():
    server = _test_server()
    result = mcp.call_tool(server, "add", {"a": 1, "b": 2})
    assert "sum=3" in result


def test_call_tool_echo():
    server = _test_server()
    result = mcp.call_tool(server, "echo", {"text": "hello"})
    assert result.strip() == "hello"


def test_describe_tool():
    server = _test_server()
    tools = mcp.list_tools(server)
    desc = mcp.describe_tool(tools[0])
    assert "add" in desc
    assert "args" in desc


def test_load_servers_from_config():
    cfg = {"mcp": {"servers": {"ha": {"url": "http://x/mcp"}, "local": {"command": "npx", "args": ["-y", "srv"]}}}}
    servers = mcp.load_servers(cfg)
    assert len(servers) == 2
    assert servers[0].url == "http://x/mcp"
    assert servers[1].command == "npx"
    assert servers[1].args == ["-y", "srv"]


def test_exec_tools_routes_to_mcp():
    """[[TOOL]] blocks for MCP tools are dispatched to the external server."""
    import tempfile

    import saku.mcp as mcp_mod
    import saku.transport as transport

    server = _test_server()
    original = mcp_mod.get_tool_registry
    mcp_mod.get_tool_registry = lambda: {"add": server}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = transport.exec_tools('[[ADD a="1" b="2"]]\n[[END]]', root, CODE_ROOT)
    finally:
        mcp_mod.get_tool_registry = original
    assert any("sum=3" in r for r in results), results


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} MCP client tests PASSED!")


if __name__ == "__main__":
    run_tests()
