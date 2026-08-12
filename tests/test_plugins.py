#!/usr/bin/env python3
"""
Tests for saku.plugins (Agent Plugins 1.0.0 manifest loading).
Based on the spec at https://agent-plugins.org/specification.
"""

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from saku import plugins


SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


def _write(tmp: Path, data: dict) -> Path:
    manifest = tmp / "plugin.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def test_valid_manifest_accepted():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-valid-") as td:
        tmp = Path(td)
        manifest = _write(tmp, {"$schema": SCHEMA, "name": "my-plugin"})
        plugin = plugins.load_manifest(manifest)
        assert plugin.name == "my-plugin"
        assert plugin.root == tmp
        assert plugin.warnings == []


def test_full_manifest_with_metadata():
    data = {
        "$schema": SCHEMA,
        "name": "acme.tools",
        "version": "1.2.0",
        "description": "br",
        "author": {"name": "a", "email": "e@x.com", "url": "https://x"},
        "homepage": "https://x",
        "license": "MIT",
        "keywords": ["a", "b"],
        "extensions": {"com.example": {"a": 1}},
    }
    with tempfile.TemporaryDirectory(prefix="saku-plugins-full-") as td:
        plugin = plugins.load_manifest(_write(Path(td), data))
        assert plugin.name == "acme.tools"


def test_missing_schema_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-noschema-") as td:
        try:
            plugins.load_manifest(_write(Path(td), {"name": "a"}))
            assert False, "should reject"
        except plugins.PluginError as e:
            assert "$schema" in str(e)


def test_unsupported_schema_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-badschema-") as td:
        try:
            plugins.load_manifest(_write(Path(td), {"$schema": "https://x/nope", "name": "a"}))
            assert False, "should reject"
        except plugins.PluginError as e:
            assert "unsupported $schema" in str(e)


def test_unknown_field_reported_but_accepted():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-unknown-") as td:
        plugin = plugins.load_manifest(_write(Path(td), {"$schema": SCHEMA, "name": "a", "hack": 1}))
        assert plugin.name == "a"
        assert any("hack" in w for w in plugin.warnings)


def test_invalid_names_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-names-") as td:
        tmp = Path(td)
        for bad in ("My-Plugin", "-start", "has--double", "too.many..dots", "", "a" * 65):
            try:
                plugins.load_manifest(_write(tmp, {"$schema": SCHEMA, "name": bad}))
                assert False, f"should reject name={bad!r}"
            except plugins.PluginError as e:
                assert "name" in str(e), e


def test_valid_names():
    for good in ("my-plugin", "acme.tools", "lint3r", "a"):
        assert plugins.is_valid_plugin_name(good), good


def test_wrong_type_author_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-author-") as td:
        try:
            plugins.load_manifest(_write(Path(td), {"$schema": SCHEMA, "name": "a", "author": {"name": 5}}))
            assert False, "should reject"
        except plugins.PluginError as e:
            assert "author" in str(e)


def test_manifest_not_json_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-badjson-") as td:
        tmp = Path(td)
        (tmp / "plugin.json").write_text("{not json", encoding="utf-8")
        try:
            plugins.load_manifest(tmp / "plugin.json")
            assert False, "should reject"
        except plugins.PluginError as e:
            assert "JSON" in str(e)


def test_load_plugins_skips_failing():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-multi-") as td:
        base = Path(td)
        good, bad = base / "good", base / "bad"
        good.mkdir()
        bad.mkdir()
        (good / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "good"}), encoding="utf-8")
        (bad / "plugin.json").write_text(json.dumps({"name": "bad"}), encoding="utf-8")
        (base / "README.md").write_text("x", encoding="utf-8")

        plugins_loaded, errors = plugins.load_plugins(base)
        assert [p.name for p in plugins_loaded] == ["good"]
        assert len(errors) == 1


def test_load_plugins_missing_root():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-empty-") as td:
        plugins_loaded, errors = plugins.load_plugins(Path(td) / "no-such-dir")
        assert plugins_loaded == []
        assert errors == []


MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def _plugin(name: str, root: Path) -> plugins.Plugin:
    return plugins.Plugin(name=name, root=root, manifest={})


def _write_mcp(root: Path, data: dict) -> Path:
    p = root / "mcp.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_mcp_stdio_conversion():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        data = {
            "$schema": MCP_SCHEMA,
            "mcpServers": {
                "local": {
                    "type": "stdio",
                    "command": "./bin/validator",
                    "args": ["--data", "${PLUGIN_DATA}/out"],
                    "env": {"CFG": "${PLUGIN_ROOT}/config.json"},
                    "cwd": "${PLUGIN_ROOT}",
                }
            },
        }
        _write_mcp(root, data)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data" / "demo")
        assert reports == [], reports
        assert len(servers) == 1
        s = servers[0]
        assert s.name == "local"
        assert s.command == str((root / "bin" / "validator").resolve())
        assert s.args == ["--data", str((root / ".data" / "demo" / "out").resolve())]
        assert s.env == {"CFG": str(root / "config.json")}
        assert s.cwd == str(root)


def test_mcp_http_conversion():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-http-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        data = {
            "$schema": MCP_SCHEMA,
            "mcpServers": {
                "remote": {"type": "streamable-http", "url": "https://x.example/mcp"},
            },
        }
        _write_mcp(root, data)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data")
        assert reports == [], reports
        assert servers[0].url == "https://x.example/mcp"


def test_mcp_invalid_entries_skipped():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-bad-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        data = {
            "$schema": MCP_SCHEMA,
            "mcpServers": {
                "good": {"type": "streamable-http", "url": "https://ok.example/mcp"},
                "bad": {"type": "stdio"},  # missing command
                "nope": {"type": "foo"},
            },
        }
        _write_mcp(root, data)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data")
        assert len(servers) == 1
        assert len(reports) == 2


def test_mcp_cwd_escape_rejected():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-cwd-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        data = {
            "$schema": MCP_SCHEMA,
            "mcpServers": {
                "evade": {"type": "stdio", "command": "./bin/x", "cwd": "${PLUGIN_ROOT}/../../etc"},
            },
        }
        _write_mcp(root, data)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data")
        assert servers == []
        assert any("escape" in r or "cwd" in r for r in reports), reports


def test_mcp_schema_mismatch_reported():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-schema-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        data = {"$schema": "https://x/old", "mcpServers": {}}
        _write_mcp(root, data)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data")
        assert servers == []
        assert any("schema" in r for r in reports)


def test_mcp_absent_returns_empty():
    with tempfile.TemporaryDirectory(prefix="saku-plugins-mcp-absent-") as td:
        root = Path(td)
        plugin = _plugin("demo", root)
        servers, reports = plugins.load_mcp_servers(plugin, root / ".data")
        assert servers == []
        assert reports == []


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} plugin tests PASSED!")


if __name__ == "__main__":
    run_tests()