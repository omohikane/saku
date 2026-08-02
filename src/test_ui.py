#!/usr/bin/env python3
"""
Unit tests for the Web UI server (saku.ui).

Starts the server on an ephemeral port in a thread and exercises endpoints.
No LLM required: /api/chat returns an SSE error event when llama-server is down.
"""

import json
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import ui

PORT_BASE = 8800
_PORT_COUNTER = {"n": 0}


def _start_server():
    _PORT_COUNTER["n"] += 1
    port = PORT_BASE + _PORT_COUNTER["n"]
    server = ui.ThreadingHTTPServer(("127.0.0.1", port), ui.Handler)
    server._test_port = port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _get(server, path: str) -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{server._test_port}{path}", timeout=5) as r:
        return r.read()


def _stop(server):
    server.shutdown()
    server.server_close()


def test_health():
    server = _start_server()
    try:
        assert json.loads(_get(server, "/api/health"))["status"] == "ok"
    finally:
        _stop(server)


def test_page():
    server = _start_server()
    try:
        body = _get(server, "/")
        assert b"<title>SAKU" in body
        assert b"api/chat" in body
    finally:
        _stop(server)


def test_not_found():
    server = _start_server()
    try:
        import urllib.error

        try:
            _get(server, "/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        _stop(server)


def test_chat_sse_error():
    server = _start_server()
    try:
        import saku.agent_loop as al

        original = al.chat_stream
        al.chat_stream = lambda history, llm_cfg, on_token=None: "[ERROR] llama-server not reachable"
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/chat",
                data=json.dumps({"message": "hi"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode("utf-8")
        finally:
            al.chat_stream = original
        assert '"type": "error"' in body
        assert '"type": "done"' in body
    finally:
        _stop(server)


def test_proactive():
    server = _start_server()
    try:
        assert json.loads(_get(server, "/api/proactive")) == {}
    finally:
        _stop(server)


def test_daemon_thread_spawns():
    """auto_daemon 時に daemon.main() がスレッドで起動されることを確認（LLMなし）。"""
    fake = types.ModuleType("daemon")
    calls = []
    fake.main = lambda: calls.append("main")
    sys.modules["daemon"] = fake
    try:
        ui._start_daemon_thread()
        time.sleep(0.3)
        assert "main" in calls
    finally:
        sys.modules.pop("daemon", None)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} UI tests PASSED!")


if __name__ == "__main__":
    run_tests()
