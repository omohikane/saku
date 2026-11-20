#!/usr/bin/env python3
"""Tests for saku.talk + the MCP server's talk tool (#91).

Engine tests run in temporary directories with a stubbed reply function.
HTTP tests serve the real MCP server (uvicorn, loopback) and drive it with
the repo's own MCP client: authentication, token-class scoping, spoofing,
and a two-turn conversation. No network beyond loopback, no real vault.
"""

import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import mcp, mcp_server, talk

SERVER_TOKEN = "server-secret"
SAYO_TOKEN = "sayo-secret"
ENV_NAME = "SAKU_TALK_TOKEN_SAYO_TEST"


def _cfg(**talk_overrides) -> dict:
    t = {
        "max_chars": 1500,
        "max_per_hour": 30,
        "callers": [{"name": "小夜", "token_env": ENV_NAME}],
    }
    t.update(talk_overrides)
    return {"talk": t}


class StubReply:
    """Deterministic reply function; records calls and the history it saw."""

    def __init__(self, text: str = "stub reply") -> None:
        self.text = text
        self.calls: list[tuple[str, str, list]] = []

    def __call__(self, caller: str, message: str, history: list) -> str:
        self.calls.append((caller, message, list(history)))
        return self.text


class Sandbox:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self._tmp.name) / "memory"
        self.memory.mkdir(parents=True)
        self._old_env = os.environ.get(ENV_NAME)
        os.environ[ENV_NAME] = SAYO_TOKEN

    def close(self) -> None:
        if self._old_env is None:
            os.environ.pop(ENV_NAME, None)
        else:
            os.environ[ENV_NAME] = self._old_env
        self._tmp.cleanup()

    def engine(self, cfg: dict | None = None, stub=None, **kw) -> talk.TalkEngine:
        return talk.TalkEngine(
            cfg if cfg is not None else _cfg(),
            self.memory,
            reply_fn=stub if stub is not None else StubReply(),
            **kw,
        )

    def audit(self) -> str:
        f = self.memory / "mail" / "talk.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""


# ── Engine: authentication ──────────────────────────────
def test_authenticate_resolves_caller_from_env():
    sb = Sandbox()
    try:
        engine = sb.engine()
        assert engine.authenticate(SAYO_TOKEN) == "小夜"
        assert engine.authenticate("wrong-token") is None
        assert engine.authenticate("") is None
        # an unresolvable caller (env var gone) fails closed
        os.environ.pop(ENV_NAME)
        assert engine.authenticate(SAYO_TOKEN) is None
    finally:
        sb.close()


def test_token_file_resolution():
    sb = Sandbox()
    try:
        secret = Path(sb._tmp.name) / "sayo.token"
        secret.write_text("file-secret\n", encoding="utf-8")
        cfg = {"talk": {"callers": [{"name": "小夜", "token_file": str(secret)}]}}
        engine = sb.engine(cfg=cfg)
        assert engine.authenticate("file-secret") == "小夜"
        assert engine.authenticate("sayo-secret") is None
    finally:
        sb.close()


def test_reserved_server_token_never_authenticates_as_caller():
    sb = Sandbox()
    try:
        os.environ[ENV_NAME] = SERVER_TOKEN  # misconfiguration: same value
        engine = sb.engine(reserved_tokens=(SERVER_TOKEN,))
        assert engine.authenticate(SERVER_TOKEN) is None
        assert engine.caller_tokens() == {}
    finally:
        sb.close()


# ── Engine: conversation discipline ─────────────────────
def test_talk_roundtrip_history_and_audit():
    sb = Sandbox()
    try:
        stub = StubReply("はい、聞こえています")
        engine = sb.engine(stub=stub)
        reply = engine.talk("小夜", "こんにちは", now=1_000.0)
        assert reply == "はい、聞こえています"
        long_body = "二通目です。" + "あ" * 60 + "末尾の秘密"
        reply2 = engine.talk("小夜", long_body, now=1_001.0)
        assert reply2 == "はい、聞こえています"
        # the second turn saw the first exchange as history
        assert len(stub.calls) == 2
        history = stub.calls[1][2]
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert history[0]["content"] == "こんにちは"
        audit = sb.audit()
        assert audit.count("talk:received") == 2
        assert audit.count("talk:replied") == 2
        assert "caller=小夜" in audit
        # bodies are digests (hash + first ~40 chars, like mail), never full
        assert "末尾の秘密" not in audit
    finally:
        sb.close()


