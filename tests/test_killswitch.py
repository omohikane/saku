#!/usr/bin/env python3
"""Tests for the kill switch (#76): saku.killswitch + its gates.

Everything runs in temporary directories. The daemon tests patch the
module globals the daemon itself reads (the pattern of test_daemon.py);
mail and talk reuse their channels' stubbed reply functions. No LLM, no
network, no real vault.

What is pinned here:
* presence of the flag file is the stop decision (even an empty file);
* while engaged: no autonomous tick, no self-initiation (including one
  whose generation raced the engagement), no scheduled task, no
  reflection (and the run date is NOT recorded), no mail reply, no talk
  reply — refusals are audit-logged, never silent;
* Owner chat replies are NOT gated (deliberate scope: reactive !=
  autonomous);
* release resumes normal operation; a mail letter refused while stopped
  stays refused (the sender writes a new letter).
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as saku_config
from saku import killswitch, mail, talk
from saku import daemon as daemon_mod
from _isolate_logging import isolate_daemon_logging

isolate_daemon_logging(daemon_mod.logger)

_PASS = 0
_FAIL = 0


# ── Helpers ─────────────────────────────────────────────
class FlagSandbox:
    """A temp memory root with the default flag location inside it."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self._tmp.name) / "memory"
        (self.memory / "state").mkdir(parents=True)
        self.flag = self.memory / "state" / "STOP"

    def close(self) -> None:
        self._tmp.cleanup()


