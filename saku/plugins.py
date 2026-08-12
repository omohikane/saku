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

Component loading (skills/, mcp.json) is added in later phases.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

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