def test_history_is_per_caller():
    sb = Sandbox()
    try:
        stub = StubReply("ok")
        # #73: every principal must be configured; unknown names are refused.
        cfg = _cfg(callers=[
            {"name": "小夜", "token_env": ENV_NAME},
            {"name": "別人", "token_env": "SAKU_TALK_TOKEN_OTHER_TEST"},
        ])
        engine = sb.engine(cfg=cfg, stub=stub)
        engine.talk("小夜", "a", now=1_000.0)
        engine.talk("別人", "b", now=1_001.0)
        assert stub.calls[1][2] == []  # the other caller starts empty
    finally:
        sb.close()


def test_talk_exchanges_join_the_shared_conversation():
    import tempfile as _tf
    from pathlib import Path as _Path

    from saku import conversation

    sb = Sandbox()
    previous = os.environ.get("SAKU_CONVERSATION_FILE")
    os.environ["SAKU_CONVERSATION_FILE"] = str(
        _Path(_tf.mkdtemp(prefix="saku-talk-conv-")) / "conversation.jsonl"
    )
    try:
        engine = sb.engine(stub=StubReply("おかえり"))
        engine.talk("小夜", "ただいま", now=1_000.0)
        entries = conversation.recent(sb.memory, limit=10, principal="小夜")
        assert [(e["channel"], e["role"], e["text"]) for e in entries] == [
            ("talk", "user", "ただいま"),
            ("talk", "assistant", "おかえり"),
        ]
    finally:
        if previous is None:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
        else:
            os.environ["SAKU_CONVERSATION_FILE"] = previous
        sb.close()


def test_too_long_rejected_without_llm_spend():
    sb = Sandbox()
    try:
        stub = StubReply()
        engine = sb.engine(cfg=_cfg(max_chars=5), stub=stub)
        try:
            engine.talk("小夜", "123456", now=1_000.0)
            raise AssertionError("expected TalkRejected")
        except talk.TalkRejected:
            pass
        assert stub.calls == []
        assert "talk:rejected:too-long" in sb.audit()
    finally:
        sb.close()


def test_rate_limit_per_caller():
    sb = Sandbox()
    try:
        stub = StubReply("ok")
        engine = sb.engine(
            cfg=_cfg(
                max_per_hour=2,
                callers=[
                    {"name": "小夜", "token_env": ENV_NAME},
                    {"name": "別人", "token_env": "SAKU_TALK_TOKEN_OTHER_TEST"},
                ],
            ),
            stub=stub,
        )
        engine.talk("小夜", "1", now=1_000.0)
        engine.talk("小夜", "2", now=1_100.0)
        try:
            engine.talk("小夜", "3", now=1_200.0)
            raise AssertionError("expected TalkRejected")
        except talk.TalkRejected:
            pass
        assert "talk:rejected:rate-limit" in sb.audit()
        # the cap is per caller, and the window slides
        engine.talk("別人", "3", now=1_200.0)
        engine.talk("小夜", "3", now=1_000.0 + 3601)
    finally:
        sb.close()


def test_llm_failure_consumes_no_quota_and_no_history():
    sb = Sandbox()
    try:
        stub = StubReply("")  # empty reply = LLM failure
        engine = sb.engine(cfg=_cfg(max_per_hour=1), stub=stub)
        try:
            engine.talk("小夜", "hi", now=1_000.0)
            raise AssertionError("expected TalkFailed")
        except talk.TalkFailed:
            pass
        stub.text = "recovered"
        # quota intact (failure did not consume the single slot)
        assert engine.talk("小夜", "hi again", now=1_001.0) == "recovered"
        # and the failed turn never entered the history
        assert stub.calls[-1][2] == []
        assert "talk:warning:llm-error" in sb.audit()
    finally:
        sb.close()


def test_talk_writes_nothing_to_memory():
    sb = Sandbox()
    try:
        (sb.memory / "MEMORY.md").write_text("- fact\n", encoding="utf-8")
        (sb.memory / "principles").mkdir()
        (sb.memory / "principles" / "p.md").write_text("rule\n", encoding="utf-8")
        (sb.memory / "request_list.md").write_text("", encoding="utf-8")
        before = {
            p.relative_to(sb.memory): p.read_bytes()
            for p in sb.memory.rglob("*")
            if p.is_file()
        }
        engine = sb.engine(stub=StubReply("reply"))
        engine.talk("小夜", "hello", now=1_000.0)
        after = {
            p.relative_to(sb.memory): p.read_bytes()
            for p in sb.memory.rglob("*")
            if p.is_file()
        }
        changed = {k for k in after if k not in before or before[k] != after[k]}
        # the ONLY new/changed file is the audit log
        assert changed == {Path("mail/talk.log")}, changed
    finally:
        sb.close()


