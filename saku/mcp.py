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
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class McpServer:
    name: str
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    headers: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)


@dataclass
class McpToolBinding:
    """A discovered MCP tool: which server, its original name, its schema.

    The registry used to map ``tool name -> server``. Bare names collide across
    servers (last one won) and the schema was unavailable at dispatch time, so
    typed arguments could not be validated (#132).
    """

    server: McpServer
    name: str
    input_schema: dict = field(default_factory=dict)

    @property
    def qualified(self) -> str:
        return f"{self.server.name}/{self.name}"


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
                env=conf.get("env", {}),
            )
        )
    return servers


async def _server_streams(server: McpServer):
    """Return an async context manager yielding (read, write) for the transport."""
    if server.url:
        from mcp.client.streamable_http import streamable_http_client

        if server.headers:
            from mcp.client.streamable_http import create_mcp_http_client

            http_client = create_mcp_http_client(headers=server.headers)

            @asynccontextmanager
            async def _authed():
                try:
                    async with streamable_http_client(server.url, http_client=http_client) as streams:
                        yield streams
                finally:
                    await http_client.aclose()

            return _authed()
        return streamable_http_client(server.url)

    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server.command,
        args=server.args,
        cwd=server.cwd,
        env=server.env or None,
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
            # Advertise the qualified form: a bare name is ambiguous when two
            # servers expose the same tool (#132), and dispatch accepts both.
            line = describe_tool(t).lstrip("- ")
            lines.append(f"- {server.name}/{line}")
    return "\n".join(lines)


def build_tool_registry(servers: list[McpServer]) -> dict[str, McpToolBinding]:
    """Map MCP tools to bindings, addressable as ``server/name`` or bare name.

    ``server/name`` is always present and collision-free. A bare name is added
    only when it is still unambiguous, so an existing ``[[ADD]]`` keeps working
    while a second server with the same tool can only be reached qualified.
    """
    registry: dict[str, McpToolBinding] = {}
    for server in servers:
        try:
            for t in list_tools(server):
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                binding = McpToolBinding(server=server, name=t.name, input_schema=schema)
                registry[binding.qualified] = binding
                registry.setdefault(t.name, binding)
        except Exception as e:
            print(f"[mcp] failed to list tools from {server.name}: {e}")
    return registry


_JSON_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def coerce_arguments(schema: dict, arguments: dict) -> dict:
    """Coerce string kwargs to the schema's types (``a="1"`` -> integer 1)."""
    props = (schema or {}).get("properties") or {}
    out = dict(arguments)
    for key, value in list(out.items()):
        spec = props.get(key) or {}
        kind = spec.get("type")
        if not isinstance(value, str):
            continue
        try:
            if kind == "integer":
                out[key] = int(value)
            elif kind == "number":
                out[key] = float(value)
            elif kind == "boolean":
                out[key] = value.strip().lower() in ("1", "true", "yes", "on")
            elif kind in ("array", "object"):
                out[key] = json.loads(value)
        except (ValueError, json.JSONDecodeError):
            pass  # validate_arguments reports the mismatch with a clear message
    return out


def validate_arguments(schema: dict, arguments: dict) -> str | None:
    """Check typed arguments against the tool's input schema before calling."""
    if not isinstance(schema, dict) or not schema:
        return None
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in arguments:
            return f"missing required argument: {key}"
    for key, value in arguments.items():
        spec = props.get(key)
        if not spec:
            if schema.get("additionalProperties") is False:
                return f"unknown argument: {key}"
            continue
        kind = spec.get("type")
        expected = _JSON_TYPES.get(kind)
        if expected is None:
            continue
        # bool is an int subclass; a boolean where a number is expected is a bug.
        if kind in ("number", "integer") and isinstance(value, bool):
            return f"argument {key} must be {kind}, not boolean"
        if not isinstance(value, expected):
            return f"argument {key} must be {kind}"
    return None


_registry_cache: dict[str, McpServer] | None = None
_descriptions_cache: str = ""


def _load():
    from .config import load_config, resolve_plugins_root
    from .plugins import load_mcp_servers, load_plugins

    cfg, config_base = load_config()
    servers = load_servers(cfg)

    plugins_root = resolve_plugins_root(cfg, config_base)
    enabled = cfg.get("plugins", {}).get("enabled")
    if isinstance(enabled, list) and enabled:
        enabled_set: list[str] | None = enabled
    else:
        enabled_set = None
    plugins, errors = load_plugins(plugins_root, enabled=enabled_set)
    for dir_, err in errors:
        print(f"[plugins] skipped {dir_.name}: {err}")
    if enabled_set is not None and not plugins and not errors:
        # No matching plugin found — still report for diagnostics
        for name in enabled_set:
            if not (plugins_root / name).is_dir():
                print(f"[plugins] enabled plugin not found: {name}")
    for plugin in plugins:
        data_dir = _plugin_data_dir(plugins_root, plugin.name)
        mcp_servers, reports = load_mcp_servers(plugin, data_dir)
        servers.extend(mcp_servers)
        for report in reports:
            print(f"[plugins] {report}")
    return servers


def _plugin_data_dir(plugins_root: Path, name: str) -> Path:
    """Plugins' writable data lives outside the plugin package, under
    ``<plugins_root>/.data/<name>`` (the ${PLUGIN_DATA} root)."""
    return plugins_root / ".data" / name


def get_tool_registry() -> dict[str, McpToolBinding]:
    """Return the cached MCP tool registry (``server/name`` / bare -> binding)."""
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
