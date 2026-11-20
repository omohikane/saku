#!/usr/bin/env python3
"""
Unit tests for the Web UI server (saku.ui).

Starts the server on an ephemeral port in a thread and exercises endpoints.
No LLM required: /api/chat returns an SSE error event when llama-server is down.
"""

import json
import os
import sys
import tempfile
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

os.environ.setdefault(
    "SAKU_AUDIT_FILE", str(Path(tempfile.mkdtemp(prefix="saku-test-audit-")) / "audit.log")
)
os.environ.setdefault(
    "SAKU_CONVERSATION_FILE",
    str(Path(tempfile.mkdtemp(prefix="saku-test-conv-")) / "conversation.jsonl"),
)

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
        al.chat_stream = lambda history, llm_cfg, on_token=None, status=None: "[ERROR] llama-server not reachable"
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
        al.chat_stream = lambda history, llm_cfg, on_token=None, status=None: "こんにちは"
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


def _post_chat(server, message, client_id, out, index):
    def run():
        req = urllib.request.Request(
            f"http://127.0.0.1:{server._test_port}/api/chat",
            data=json.dumps({"message": message, "client_id": client_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            out[index] = r.read().decode("utf-8")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_concurrent_requests_for_one_session_are_serialized():
    """#131: the slower turn used to overwrite the faster one's exchange."""
    from saku.agent_loop import LoopResult

    Path(os.environ["SAKU_CONVERSATION_FILE"]).write_text("", encoding="utf-8")
    server = _start_server()
    events = []
    snapshots = []
    originals = (ui.run_agent_loop, ui.agent.build_light_system_prompt,
                 ui.agent.build_recall_message, ui.agent.save_journal)
    ui.agent.save_journal = lambda *a, **k: None
    ui.agent.build_recall_message = lambda m: None
    ui.agent.build_light_system_prompt = lambda: "system"

    def fake_loop(history, llm_cfg, ctx, mem, code, **kw):
        users = [m["content"] for m in history if m["role"] == "user"]
        snapshots.append(users)
        events.append("start")
        time.sleep(0.3)
        events.append("end")
        return LoopResult(
            visible="ok", thinking="", last_raw="ok",
            history=list(history) + [{"role": "assistant", "content": "ok"}],
            action_taken=True,
        )

    ui.run_agent_loop = fake_loop
    out = {}
    try:
        t1 = _post_chat(server, "A", "t-concurrent", out, 1)
        time.sleep(0.05)
        t2 = _post_chat(server, "B", "t-concurrent", out, 2)
        t1.join(10)
        t2.join(10)
    finally:
        ui.run_agent_loop, ui.agent.build_light_system_prompt, \
            ui.agent.build_recall_message, ui.agent.save_journal = originals
        ui._sessions.pop("t-concurrent", None)
        ui._session_locks.pop("t-concurrent", None)
        _stop(server)

    assert events == ["start", "end", "start", "end"], events
    # The second turn saw the first turn's history instead of a stale copy.
    assert snapshots[1] == ["A", "B"], snapshots


def test_requests_for_different_sessions_run_in_parallel():
    """The session lock must not serialize the whole server."""
    from saku.agent_loop import LoopResult

    server = _start_server()
    barrier = threading.Barrier(2, timeout=3)
    reached = []
    originals = (ui.run_agent_loop, ui.agent.build_light_system_prompt,
                 ui.agent.build_recall_message, ui.agent.save_journal)
    ui.agent.save_journal = lambda *a, **k: None
    ui.agent.build_recall_message = lambda m: None
    ui.agent.build_light_system_prompt = lambda: "system"

    def fake_loop(history, llm_cfg, ctx, mem, code, **kw):
        try:
            barrier.wait()
            reached.append(True)
        except threading.BrokenBarrierError:
            reached.append(False)
        return LoopResult(
            visible="ok", thinking="", last_raw="ok",
            history=list(history) + [{"role": "assistant", "content": "ok"}],
            action_taken=True,
        )

    ui.run_agent_loop = fake_loop
    out = {}
    try:
        t1 = _post_chat(server, "hi", "t-parallel-a", out, 1)
        t2 = _post_chat(server, "hi", "t-parallel-b", out, 2)
        t1.join(10)
        t2.join(10)
    finally:
        ui.run_agent_loop, ui.agent.build_light_system_prompt, \
            ui.agent.build_recall_message, ui.agent.save_journal = originals
        for cid in ("t-parallel-a", "t-parallel-b"):
            ui._sessions.pop(cid, None)
            ui._session_locks.pop(cid, None)
        _stop(server)

    assert reached == [True, True], reached


def test_a_new_client_continues_the_shared_thread():
    """#120 follow-up: another tab/device is the same conversation, not a stranger."""
    from saku import conversation
    from saku.agent_loop import LoopResult

    Path(os.environ["SAKU_CONVERSATION_FILE"]).write_text("", encoding="utf-8")
    conversation.append(tmp_root := Path(tempfile.mkdtemp()), channel="chat.md",
                        principal="owner", role="user", text="前の場所での話")
    conversation.append(tmp_root, channel="chat.md", principal="owner",
                        role="assistant", text="覚えています")

    server = _start_server()
    seen = {}
    originals = (ui.run_agent_loop, ui.agent.build_light_system_prompt,
                 ui.agent.build_recall_message, ui.agent.save_journal)
    ui.agent.save_journal = lambda *a, **k: None
    ui.agent.build_recall_message = lambda m: None
    ui.agent.build_light_system_prompt = lambda: "system"

    def fake_loop(history, llm_cfg, ctx, mem, code, **kw):
        seen["contents"] = [m["content"] for m in history]
        return LoopResult(visible="続きだね", thinking="", last_raw="続きだね",
                          history=list(history) + [{"role": "assistant", "content": "続きだね"}],
                          action_taken=True)

    ui.run_agent_loop = fake_loop
    out = {}
    try:
        t = _post_chat(server, "新しい端末から", "t-new-device", out, 1)
        t.join(10)
    finally:
        ui.run_agent_loop, ui.agent.build_light_system_prompt, \
            ui.agent.build_recall_message, ui.agent.save_journal = originals
        ui._sessions.pop("t-new-device", None)
        ui._session_locks.pop("t-new-device", None)
        _stop(server)

    assert "前の場所での話" in seen["contents"], seen
    assert "覚えています" in seen["contents"], seen


def test_system_prompt_is_rebuilt_each_turn():
    """The continuity block and current time must not freeze at session start."""
    from saku.agent_loop import LoopResult

    server = _start_server()
    prompts = iter(["system-1", "system-2", "system-3"])
    originals = (ui.run_agent_loop, ui.agent.build_light_system_prompt,
                 ui.agent.build_recall_message, ui.agent.save_journal)
    ui.agent.save_journal = lambda *a, **k: None
    ui.agent.build_recall_message = lambda m: None
    ui.agent.build_light_system_prompt = lambda: next(prompts)

    def fake_loop(history, llm_cfg, ctx, mem, code, **kw):
        return LoopResult(
            visible="ok", thinking="", last_raw="ok",
            history=list(history) + [{"role": "assistant", "content": "ok"}],
            action_taken=True,
        )

    ui.run_agent_loop = fake_loop
    out = {}
    try:
        for i in (1, 2):
            t = _post_chat(server, f"m{i}", "t-fresh-prompt", out, i)
            t.join(10)
        stored = ui._sessions.get("t-fresh-prompt", [])
    finally:
        ui.run_agent_loop, ui.agent.build_light_system_prompt, \
            ui.agent.build_recall_message, ui.agent.save_journal = originals
        ui._sessions.pop("t-fresh-prompt", None)
        ui._session_locks.pop("t-fresh-prompt", None)
        _stop(server)

    assert stored and stored[0]["role"] == "system"
    assert stored[0]["content"] == "system-2"
    assert stored.count({"role": "system", "content": "system-1"}) == 0


def test_proactive():
    """GET /api/proactive is the legacy single-message view of the outbox (#120).

    Uses a temporary memory root: the endpoint acknowledges the entry, so
    pointing it at the live root would eat the Owner's pending notification.
    """
    import shutil
    import tempfile

    from saku import outbox

    tmpdir = Path(tempfile.mkdtemp())
    original_root = ui._MEMORY_ROOT
    ui._MEMORY_ROOT = tmpdir
    try:
        server = _start_server()
        try:
            assert json.loads(_get(server, "/api/proactive")) == {}
            entry = outbox.add(tmpdir, "お知らせ", kind=outbox.KIND_NOTICE)
            payload = json.loads(_get(server, "/api/proactive"))
            assert payload["message"] == "お知らせ"
            assert payload["id"] == entry.id
            # Answered and acknowledged: the next poll is empty.
            assert json.loads(_get(server, "/api/proactive")) == {}
        finally:
            _stop(server)
    finally:
        ui._MEMORY_ROOT = original_root
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_outbox_endpoint_lists_and_acknowledges():
    """#120: the Owner sees queued speech after reopening the UI, and can dismiss it."""
    import shutil
    import tempfile

    from saku import outbox

    tmpdir = Path(tempfile.mkdtemp())
    original_root = ui._MEMORY_ROOT
    ui._MEMORY_ROOT = tmpdir
    try:
        server = _start_server()
        try:
            spoken = outbox.add(tmpdir, "少し話したいことがある", kind=outbox.KIND_SPONTANEOUS)
            outbox.add(tmpdir, "再起動しました", kind=outbox.KIND_NOTICE)

            payload = json.loads(_get(server, "/api/outbox"))
            assert payload["unread"] == 2
            assert {i["kind"] for i in payload["items"]} == {"spontaneous", "notice"}

            req = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/outbox/read",
                data=json.dumps({"ids": [spoken.id]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                assert json.loads(r.read())["changed"] == 1

            payload = json.loads(_get(server, "/api/outbox"))
            assert payload["unread"] == 1
            assert payload["items"][0]["kind"] == "notice"
        finally:
            _stop(server)
    finally:
        ui._MEMORY_ROOT = original_root
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_replying_acknowledges_spontaneous_speech_but_not_notices():
    """A reply continues the conversation, so queued speech is no longer unread."""
    import shutil
    import tempfile

    from saku import outbox

    tmpdir = Path(tempfile.mkdtemp())
    original_root = ui._MEMORY_ROOT
    ui._MEMORY_ROOT = tmpdir
    original_loop = ui.run_agent_loop
    original_save = ui.agent.save_journal
    original_prompt = ui.agent.build_light_system_prompt
    original_recall = ui.agent.build_recall_message
    from saku.agent_loop import LoopResult

    def fake_loop(history, llm_cfg, ctx, mem, code, **kw):
        return LoopResult(visible="うん", thinking="", last_raw="うん",
                          history=list(history) + [{"role": "assistant", "content": "うん"}],
                          action_taken=True)

    ui.run_agent_loop = fake_loop
    ui.agent.save_journal = lambda *a, **k: None
    ui.agent.build_light_system_prompt = lambda: "system"
    ui.agent.build_recall_message = lambda m: None
    try:
        server = _start_server()
        try:
            outbox.add(tmpdir, "自発", kind=outbox.KIND_SPONTANEOUS)
            outbox.add(tmpdir, "通知", kind=outbox.KIND_NOTICE)
            req = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/chat",
                data=json.dumps({"message": "返事", "client_id": "t-outbox"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            remaining = outbox.items(tmpdir)
        finally:
            _stop(server)
    finally:
        ui._MEMORY_ROOT = original_root
        ui.run_agent_loop = original_loop
        ui.agent.save_journal = original_save
        ui.agent.build_light_system_prompt = original_prompt
        ui.agent.build_recall_message = original_recall
        ui._sessions.pop("t-outbox", None)
        ui._session_locks.pop("t-outbox", None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    assert [e.kind for e in remaining if not e.read_at] == ["notice"], remaining


def _identity_env(tmp: Path):
    (tmp / "identity").mkdir(parents=True, exist_ok=True)
    (tmp / "identity" / "soul.md").write_text("# soul\n", encoding="utf-8")
    (tmp / "identity" / "genome.md").write_text("# genome\n", encoding="utf-8")
    (tmp / "identity" / "persona.md").write_text("# persona\n", encoding="utf-8")
    return {"identity": {"soul": "identity/soul.md", "genome": "identity/genome.md"},
            "persona": {"file": "identity/persona.md", "chars": 500}}


def test_persona_files_can_be_read_and_saved_from_the_ui():
    """#73 follow-up: the Owner edits identity files; SAKU's tools cannot."""
    import shutil
    import tempfile

    from saku import identity

    tmp = Path(tempfile.mkdtemp())
    cfg = _identity_env(tmp)
    original_root, original_cfg = ui._MEMORY_ROOT, ui._app_cfg
    ui._MEMORY_ROOT = tmp
    ui._app_cfg = lambda: cfg
    reloads = []
    original_reload = ui.agent.reload_system_prompt_cache
    ui.agent.reload_system_prompt_cache = lambda: reloads.append(True)
    try:
        server = _start_server()
        try:
            data = json.loads(_get(server, "/api/persona/files"))
            names = {f["name"] for f in data["files"]}
            assert names == {"soul", "genome", "persona"}
            persona = next(f for f in data["files"] if f["name"] == "persona")
            assert persona["content"] == "# persona\n"

            req = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/persona/files",
                data=json.dumps({"name": "persona", "content": "# 新しい人格\n"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                assert json.loads(r.read())["ok"] is True
            assert (tmp / "identity" / "persona.md").read_text(encoding="utf-8") == "# 新しい人格\n"
            assert reloads, "the prompt cache must be reloaded after an edit"
            assert identity.tail(tmp, 1)[0]["action"] == "identity.write"

            bad = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/persona/files",
                data=json.dumps({"name": "meta.md", "content": "x"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(bad, timeout=5)
                raise AssertionError("unknown files must be rejected")
            except urllib.error.HTTPError as e:
                assert e.code == 400

            big = urllib.request.Request(
                f"http://127.0.0.1:{server._test_port}/api/persona/files",
                data=json.dumps({"name": "persona", "content": "x" * 60001}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(big, timeout=5)
                raise AssertionError("oversized files must be rejected")
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            _stop(server)
    finally:
        ui._MEMORY_ROOT, ui._app_cfg = original_root, original_cfg
        ui.agent.reload_system_prompt_cache = original_reload
        shutil.rmtree(tmp, ignore_errors=True)


def test_page_has_the_identity_editor():
    server = _start_server()
    try:
        page = _get(server, "/").decode("utf-8")
        assert 'id="idbtn"' in page and 'id="idpanel"' in page
        assert "/api/persona/files" in page
    finally:
        _stop(server)


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