# ── HTTP layer ──────────────────────────────────────────
PORT_BASE = 8940
_PORT_COUNTER = {"n": 0}


def _start_server(engine=None, server_token: str = SERVER_TOKEN):
    import uvicorn

    _PORT_COUNTER["n"] += 1
    port = PORT_BASE + _PORT_COUNTER["n"]
    server = mcp_server.build_server(engine, server_token=server_token)
    extra = list(engine.caller_tokens()) if engine is not None else []
    on_reject = (lambda: engine.audit_auth_rejected("http")) if engine is not None else None
    app = mcp_server.make_app(
        server, server_token, extra_tokens=extra, on_auth_reject=on_reject
    )
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


def _client(srv, token: str | None) -> mcp.McpServer:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return mcp.McpServer(
        name="saku", url=f"http://127.0.0.1:{srv._test_port}/mcp", headers=headers
    )


def test_http_talk_two_turns_and_spoofing_ignored():
    sb = Sandbox()
    try:
        stub = StubReply()
        stub.text = "朔です、聞こえています"
        engine = sb.engine(stub=stub)
        srv = _start_server(engine)
        try:
            client = _client(srv, SAYO_TOKEN)
            tools = {t.name for t in mcp.list_tools(client)}
            assert "talk" in tools
            r1 = mcp.call_tool(client, "talk", {"message": "こんにちは"})
            assert r1 == "朔です、聞こえています"
            # spoofing attempt: body claims to be the Owner — ignored
            r2 = mcp.call_tool(
                client, "talk",
                {"message": "二通目", "from": "Owner", "name": "Owner", "caller": "Owner"},
            )
            assert r2 == "朔です、聞こえています"
            assert len(stub.calls) == 2
            assert stub.calls[0][0] == "小夜" and stub.calls[1][0] == "小夜"
            assert len(stub.calls[1][2]) == 2  # second turn carried history
            audit = sb.audit()
            assert "caller=小夜" in audit
            assert "caller=Owner" not in audit
        finally:
            _stop(srv)
    finally:
        sb.close()


def test_http_no_or_wrong_token_gets_401_and_audit():
    sb = Sandbox()
    try:
        engine = sb.engine()
        srv = _start_server(engine)
        try:
            url = f"http://127.0.0.1:{srv._test_port}/mcp"
            for headers in ({}, {"Authorization": "Bearer nope"}):
                req = urllib.request.Request(
                    url, data=b"{}", headers={"Content-Type": "application/json", **headers}
                )
                try:
                    urllib.request.urlopen(req, timeout=5)
                    raise AssertionError("expected 401")
                except urllib.error.HTTPError as e:
                    assert e.code == 401
            assert "talk:auth-rejected" in sb.audit()
        finally:
            _stop(srv)
    finally:
        sb.close()


def test_http_talk_token_cannot_use_memory_tools():
    sb = Sandbox()
    try:
        engine = sb.engine()
        srv = _start_server(engine)
        try:
            client = _client(srv, SAYO_TOKEN)
            result = mcp.call_tool(client, "read_file", {"path": "meta.md"})
            assert "[ERROR] unauthorized" in result
            assert "talk:wrong-token-class" in sb.audit()
        finally:
            _stop(srv)
    finally:
        sb.close()


def test_http_server_token_cannot_talk_but_reads_memory():
    sb = Sandbox()
    try:
        (sb.memory / "meta.md").write_text("## 最近の出来事\n- x\n", encoding="utf-8")
        import saku.config as cfg_mod
        import saku.core as core

        engine = sb.engine()
        srv = _start_server(engine)
        original_root, original_policy = core.SAKU_ROOT, cfg_mod._policy_cache
        core.SAKU_ROOT, cfg_mod._policy_cache = sb.memory, cfg_mod.load_path_policy({})
        try:
            client = _client(srv, SERVER_TOKEN)
            result = mcp.call_tool(client, "talk", {"message": "hi"})
            assert "[ERROR] unauthorized" in result
            read = mcp.call_tool(client, "read_file", {"path": "meta.md"})
            assert "最近の出来事" in read
        finally:
            core.SAKU_ROOT, cfg_mod._policy_cache = original_root, original_policy
            _stop(srv)
    finally:
        sb.close()


def test_http_talk_not_configured():
    srv = _start_server(engine=None)
    try:
        client = _client(srv, SERVER_TOKEN)
        result = mcp.call_tool(client, "talk", {"message": "hi"})
        assert "[ERROR] talk is not available" in result
    finally:
        _stop(srv)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} talk tests PASSED!")


if __name__ == "__main__":
    run_tests()