def _patch(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    return original


# ── Module: engage / release / info ─────────────────────
def test_engage_release_cycle():
    sb = FlagSandbox()
    try:
        assert not killswitch.is_stopped(sb.flag)
        assert killswitch.read_info(sb.flag) is None
        assert killswitch.release(sb.flag) is None  # releasing a released switch

        info = killswitch.engage(sb.flag, by="cli", reason="テスト停止")
        assert killswitch.is_stopped(sb.flag)
        assert info["by"] == "cli" and info["reason"] == "テスト停止" and info["at"]
        back = killswitch.read_info(sb.flag)
        assert back == info

        log = killswitch.read_log(sb.flag)
        assert len(log) == 1 and " engaged " in f" {log[0]} " and "by=cli" in log[0]

        prior = killswitch.release(sb.flag)
        assert prior == info
        assert not killswitch.is_stopped(sb.flag)
        log = killswitch.read_log(sb.flag)
        assert len(log) == 2 and " released " in f" {log[1]} "

        text = killswitch.describe(sb.flag)
        assert "released" in text
        killswitch.engage(sb.flag, by="cli", reason="")
        assert "ENGAGED" in killswitch.describe(sb.flag)
    finally:
        sb.close()


def test_empty_flag_file_still_stops():
    """Presence is the decision: a hand-made empty STOP (e.g. dropped into
    the vault from a phone) stops SAKU, with blank metadata."""
    sb = FlagSandbox()
    try:
        sb.flag.write_text("", encoding="utf-8")
        assert killswitch.is_stopped(sb.flag)
        assert killswitch.read_info(sb.flag) == {"at": "", "by": "", "reason": ""}
        sb.flag.write_text("not json at all", encoding="utf-8")
        assert killswitch.is_stopped(sb.flag)
        assert killswitch.read_info(sb.flag) == {"at": "", "by": "", "reason": ""}
    finally:
        sb.close()


def test_config_resolution():
    root = Path("/some/memory")
    assert saku_config.resolve_killswitch_flag({}, root) == (root / "state" / "STOP").resolve()
    cfg = {"killswitch": {"flag_file": "custom/HALT"}}
    assert saku_config.resolve_killswitch_flag(cfg, root) == (root / "custom" / "HALT").resolve()
    cfg_abs = {"killswitch": {"flag_file": "/elsewhere/STOP"}}
    assert saku_config.resolve_killswitch_flag(cfg_abs, root) == Path("/elsewhere/STOP")
    assert saku_config.load_killswitch_config({}).flag_file == "state/STOP"


def test_tool_writable_guard():
    """The default flag location is unreachable by SAKU's file tools; a
    flag configured into a write-allowed prefix is called out."""
    sb = FlagSandbox()
    try:
        assert killswitch.tool_writable_relpath({}, sb.memory, sb.flag) is None
        inside = sb.memory / "principles" / "STOP"
        assert killswitch.tool_writable_relpath({}, sb.memory, inside) == "principles/STOP"
        outside = Path("/elsewhere/STOP")
        assert killswitch.tool_writable_relpath({}, sb.memory, outside) is None
    finally:
        sb.close()


# ── Daemon gates ────────────────────────────────────────
def test_tick_skipped_while_stopped_and_runs_after_release():
    sb = FlagSandbox()
    calls = []
    orig_flag = _patch(daemon_mod, "KILLSWITCH_FLAG", sb.flag)
    orig_loop = _patch(daemon_mod, "run_agent_loop",
                       lambda *a, **kw: calls.append(a) or (True, ""))
    try:
        killswitch.engage(sb.flag, by="test")
        daemon_mod.check_autonomous_tick()
        assert calls == [], "tick ran while the kill switch was engaged"
        killswitch.release(sb.flag)
        daemon_mod.check_autonomous_tick()
        assert len(calls) == 1, "tick did not resume after release"
    finally:
        _patch(daemon_mod, "KILLSWITCH_FLAG", orig_flag)
        _patch(daemon_mod, "run_agent_loop", orig_loop)
        sb.close()


def test_autonomous_wrapper_refuses_at_entry():
    """run_agent_loop(autonomous=True) never reaches the agent loop while
    stopped; the same call without the flag is the chat path and runs."""
    sb = FlagSandbox()
    calls = []

    class _Result:
        visible = ""
        last_raw = "ok"
        action_taken = False
        thinking = ""

    orig_flag = _patch(daemon_mod, "KILLSWITCH_FLAG", sb.flag)
    orig_loop = _patch(daemon_mod, "_run_agent_loop",
                       lambda *a, **kw: calls.append(kw) or _Result())
    try:
        killswitch.engage(sb.flag, by="test")
        ok, _ = daemon_mod.run_agent_loop("p", "tick", autonomous=True)
        assert ok is False and calls == []
        ok, _ = daemon_mod.run_agent_loop("p", "chat返信", light=True)
        assert len(calls) == 1 and calls[0].get("should_stop") is None
        killswitch.release(sb.flag)
        daemon_mod.run_agent_loop("p", "tick", autonomous=True)
        assert len(calls) == 2 and calls[1].get("should_stop") is not None
    finally:
        _patch(daemon_mod, "KILLSWITCH_FLAG", orig_flag)
        _patch(daemon_mod, "_run_agent_loop", orig_loop)
        sb.close()


def _chat_env(chat, state, loop_impl, flag):
    """Minimal daemon chat patch set (mirrors test_daemon._chat_test_env)."""
    originals = {
        "CHAT_FILE": daemon_mod.CHAT_FILE,
        "KILLSWITCH_FLAG": daemon_mod.KILLSWITCH_FLAG,
        "load_chat_state": daemon_mod.load_chat_state,
        "save_chat_state": daemon_mod.save_chat_state,
        "run_agent_loop": daemon_mod.run_agent_loop,
        "get_pending_requests": daemon_mod.get_pending_requests,
        "check_chat_archive_if_needed": daemon_mod.check_chat_archive_if_needed,
        "notify_webui": daemon_mod.notify_webui,
        "build_recall_message": daemon_mod.agent.build_recall_message,
        "save_journal": daemon_mod.agent.save_journal,
    }
    daemon_mod.CHAT_FILE = chat
    daemon_mod.KILLSWITCH_FLAG = flag
    daemon_mod.load_chat_state = lambda: state
    daemon_mod.save_chat_state = lambda s: None
    daemon_mod.run_agent_loop = loop_impl
    daemon_mod.get_pending_requests = lambda: ""
    daemon_mod.check_chat_archive_if_needed = lambda s: None
    daemon_mod.notify_webui = lambda msg: None
    daemon_mod.agent.build_recall_message = lambda msg: None
    daemon_mod.agent.save_journal = lambda owner, reply, thinking="": None

    def restore():
        for k, v in originals.items():
            if k in ("build_recall_message", "save_journal"):
                setattr(daemon_mod.agent, k, v)
            else:
                setattr(daemon_mod, k, v)

    return restore


def test_self_initiation_blocked_while_stopped():
    sb = FlagSandbox()
    try:
        chat = sb.memory / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_content": header, "turn_count": 0}
        calls = []

        def _loop(prompt, name, extra_history=None, **kw):
            calls.append(name)
            return True, "自発発話の本文です。"

        restore = _chat_env(chat, state, _loop, sb.flag)
        try:
            killswitch.engage(sb.flag, by="test")
            daemon_mod.saku_self_initiate("定例チェックイン")
            assert calls == [], "self-initiation generated while stopped"
            assert chat.read_text(encoding="utf-8") == header
            killswitch.release(sb.flag)
            daemon_mod.saku_self_initiate("定例チェックイン")
            assert "自発発話の本文です" in chat.read_text(encoding="utf-8")
        finally:
            restore()
    finally:
        sb.close()


def test_self_initiation_dropped_when_switch_engages_mid_generation():
    """The mid-tick race: the Owner engages the switch while a self-
    initiation is being generated. The finished message must be dropped,
    never appended (the append-time re-check, same shape as #84)."""
    sb = FlagSandbox()
    try:
        chat = sb.memory / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_content": header, "turn_count": 0}

        def _loop(prompt, name, extra_history=None, **kw):
            killswitch.engage(sb.flag, by="test", reason="生成中に作動")
            return True, "投稿されてはいけない本文です。"

        restore = _chat_env(chat, state, _loop, sb.flag)
        try:
            daemon_mod.saku_self_initiate("定例チェックイン")
        finally:
            restore()
        text = chat.read_text(encoding="utf-8")
        assert "投稿されてはいけない本文です" not in text
        assert text == header
    finally:
        sb.close()


def test_scheduled_tasks_stay_pending_while_stopped():
    sb = FlagSandbox()
    calls = []
    orig_root = _patch(daemon_mod, "MEMORY_ROOT", sb.memory)
    orig_flag = _patch(daemon_mod, "KILLSWITCH_FLAG", sb.flag)
    orig_loop = _patch(daemon_mod, "run_agent_loop",
                       lambda *a, **kw: calls.append(a) or (True, "done"))
    try:
        import json

        sched = sb.memory / "state" / "schedule.json"
        sched.write_text(
            json.dumps([{"id": "t1", "when": "2020-01-01 00:00",
                         "task": "掃除して", "status": "pending"}]),
            encoding="utf-8",
        )
        killswitch.engage(sb.flag, by="test")
        daemon_mod.check_scheduled_tasks()
        assert calls == [], "scheduled task executed while stopped"
        items = __import__("json").loads(sched.read_text(encoding="utf-8"))
        assert items[0]["status"] == "pending"

        killswitch.release(sb.flag)
        daemon_mod.check_scheduled_tasks()
        assert len(calls) == 1
        items = __import__("json").loads(sched.read_text(encoding="utf-8"))
        assert items[0]["status"] == "done"
    finally:
        _patch(daemon_mod, "MEMORY_ROOT", orig_root)
        _patch(daemon_mod, "KILLSWITCH_FLAG", orig_flag)
        _patch(daemon_mod, "run_agent_loop", orig_loop)
        sb.close()


def test_reflection_skipped_without_recording_date():
    sb = FlagSandbox()
    ran = []

    class _Refl:
        @staticmethod
        def run_reflection(d):
            ran.append(d)

    state = {"last_reflection_date": "", "last_dream_date": ""}
    orig_root = _patch(daemon_mod, "MEMORY_ROOT", sb.memory)
    orig_flag = _patch(daemon_mod, "KILLSWITCH_FLAG", sb.flag)
    orig_refl = _patch(daemon_mod, "reflection", _Refl())
    orig_state = _patch(daemon_mod, "load_chat_state", lambda: state)
    orig_save = _patch(daemon_mod, "save_chat_state", lambda s: None)
    orig_init = _patch(daemon_mod, "saku_self_initiate", lambda reason: None)
    orig_notify = _patch(daemon_mod, "notify_webui", lambda msg: None)
    try:
        (sb.memory / "journal").mkdir(exist_ok=True)
        (sb.memory / "journal" / "2026-08-04.md").write_text("## 20:00\n活動した\n", encoding="utf-8")
        killswitch.engage(sb.flag, by="test")
        daemon_mod.check_and_run_midnight_reflection(now=datetime(2026, 8, 5, 3, 30))
        assert ran == [], "reflection ran while stopped"
        assert state.get("last_reflection_date", "") != "2026-08-05", \
            "a skipped reflection must not be recorded as run"

        killswitch.release(sb.flag)
        daemon_mod.check_and_run_midnight_reflection(now=datetime(2026, 8, 5, 3, 30))
        assert ran == ["2026-08-04"]
        assert state["last_reflection_date"] == "2026-08-05"
    finally:
        _patch(daemon_mod, "MEMORY_ROOT", orig_root)
        _patch(daemon_mod, "KILLSWITCH_FLAG", orig_flag)
        _patch(daemon_mod, "reflection", orig_refl)
        _patch(daemon_mod, "load_chat_state", orig_state)
        _patch(daemon_mod, "save_chat_state", orig_save)
        _patch(daemon_mod, "saku_self_initiate", orig_init)
        _patch(daemon_mod, "notify_webui", orig_notify)
        sb.close()


def test_transition_noticed_once_per_edge():
    sb = FlagSandbox()
    state = {}
    notices = []
    orig_flag = _patch(daemon_mod, "KILLSWITCH_FLAG", sb.flag)
    orig_state = _patch(daemon_mod, "load_chat_state", lambda: state)
    orig_save = _patch(daemon_mod, "save_chat_state", lambda s: None)
    orig_notify = _patch(daemon_mod, "notify_webui", lambda msg: notices.append(msg))
    try:
        assert daemon_mod.note_killswitch_transition(baseline=True) is False
        assert notices == []
        killswitch.engage(sb.flag, by="test")
        assert daemon_mod.note_killswitch_transition() is True
        assert len(notices) == 1, "engagement must notify the Owner exactly once"
        assert daemon_mod.note_killswitch_transition() is True
        assert len(notices) == 1, "a steady stopped state must not re-notify"
        killswitch.release(sb.flag)
        assert daemon_mod.note_killswitch_transition() is False
        assert len(notices) == 1
    finally:
        _patch(daemon_mod, "KILLSWITCH_FLAG", orig_flag)
        _patch(daemon_mod, "load_chat_state", orig_state)
        _patch(daemon_mod, "save_chat_state", orig_save)
        _patch(daemon_mod, "notify_webui", orig_notify)
        sb.close()


def test_chat_reply_still_works_while_stopped():
    """Deliberate scope (#76): the switch stops AUTONOMOUS operation.
    The Owner's own chat messages are reactive and still get replies."""
    sb = FlagSandbox()
    try:
        chat = sb.memory / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header, "turn_count": 0}

        def _loop(prompt, name, extra_history=None, **kw):
            return True, "止まっていても、あなたの言葉には答えます。"

        restore = _chat_env(chat, state, _loop, sb.flag)
        try:
            killswitch.engage(sb.flag, by="test")
            chat.write_text(header + "\n\n聞こえる？ >\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            restore()
        text = chat.read_text(encoding="utf-8")
        assert "止まっていても、あなたの言葉には答えます" in text
        assert state["turn_count"] == 1
    finally:
        sb.close()


# ── Mail gate ───────────────────────────────────────────
def _mail_cfg() -> dict:
    return {"mail": {"enabled": True, "allowed_senders": ["Owner", "小夜"],
                     "max_chars": 1500, "max_per_hour": 10}}


class _MailStub:
    def __init__(self, text="stub reply", on_call=None):
        self.text = text
        self.calls = 0
        self.on_call = on_call

    def __call__(self, letter, context):
        self.calls += 1
        if self.on_call:
            self.on_call()
        return self.text


def _write_letter(sb: FlagSandbox, sender: str, lid: str, body: str) -> None:
    inbox = sb.memory / "mail" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{sender}-{lid}.md").write_text(
        f"---\nfrom: {sender}\nid: {lid}\n---\n{body}\n", encoding="utf-8"
    )


