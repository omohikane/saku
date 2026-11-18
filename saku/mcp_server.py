"""MCP server exposing SAKU's memory/tools to external MCP clients.

Auth: Bearer token from ``[mcp.server]`` config. Scope: the same ``[paths]``
PathPolicy that governs SAKU's own tools, so external clients cannot exceed
SAKU's powers (they use the same read/write allowed lists).

The server exposes read/write/list/search tools over the memory root, letting
other agents (e.g. a stronger cloud LLM) read and extend SAKU's memory.

It also exposes a ``talk`` tool (#91): synchronous conversation with SAKU for
other agents. Talk has its own credential class — per-caller Bearer tokens
from ``[talk] callers`` — kept strictly separate from the server token: a
talk token cannot reach the memory tools, and the server token is not a
talk identity. See ``saku/talk.py`` for the conversation discipline.
"""

import hmac
import importlib.util
import sys
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

_CODE_ROOT = Path(__file__).resolve().parent  # saku/ (code package)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import core as agent
from saku import talk as talk_mod

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
    Tool(
        name="talk",
        description=(
            "Hold a synchronous conversation with SAKU (agent-to-agent). "
            "HTTP transport only: the caller's identity is bound to a [talk] "
            "caller Bearer token — never to any argument. Arguments other "
            "than 'message' are ignored."
        ),
        inputSchema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    ),
]

_MEMORY_TOOLS = {"read_file", "list_dir", "write_file", "append_file", "search_notes"}


def _call_system_tool(name: str, args: dict) -> str:
    """Load and run the matching system tool against the memory root (PathPolicy scoped)."""
    tool_path = _CODE_ROOT / "tools" / f"{name}.py"
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


def _handle_talk(engine, presented: str, args: dict) -> "tuple[str, bool]":
    """Run one talk call. Returns (text, is_error).

    Identity comes from the token alone: any from/name/caller argument in
    the request body is deliberately ignored (naming yourself proves
    nothing, #73).
    """
    if engine is None:
        return (
            "[ERROR] talk is not available on this server "
            "(no [talk] callers configured)",
            True,
        )
    caller = engine.authenticate(presented) if presented else None
    if caller is None:
        engine.audit_auth_rejected("tool")
        return "[ERROR] unauthorized: talk requires a valid caller token", True
    message = args.get("message", "")
    if not isinstance(message, str):
        message = str(message)
    try:
        reply = engine.talk(caller, message)
    except talk_mod.TalkRejected as e:
        return f"[ERROR] rejected: {e}", True
    except talk_mod.TalkFailed as e:
        return f"[ERROR] {e}", True
    return reply, False


def _request_headers(ctx):
    """Best-effort access to the inbound HTTP headers for a tool call.

    The lowlevel handler context carries the Starlette request on
    ``ctx.request`` for streamable HTTP; a TransportContext with headers
    (other transports) is the fallback. Stdio has neither — the result is
    empty, talk then has no credential and is refused, as designed.
    """
    request = getattr(ctx, "request", None)
    headers = getattr(request, "headers", None) if request is not None else None
    if headers is None:
        transport = getattr(ctx, "transport", None)
        headers = getattr(transport, "headers", None) if transport is not None else None
    return headers or {}


def build_server(talk_engine=None, server_token: str = "") -> Server:
    async def on_list_tools(ctx, params):
        return ListToolsResult(tools=_TOOL_SPECS)

    async def on_call_tool(ctx, params):
        name = params.name
        args = params.arguments or {}
        presented = talk_mod.extract_bearer(_request_headers(ctx))

        if name == "talk":
            text, is_error = _handle_talk(talk_engine, presented, args)
            return CallToolResult(
                content=[TextContent(type="text", text=text)], isError=is_error
            )
        if name not in _MEMORY_TOOLS:
            return CallToolResult(
                content=[TextContent(type="text", text=f"[ERROR] unknown tool: {name}")],
                isError=True,
            )
        if server_token and not hmac.compare_digest(
            presented.encode("utf-8"), server_token.encode("utf-8")
        ):
            # Default-deny: when a server token is configured, ONLY it may
            # reach the memory tools. A talk caller token is a conversation
            # credential, not a memory credential.
            if talk_engine is not None and presented:
                caller = talk_engine.authenticate(presented)
                if caller is not None:
                    talk_engine.audit_wrong_token_class(caller)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="[ERROR] unauthorized: memory tools require the server token",
                    )
                ],
                isError=True,
            )
        try:
            result = _call_system_tool(name, args)
        except Exception as e:
            result = f"[ERROR] {e}"
        return CallToolResult(content=[TextContent(type="text", text=result)])

    return Server("saku", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def _auth_middleware(app, tokens, on_reject=None):
    """ASGI middleware requiring a known Bearer token on HTTP requests.

    ``tokens`` is the union of the server token and any talk caller tokens:
    the middleware is the coarse gate (is this request authenticated at
    all?), while the tool handler enforces which token class may use which
    tool. Both layers parse the header with the same function
    (``talk.extract_bearer``) so they can never disagree about the token a
    request presented.
    """
    valid = [t for t in tokens if t]

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        presented = talk_mod.extract_bearer(headers)
        ok = False
        for token in valid:
            if presented and hmac.compare_digest(
                presented.encode("utf-8"), token.encode("utf-8")
            ):
                ok = True
        if not ok:
            if on_reject is not None:
                try:
                    on_reject()
                except Exception:
                    pass  # logging must never gate the auth decision
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


def make_app(server: Server, token: str = "", extra_tokens=(), on_auth_reject=None):
    """Return the Starlette ASGI app (with optional Bearer-token auth).

    ``token`` is the [mcp.server] operator token; ``extra_tokens`` are talk
    caller tokens, which pass the HTTP gate but are scoped to the talk tool
    by the handler.
    """
    app = server.streamable_http_app()
    tokens = ([token] if token else []) + [t for t in extra_tokens if t]
    if tokens:
        app = _auth_middleware(app, tokens, on_reject=on_auth_reject)
    return app


def serve(host: str = "127.0.0.1", port: int = 8765, token: str = "", talk_engine=None) -> None:
    """Serve the MCP server (blocking)."""
    import uvicorn

    server = build_server(talk_engine, server_token=token)
    extra = list(talk_engine.caller_tokens()) if talk_engine is not None else []
    on_reject = (
        (lambda: talk_engine.audit_auth_rejected("http"))
        if talk_engine is not None
        else None
    )
    app = make_app(server, token, extra_tokens=extra, on_auth_reject=on_reject)
    print(f"SAKU MCP server on http://{host}:{port}/mcp")
    print(f"Token auth: {'on' if (token or extra) else 'OFF'}")
    print(f"Talk: {'on (%d caller(s))' % len(extra) if talk_engine is not None else 'off'}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    from saku import config as saku_config

    cfg, base = saku_config.load_config()
    srv = cfg.get("mcp", {}).get("server", {})
    if not srv.get("enable", False):
        print("[saku] MCP server is disabled ([mcp.server] enable = false).")
        return
    host = srv.get("bind", "127.0.0.1")
    port = int(srv.get("port", 8765))
    token = srv.get("token", "")

    talk_engine = None
    if saku_config.load_talk_config(cfg).callers:
        memory_root = saku_config.resolve_memory_root(cfg, base)
        llm_cfg = saku_config.load_active_llm(cfg)
        talk_engine = talk_mod.TalkEngine(
            cfg, memory_root, llm_cfg=llm_cfg, reserved_tokens=(token,)
        )
    serve(host, port, token, talk_engine=talk_engine)


if __name__ == "__main__":
    main()
