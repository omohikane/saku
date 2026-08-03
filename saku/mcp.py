"""MCP client for SAKU — connect to external MCP servers and call their tools.

Reads ``[mcp.servers.*]`` from config.toml. Each server is either:
- ``url``: a Streamable HTTP endpoint (e.g. Home Assistant)
- ``command``: a local stdio command (e.g. ``npx some-mcp-server``)

Tools are discovered via ``tools/list`` and executed via ``tools/call``, then
exposed to the agent as callable tools.

The ``mcp`` package is an optional dependency (``pip install saku[mcp]``) and is
imported lazily so the core runs without it.
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class McpServer:
    name: str
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    headers: dict = field(default_factory=dict)


def load_servers(cfg: dict) -> list[McpServer]:
    """Load MCP server definitions from [mcp.servers]."""
    servers = []
    for name, conf in cfg.get("mcp", {}).get("servers", {}).items():
        servers.append(
            McpServer(
                name=name,
                url=conf.get("url", ""),
                command=conf.get("command", ""),
                args=list(conf.get("args", [])),
                cwd=conf.get("cwd"),
                headers=conf.get("headers", {}),
            )
        )
    return servers


async def _server_streams(server: McpServer):
    """Return an async context manager yielding (read, write) for the transport."""
    if server.url:
        from mcp.client.streamable_http import streamable_http_client

        return streamable_http_client(server.url, headers=server.headers or None)

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server.command,
        args=server.args,
        cwd=server.cwd,
    )
    return stdio_client(params)


async def _list_tools_async(server: McpServer):
    from mcp import ClientSession

    streams = await _server_streams(server)
    async with streams as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return tools.tools


async def _call_tool_async(server: McpServer, tool_name: str, arguments: dict):
    from mcp import ClientSession

    streams = await _server_streams(server)
    async with streams as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            texts = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(texts) if texts else str(result)


def list_tools(server: McpServer):
    """Return tool definitions for a server (sync bridge)."""
    return asyncio.run(_list_tools_async(server))


def call_tool(server: McpServer, tool_name: str, arguments: dict) -> str:
    """Call a tool on a server and return its text result (sync bridge)."""
    return asyncio.run(_call_tool_async(server, tool_name, arguments))


def describe_tool(t) -> str:
    """One-line description of an MCP tool for the prompt."""
    schema = getattr(t, "input_schema", None) or {}
    props = list((schema.get("properties") or {}).keys())
    desc = (getattr(t, "description", "") or "").replace("\n", " ")
    line = f"- {t.name}: {desc}"
    if props:
        line += f" (args: {', '.join(props)})"
    return line


def discover_tool_descriptions(servers: list[McpServer]) -> str:
    """Prompt-ready list of all MCP tools across configured servers."""
    lines = []
    for server in servers:
        try:
            tools = list_tools(server)
        except Exception as e:
            lines.append(f"- [{server.name}] (unreachable: {e})")
            continue
        for t in tools:
            lines.append(f"- [{server.name}] {describe_tool(t)}")
    return "\n".join(lines)


def build_tool_registry(servers: list[McpServer]) -> dict[str, McpServer]:
    """Map MCP tool names to their server (for transport dispatch)."""
    registry: dict[str, McpServer] = {}
    for server in servers:
        try:
            for t in list_tools(server):
                registry[t.name] = server
        except Exception as e:
            print(f"[mcp] failed to list tools from {server.name}: {e}")
    return registry


_registry_cache: dict[str, McpServer] | None = None
_descriptions_cache: str = ""


def _load():
    from .config import load_config

    cfg, _ = load_config()
    return load_servers(cfg)


def get_tool_registry() -> dict[str, McpServer]:
    """Return the cached MCP tool registry (tool_name -> server)."""
    global _registry_cache
    if _registry_cache is None:
        _registry_cache = build_tool_registry(_load())
    return _registry_cache


def get_tool_descriptions() -> str:
    """Return the cached prompt description of MCP tools."""
    global _descriptions_cache
    if not _descriptions_cache:
        _descriptions_cache = discover_tool_descriptions(_load())
    return _descriptions_cache