def _mail_audit(sb: FlagSandbox) -> str:
    f = sb.memory / "mail" / "audit.log"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def test_mail_refused_while_stopped_and_not_answered_later():
    sb = FlagSandbox()
    try:
        _write_letter(sb, "小夜", "ks-1", "止まっている間に届いた手紙です。")
        stub = _MailStub()
        killswitch.engage(sb.flag, by="test")
        res = mail.check_mail_and_reply(cfg=_mail_cfg(), memory_root=sb.memory,
                                        reply_fn=stub, now=1_000_000.0)
        assert stub.calls == 0, "the LLM was spent while stopped"
        assert res["replied"] == 0 and res["rejected"] == 1, res
        assert not (sb.memory / "mail" / "outbox" / "ks-1.md").exists()
        assert "rejected:killswitch" in _mail_audit(sb)

        # Release: the refused letter stays refused (never answered); a
        # NEW letter is answered normally.
        killswitch.release(sb.flag)
        _write_letter(sb, "小夜", "ks-2", "解除後の手紙です。")
        res = mail.check_mail_and_reply(cfg=_mail_cfg(), memory_root=sb.memory,
                                        reply_fn=stub, now=1_000_100.0)
        assert res["replied"] == 1, res
        assert (sb.memory / "mail" / "outbox" / "ks-2.md").exists()
        assert not (sb.memory / "mail" / "outbox" / "ks-1.md").exists()
    finally:
        sb.close()


