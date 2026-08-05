#!/usr/bin/env python3
"""
Tests for saku.mcp_server (MCP server exposing SAKU's memory with token auth).
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import mcp, mcp_server

TOKEN = "test-token"
PORT_BASE = 8920
_PORT_COUNTER = {"n": 0}


def _start_server():
    import uvicorn

    _PORT_COUNTER["n"] += 1
    port = PORT_BASE + _PORT_COUNTER["n"]
    server = mcp_server.build_server()
    app = mcp_server.make_app(server, TOKEN)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    srv._test_port = port
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    return srv


def _stop(srv):
    srv.should_exit = True
    time.sleep(0.2)


def _server(srv) -> mcp.McpServer:
    return mcp.McpServer(
        name="saku",
        url=f"http://127.0.0.1:{srv._test_port}/mcp",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_list_and_read():
    srv = _start_server()
    import saku.core as core
    import saku.config as cfg_mod
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        meta = root / "meta.md"
        meta.write_text("## 最近の出来事\n- 初期\n", encoding="utf-8")
        original_root, original_policy = core.SAKU_ROOT, cfg_mod._policy_cache
        core.SAKU_ROOT, cfg_mod._policy_cache = root, cfg_mod.load_path_policy({})
        try:
            tools = mcp.list_tools(_server(srv))
            names = {t.name for t in tools}
            assert "read_file" in names
            assert "write_file" in names
            # read the self-model from the temporary memory root
            result = mcp.call_tool(_server(srv), "read_file", {"path": "meta.md"})
            assert "## 最近の出来事" in result
        finally:
            core.SAKU_ROOT, cfg_mod._policy_cache = original_root, original_policy
    _stop(srv)


def test_write_and_append_scoped():
    srv = _start_server()
    import saku.core as core
    import saku.config as cfg_mod
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original_root, original_policy = core.SAKU_ROOT, cfg_mod._policy_cache
        core.SAKU_ROOT, cfg_mod._policy_cache = root, cfg_mod.load_path_policy({})
        try:
            result = mcp.call_tool(_server(srv), "write_file", {"path": "study/mcp_test.txt", "content": "hello from mcp"})
            assert "[OK]" in result
            # meta.md is write-denied under the default PathPolicy
            denied = mcp.call_tool(_server(srv), "write_file", {"path": "meta.md", "content": "clobber"})
            assert "[DENY]" in denied
        finally:
            core.SAKU_ROOT, cfg_mod._policy_cache = original_root, original_policy
    _stop(srv)


def test_auth_required():
    srv = _start_server()
    try:
        no_token = mcp.McpServer(name="saku", url=f"http://127.0.0.1:{srv._test_port}/mcp")
        raised = False
        try:
            mcp.list_tools(no_token)
        except Exception:
            raised = True
        assert raised, "expected token auth to reject unauthenticated requests"
    finally:
        _stop(srv)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} MCP server tests PASSED!")


if __name__ == "__main__":
    run_tests()
