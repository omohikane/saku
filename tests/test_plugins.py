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


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} plugin tests PASSED!")


if __name__ == "__main__":
    run_tests()