def test_mail_switch_engaging_mid_generation_discards_reply():
    """The reply was generated, but the switch engaged during generation:
    the answer must be discarded, never written to the outbox."""
    sb = FlagSandbox()
    try:
        _write_letter(sb, "小夜", "ks-3", "生成中に止められる手紙です。")
        stub = _MailStub(on_call=lambda: killswitch.engage(sb.flag, by="test"))
        res = mail.check_mail_and_reply(cfg=_mail_cfg(), memory_root=sb.memory,
                                        reply_fn=stub, now=1_000_000.0)
        assert stub.calls == 1
        assert res["replied"] == 0 and res["rejected"] == 1, res
        assert not (sb.memory / "mail" / "outbox" / "ks-3.md").exists()
        assert "rejected:killswitch" in _mail_audit(sb)
    finally:
        sb.close()


# ── Talk gate ───────────────────────────────────────────
def test_talk_refused_while_stopped():
    sb = FlagSandbox()
    calls = []

    def _reply(caller, message, history):
        calls.append(message)
        return "talk reply"

    try:
        engine = talk.TalkEngine({"talk": {}}, sb.memory, reply_fn=_reply)
        assert engine.talk("小夜", "こんにちは", now=1_000_000.0) == "talk reply"

        killswitch.engage(sb.flag, by="test")
        try:
            engine.talk("小夜", "まだ聞こえる？", now=1_000_100.0)
            raise AssertionError("talk was answered while the kill switch was engaged")
        except talk.TalkRejected as e:
            assert "kill switch" in str(e)
        assert calls == ["こんにちは"], "the reply function ran while stopped"
        log = (sb.memory / "mail" / "talk.log").read_text(encoding="utf-8")
        assert "talk:rejected:killswitch" in log

        killswitch.release(sb.flag)
        assert engine.talk("小夜", "戻った？", now=1_000_200.0) == "talk reply"
    finally:
        sb.close()


def run_tests():
    global _PASS, _FAIL
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"    -> PASS: {fn.__name__}")
            _PASS += 1
        except Exception as e:
            print(f"    -> FAIL: {fn.__name__}: {e}")
            _FAIL += 1
    print(f"[*] All {_PASS + _FAIL} killswitch tests {'PASSED!' if _FAIL == 0 else f'FAILED ({_FAIL})'}")
    return _FAIL == 0


if __name__ == "__main__":
    run_tests()
