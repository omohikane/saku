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


def test_chat_sse_status_and_page():
    """SSE carries status events (thinking / tool), and the page renders them."""
    server = _start_server()
    try:
        import saku.agent_loop as al

        original = al.chat_stream
        saved = []
        original_save = ui.agent.save_journal
        # The handler journals the exchange; without this the test writes into the
        # Owner's real journal via the configured memory root.
        ui.agent.save_journal = lambda msg, reply, thinking="": saved.append((msg, reply))
        al.chat_stream = lambda history, llm_cfg, on_token=None: "こんにちは"
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/chat",
                data=json.dumps({"message": "hi", "client_id": "t-status"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode("utf-8")
        finally:
            al.chat_stream = original
            ui.agent.save_journal = original_save
        assert '"type": "status"' in body
        assert '"thinking"' in body
        assert saved == [("hi", "こんにちは")]

        page = _get(server, "/").decode("utf-8")
        assert 'data.type === "status"' in page
        assert "考えています" in page
    finally:
        _stop(server)


def test_proactive():
    """GET /api/proactive returns the daemon's notification and then clears it.

    Uses a temporary inbox: the endpoint consumes the file, so pointing it at the
    live memory root made the assertion depend on daemon timing and silently ate
    the Owner's pending notification.
    """
    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    inbox = tmpdir / "ui_inbox.json"
    original_inbox = ui._UI_INBOX
    ui._UI_INBOX = inbox
    try:
        server = _start_server()
        try:
            assert json.loads(_get(server, "/api/proactive")) == {}
            inbox.write_text(json.dumps({"message": "お知らせ"}, ensure_ascii=False), encoding="utf-8")
            assert json.loads(_get(server, "/api/proactive")) == {"message": "お知らせ"}
            assert json.loads(_get(server, "/api/proactive")) == {}
        finally:
            _stop(server)
    finally:
        ui._UI_INBOX = original_inbox
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_requests_endpoint():
    """GET /api/requests returns pending items from request_list.md."""
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    req_file = tmpdir / "request_list.md"
    req_file.write_text(
        "# 依頼\n\n- [ ] ブログ下書きを確認してほしい\n- [x] 完了済み\n- [ ] 新しいツールの承認\n\n- [ ] タブとスペース\n",
        encoding="utf-8",
    )
    original_req, original_root = ui._REQ_FILE, ui._MEMORY_ROOT
    ui._REQ_FILE, ui._MEMORY_ROOT = req_file, tmpdir
    try:
        server = _start_server()
        try:
            data = json.loads(_get(server, "/api/requests"))
            assert data == {"requests": ["ブログ下書きを確認してほしい", "新しいツールの承認", "タブとスペース"]}, data
        finally:
            _stop(server)
    finally:
        ui._REQ_FILE, ui._MEMORY_ROOT = original_req, original_root
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_daemon_thread_spawns():
    """Verify that daemon.main() is started in a thread with auto_daemon enabled (no LLM)."""
    import saku.daemon as daemon_mod

    calls = []
    original = daemon_mod.main
    daemon_mod.main = lambda: calls.append("main")
    try:
        ui._start_daemon_thread()
        time.sleep(0.3)
        assert "main" in calls
    finally:
        daemon_mod.main = original


def test_server_class_follows_bind_family():
    import socket

    assert ui._server_class("::") is ui._DualStackHTTPServer
    assert ui._server_class("0.0.0.0") is ui.ThreadingHTTPServer
    assert ui._DualStackHTTPServer.address_family == socket.AF_INET6


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} UI tests PASSED!")


if __name__ == "__main__":
    run_tests()
