"""MCP server exposing SAKU's memory/tools to external MCP clients.

Auth: Bearer token from ``[mcp.server]`` config. Scope: the same ``[paths]``
PathPolicy that governs SAKU's own tools, so external clients cannot exceed
SAKU's powers (they use the same read/write allowed lists).

The server exposes read/write/list/search tools over the memory root, letting
other agents (e.g. a stronger cloud LLM) read and extend SAKU's memory.
"""

import importlib.util
import sys
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

_CODE_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import saku_core as agent

_TOOL_SPECS = [
    Tool(
        name="read_file",
        description="Read a file within the memory root (path is relative to the memory root).",
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    ),
    Tool(
        name="list_dir",
        description="List files and directories within the memory root.",
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    Tool(
        name="write_file",
        description="Write a file in an allowed memory directory (blocks meta.md etc.).",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    Tool(
        name="append_file",
        description="Append content to a file (meta.md requires the heading parameter).",
        inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "heading": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    Tool(
        name="search_notes",
        description="Search the memory root for a keyword.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
]


def _call_system_tool(name: str, args: dict) -> str:
    """Load and run the matching system tool against the memory root (PathPolicy scoped)."""
    tool_path = _CODE_ROOT / "system_tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_mcp_tool_{name}", tool_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"_mcp_tool_{name}"] = module
    spec.loader.exec_module(module)

    path = args.get("path", "")
    body = args.get("content", "")
    if name == "search_notes":
        body = args.get("query", "")
    kwargs = {}
    if "heading" in args:
        kwargs["heading"] = args["heading"]
    return module.run(agent.SAKU_ROOT, path, body, **kwargs)


def build_server() -> Server:
    async def on_list_tools(ctx, params):
        return ListToolsResult(tools=_TOOL_SPECS)

    async def on_call_tool(ctx, params):
        name = params.name
        args = params.arguments or {}
        try:
            result = _call_system_tool(name, args)
        except Exception as e:
            result = f"[ERROR] {e}"
        return CallToolResult(content=[TextContent(type="text", text=result)])

    return Server("saku", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def _auth_middleware(app, token: str):
    """ASGI middleware requiring 'Authorization: Bearer <token>' on HTTP requests."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        if headers.get("authorization", "") != f"Bearer {token}":
            body = b"unauthorized"
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return wrapped


def make_app(server: Server, token: str = ""):
    """Return the Starlette ASGI app (with optional Bearer-token auth)."""
    app = server.streamable_http_app()
    if token:
        app = _auth_middleware(app, token)
    return app


def serve(host: str = "127.0.0.1", port: int = 8765, token: str = "") -> None:
    """Serve the MCP server (blocking)."""
    import uvicorn

    server = build_server()
    app = make_app(server, token)
    print(f"SAKU MCP server on http://{host}:{port}/mcp")
    print(f"Token auth: {'on' if token else 'OFF'}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    from saku import config as saku_config

    cfg, _ = saku_config.load_config()
    srv = cfg.get("mcp", {}).get("server", {})
    if not srv.get("enable", False):
        print("[saku] MCP server is disabled ([mcp.server] enable = false).")
        return
    host = srv.get("bind", "127.0.0.1")
    port = int(srv.get("port", 8765))
    token = srv.get("token", "")
    serve(host, port, token)


if __name__ == "__main__":
    main()
