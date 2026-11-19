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


def test_dependency_range_matches_the_tested_api():
    """#132: the extra used to allow 1.x, whose server API differs."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    spec = data["project"]["optional-dependencies"]["mcp"][0]
    assert ">=2" in spec, spec


def test_registry_keeps_server_qualified_names_and_schemas():
    from types import SimpleNamespace

    def fake_list(server):
        return [
            SimpleNamespace(
                name="getState",
                description="state",
                input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
            )
        ]

    original = mcp.list_tools
    mcp.list_tools = fake_list
    try:
        registry = mcp.build_tool_registry(
            [mcp.McpServer(name="alpha"), mcp.McpServer(name="beta")]
        )
    finally:
        mcp.list_tools = original

    assert "alpha/getState" in registry and "beta/getState" in registry
    assert registry["alpha/getState"].name == "getState"
    assert registry["alpha/getState"].input_schema["properties"]["id"]["type"] == "string"
    # A bare name stays available only as the first server's binding.
    assert registry["getState"].server.name == "alpha"


def test_dispatch_passes_typed_json_arguments():
    import tempfile
    from pathlib import Path

    import saku.mcp as mcp_mod
    import saku.transport as transport

    schema = {
        "type": "object",
        "properties": {
            "flag": {"type": "boolean"},
            "items": {"type": "array"},
            "mode": {"type": "string"},
        },
        "required": ["flag"],
    }
    binding = mcp.McpToolBinding(mcp.McpServer(name="svc"), "doThing", schema)
    captured = {}
    original_registry = mcp_mod.get_tool_registry
    original_call = mcp_mod.call_tool
    mcp_mod.get_tool_registry = lambda: {"svc/doThing": binding}
    mcp_mod.call_tool = lambda server, name, args: captured.update(
        server=server.name, name=name, args=args
    ) or "ok"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            results = transport.exec_tools(
                '[[svc/doThing]]\n{"flag": true, "items": [1, 2], "mode": "x"}\n[[END]]',
                Path(tmp),
                CODE_ROOT,
            )
    finally:
        mcp_mod.get_tool_registry = original_registry
        mcp_mod.call_tool = original_call

    assert captured["name"] == "doThing", "case-sensitive tool name was changed"
    assert captured["args"] == {"flag": True, "items": [1, 2], "mode": "x"}
    assert any("ok" in r for r in results)


def test_dispatch_coerces_kwargs_to_schema_types():
    import tempfile
    from pathlib import Path

    import saku.mcp as mcp_mod
    import saku.transport as transport

    schema = {
        "type": "object",
        "properties": {"flag": {"type": "boolean"}, "count": {"type": "integer"}},
        "required": ["flag"],
    }
    binding = mcp.McpToolBinding(mcp.McpServer(name="svc"), "run", schema)
    captured = {}
    original_registry = mcp_mod.get_tool_registry
    original_call = mcp_mod.call_tool
    mcp_mod.get_tool_registry = lambda: {"svc/run": binding}
    mcp_mod.call_tool = lambda server, name, args: captured.update(args=args) or "ok"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            transport.exec_tools(
                '[[svc/run flag="true" count="3"]]\n[[END]]', Path(tmp), CODE_ROOT
            )
    finally:
        mcp_mod.get_tool_registry = original_registry
        mcp_mod.call_tool = original_call

    assert captured["args"] == {"flag": True, "count": 3}


def test_dispatch_validates_arguments_before_calling():
    import tempfile
    from pathlib import Path

    import saku.mcp as mcp_mod
    import saku.transport as transport

    schema = {
        "type": "object",
        "properties": {"flag": {"type": "boolean"}},
        "required": ["flag"],
    }
    binding = mcp.McpToolBinding(mcp.McpServer(name="svc"), "run", schema)
    called = []
    original_registry = mcp_mod.get_tool_registry
    original_call = mcp_mod.call_tool
    mcp_mod.get_tool_registry = lambda: {"svc/run": binding}
    mcp_mod.call_tool = lambda *a, **k: called.append(a) or "ok"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = transport.exec_tools('[[svc/run]]\n{}\n[[END]]', root, CODE_ROOT)
            wrong = transport.exec_tools(
                '[[svc/run]]\n{"flag": "yes"}\n[[END]]', root, CODE_ROOT
            )
    finally:
        mcp_mod.get_tool_registry = original_registry
        mcp_mod.call_tool = original_call

    assert any("missing required argument" in r for r in missing), missing
    assert any("must be boolean" in r for r in wrong), wrong
    assert called == [], "invalid arguments reached the server"


def test_complete_mcp_blocks_are_stripped_from_visible_text():
    from saku import thinking

    out = thinking.strip_tool_blocks('[[svc/doThing]]\nx\n[[END]]\n結果です')
    assert out == "結果です"
    # An unclosed double-bracket link is prose, not a tool call.
    assert thinking.strip_tool_blocks("[[memory]] の話") == "[[memory]] の話"


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} MCP client tests PASSED!")


if __name__ == "__main__":
    run_tests()
