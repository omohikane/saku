"""Agent Plugins 1.0.0 loader — discover and validate plugin manifests.

Implements Phase 1 of the Agent Plugins support (see issue #20):
- plugin directory discovery
- ``plugin.json`` manifest parsing and validation per the
  `Agent Plugins 1.0.0 specification <https://agent-plugins.org/specification>`_

The loading rules follow the spec strictly:

- ``$schema`` and ``name`` are required. Any missing / wrongly-typed required
  field rejects the plugin (spec §5.3).
- The manifest schema is closed: unknown top-level fields are reported and
  ignored, but do not reject the plugin (spec §5.2).
- An unsupported ``$schema`` version rejects the plugin (spec §5.2).

Components:

- ``skills/`` (Agent Skills) and ``mcp.json`` (MCP servers) are loaded per the
  spec §6/§7. Skills discovery is a later phase; MCP servers are supported.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from saku.mcp import McpServer

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

PLUGIN_ROOT = "${PLUGIN_ROOT}"
PLUGIN_DATA = "${PLUGIN_DATA}"

STDIO_FIELDS = {"type", "command", "args", "env", "cwd"}
HTTP_FIELDS = {"type", "url", "headers"}

# Allowed manifest top-level fields (spec §5.2). Anything else is reported+ignored.
MANIFEST_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}

AUTHOR_FIELDS = {"name", "email", "url"}

NAME_MAX_LEN = 64
_NAME_CHARS = re.compile(r"^[a-z0-9\-\.]+$")


def is_valid_plugin_name(name: str) -> bool:
    """Validate ``name`` per spec §5.5.

    - 1-64 characters
    - ``a-z``/``0-9``/``-``/``.`` only
    - starts and ends with an alphanumeric
    - no ``--`` or ``..``
    """
    if not isinstance(name, str):
        return False
    if not 1 <= len(name) <= NAME_MAX_LEN:
        return False
    if not _NAME_CHARS.match(name):
        return False
    if name[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        return False
    if name[-1] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        return False
    if "--" in name or ".." in name:
        return False
    return True


@dataclass
class Plugin:
    """A loaded-and-validated Agent Plugin manifest."""

    name: str
    root: Path
    manifest: dict
    warnings: list[str] = field(default_factory=list)


class PluginError(Exception):
    """Raised when a manifest is fatal and the plugin must be rejected."""


def load_manifest(path: Path) -> Plugin:
    """Parse and validate the ``plugin.json`` at ``path``.

    Returns a ``Plugin`` for a conforming manifest. Rejects with ``PluginError``
    when a required field is missing / invalid or ``$schema`` is unsupported.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PluginError(f"manifest unreadable: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PluginError(f"plugin.json is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise PluginError("plugin.json must be a JSON object")

    warnings: list[str] = []

    # Unknown top-level fields are reported and ignored (spec §5.2).
    unknown = sorted(set(data) - MANIFEST_FIELDS)
    for k in unknown:
        warnings.append(f"ignored unknown manifest field: {k}")

    # Required fields (spec §5.3).
    schema = data.get("$schema")
    if not isinstance(schema, str) or not schema:
        raise PluginError("missing or invalid required field: $schema")
    if schema != PLUGIN_SCHEMA:
        raise PluginError(f"unsupported $schema: {schema}")

    name = data.get("name")
    if not is_valid_plugin_name(name):
        raise PluginError(f"invalid required field: name={name!r}")

    # Metadata type validation (spec §5.4: only the stated types; non-fatal for
    # loose things like version format, but a type violation is fatal).
    for key, typ in (
        ("version", str),
        ("description", str),
        ("homepage", str),
        ("repository", str),
        ("license", str),
    ):
        v = data.get(key)
        if v is not None and not isinstance(v, typ):
            raise PluginError(f"field '{key}' has wrong type: expected {typ.__name__}")

    keywords = data.get("keywords")
    if keywords is not None:
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise PluginError("field 'keywords' must be an array of strings")

    author = data.get("author")
    if author is not None:
        if not isinstance(author, dict):
            raise PluginError("field 'author' must be an object")
        unknown_author = set(author) - AUTHOR_FIELDS
        for k in sorted(unknown_author):
            warnings.append(f"ignored unknown author field: {k}")
        for k in AUTHOR_FIELDS:
            if k in author and not isinstance(author[k], str):
                raise PluginError(f"author field '{k}' must be a string")

    extensions = data.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        raise PluginError("field 'extensions' must be an object")

    return Plugin(name=name, root=path.parent, manifest=data, warnings=warnings)


def discover_plugin_dirs(plugins_root: Path) -> list[Path]:
    """Return immediate child directories of ``plugins_root`` (plugin packages)."""
    if not plugins_root.is_dir():
        return []
    return sorted(
        [p for p in plugins_root.iterdir() if p.is_dir()], key=lambda p: p.name
    )


def load_plugins(plugins_root: Path) -> tuple[list[Plugin], list[tuple[Path, PluginError]]]:
    """Load all plugins under ``plugins_root``.

    Returns ``(plugins, errors)`` where each error is ``(plugin_dir, error)``.
    A failing plugin is skipped, the others are still loaded (spec §5.3 client
    behavior mirrors component-level isolation).
    """
    plugins: list[Plugin] = []
    errors: list[tuple[Path, PluginError]] = []
    for d in discover_plugin_dirs(plugins_root):
        try:
            plugins.append(load_manifest(d / "plugin.json"))
        except PluginError as e:
            errors.append((d, e))
    return plugins, errors


# ── MCP servers (spec §7.2) ─────────────────────────────
def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _expand_placeholders(value: str, root: Path, data_dir: Path) -> str:
    """Expand ${PLUGIN_ROOT} / ${PLUGIN_DATA} (spec §7.2.1 stdio)."""
    return value.replace(PLUGIN_ROOT, str(root)).replace(PLUGIN_DATA, str(data_dir))


def _resolve_cwd(value: str, root: Path, data_dir: Path) -> Path:
    """Resolve a cwd per spec §7.2.1: must stay within root or data dir."""
    if value == PLUGIN_ROOT or value.startswith(f"{PLUGIN_ROOT}/"):
        p = (root / value[len(PLUGIN_ROOT) :].lstrip("/")).resolve()
        if not _is_within(p, root):
            raise PluginError(f"cwd escapes plugin root: {value}")
        return p
    if value == PLUGIN_DATA or value.startswith(f"{PLUGIN_DATA}/"):
        p = (data_dir / value[len(PLUGIN_DATA) :].lstrip("/")).resolve()
        if not _is_within(p, data_dir):
            raise PluginError(f"cwd escapes plugin data dir: {value}")
        return p
    if value.startswith("./"):
        p = (root / value[2:]).resolve()
        if not _is_within(p, root):
            raise PluginError(f"cwd escapes plugin root: {value}")
        return p
    raise PluginError(f"cwd must be plugin-relative, {PLUGIN_ROOT}, or {PLUGIN_DATA}: {value}")


def _convert_stdio(name: str, conf: dict, root: Path, data_dir: Path) -> McpServer:
    command = conf.get("command")
    if not isinstance(command, str) or not command:
        raise PluginError("stdio server requires 'command'")
    # Bare executable name or plugin-relative path beginning with ./ (spec §7.2.1).
    if command.startswith("./"):
        exe = (root / command[2:]).resolve()
        if not _is_within(exe, root):
            raise PluginError("command escapes plugin root")
        command = str(exe)
    elif "/" in command or command.startswith("."):
        raise PluginError("command must be a bare executable name or ./-relative path")

    args = []
    for a in conf.get("args", []) or []:
        if not isinstance(a, str):
            raise PluginError("stdio 'args' must be an array of strings")
        args.append(_expand_placeholders(a, root, data_dir))

    env = {}
    for k, v in (conf.get("env", {}) or {}).items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise PluginError("stdio 'env' must be an object of strings")
        env[k] = _expand_placeholders(v, root, data_dir)

    cwd = root if "cwd" not in conf else _resolve_cwd(conf["cwd"], root, data_dir)

    return McpServer(name=name, command=command, args=args, cwd=str(cwd), env=env)


def _convert_http(name: str, conf: dict) -> McpServer:
    url = conf.get("url")
    if not isinstance(url, str) or not url:
        raise PluginError("http server requires 'url'")
    return McpServer(name=name, url=url, headers=conf.get("headers", {}) or {})


def _convert_mcp_server(name: str, conf: dict, root: Path, data_dir: Path) -> McpServer:
    """Convert a single mcp.json server entry. Raises PluginError on invalid."""
    if not isinstance(conf, dict):
        raise PluginError("server config must be an object")
    type_ = conf.get("type")
    if type_ == "stdio":
        unknown = set(conf) - STDIO_FIELDS
        if unknown:
            raise PluginError(f"unexpected stdio field(s): {', '.join(sorted(unknown))}")
        return _convert_stdio(name, conf, root, data_dir)
    if type_ in ("streamable-http", "sse"):
        unknown = set(conf) - HTTP_FIELDS
        if unknown:
            raise PluginError(f"unexpected {type_} field(s): {', '.join(sorted(unknown))}")
        return _convert_http(name, conf)
    raise PluginError(f"unknown transport type: {type_!r}")


def load_mcp_servers(plugin: Plugin, data_dir: Path) -> tuple[list[McpServer], list[str]]:
    """Parse plugin's ``mcp.json`` into McpServer definitions (spec §7.2).

    Invalid server entries are skipped with a report; the plugin's other
    component types are still loaded (spec §7.2.2).
    """
    mcp_path = plugin.root / "mcp.json"
    if not mcp_path.is_file():
        return [], []

    try:
        raw = mcp_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        return [], [f"mcp.json is not valid JSON: {e}"]

    if not isinstance(data, dict):
        return [], ["mcp.json must be a JSON object"]
    if data.get("$schema") != MCP_SCHEMA:
        return [], [f"unsupported $schema in mcp.json: {data.get('$schema')!r}"]

    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return [], ["mcp.json 'mcpServers' must be an object"]

    servers: list[McpServer] = []
    reports: list[str] = []
    for name, conf in mcp_servers.items():
        try:
            servers.append(_convert_mcp_server(name, conf, plugin.root, data_dir))
        except PluginError as e:
            reports.append(f"{plugin.name}/{name}: {e}")
    return servers, reports