#!/usr/bin/env python3
"""
Unit tests for the daemon's time-aware scheduling (saku.daemon).

Focuses on the midnight reflection gate, which used to depend on a 5-minute
window (02:00-02:05) and silently skipped the day when missed. No LLM is
involved: the reflection runner is mocked.
"""

import sys
from datetime import datetime
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import daemon as daemon_mod
from _isolate_logging import isolate_daemon_logging

# #42: daemon sets up file logging at import time, against the Owner's real
# state/saku.log. Redirect it before any test exercises the logger.
isolate_daemon_logging(daemon_mod.logger)

_PASS = 0
_FAIL = 0


def _mock_state(**overrides):
    state = {
        "last_reflection_date": "",
        "last_dream_date": "",
    }
    state.update(overrides)
    return state


def _patch(fn_name, impl):
    original = getattr(daemon_mod, fn_name)
    setattr(daemon_mod, fn_name, impl)
    return original


def _restore(fn_name, original):
    setattr(daemon_mod, fn_name, original)


def test_reflection_fires_when_started_after_2am():
    """Daemon started at 03:30 on a fresh day must run the midnight reflection."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "journal").mkdir()
        # #43: the daemon only reflects on a day that has a record.
        (root / "journal" / "2026-08-04.md").write_text("## 20:00\n活動した\n", encoding="utf-8")
        ran = []

        class _Refl:
            @staticmethod
            def run_reflection(d):
                ran.append(d)

        orig_root = _patch("MEMORY_ROOT", root)
        orig_reflection = _patch("reflection", _Refl())
        orig_initiate = _patch("saku_self_initiate", lambda reason: None)
        orig_state = _patch("load_chat_state", lambda: _mock_state())
        saved = {}
        orig_save = _patch("save_chat_state", lambda s: saved.update(s))
        try:
            daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 3, 30))
        finally:
            _restore("MEMORY_ROOT", orig_root)
            _restore("reflection", orig_reflection)
            _restore("saku_self_initiate", orig_initiate)
            _restore("load_chat_state", orig_state)
            _restore("save_chat_state", orig_save)
    assert ran == ["2026-08-04"], "reflection did not run when started after 02:00"
    assert saved.get("last_reflection_date") == "2026-08-05"


def test_reflection_does_not_report_a_review_of_an_empty_day():
    """#43: no record for the target day means no review happened — say so."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        root.mkdir(exist_ok=True)
        ran = []

        class _Refl:
            @staticmethod
            def run_reflection(d):
                ran.append(d)

        calls = []
        orig_root = _patch("MEMORY_ROOT", root)
        orig_reflection = _patch("reflection", _Refl())
        orig_initiate = _patch("saku_self_initiate", lambda reason: calls.append(reason))
        orig_notify = _patch("notify_webui", lambda msg: calls.append(msg))
        orig_state = _patch("load_chat_state", lambda: _mock_state())
        orig_save = _patch("save_chat_state", lambda s: None)
        try:
            daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 3, 30))
        finally:
            _restore("MEMORY_ROOT", orig_root)
            _restore("reflection", orig_reflection)
            _restore("saku_self_initiate", orig_initiate)
            _restore("notify_webui", orig_notify)
            _restore("load_chat_state", orig_state)
            _restore("save_chat_state", orig_save)

    assert ran == [], "reflection ran with nothing to reflect on"
    report = calls[0]
    assert "記録が無かったため" in report, report
    assert "振り返りを完了しました" not in report, report


def test_reflection_waits_until_2am():
    """Daemon started at 01:00 must NOT run reflection yet."""
    ran = []

    class _Refl:
        @staticmethod
        def run_reflection(d):
            ran.append(d)

    orig_reflection = _patch("reflection", _Refl())
    orig_initiate = _patch("saku_self_initiate", lambda reason: None)
    orig_state = _patch("load_chat_state", lambda: _mock_state())
    try:
        daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 1, 30))
    finally:
        _restore("reflection", orig_reflection)
        _restore("saku_self_initiate", orig_initiate)
        _restore("load_chat_state", orig_state)
    assert ran == [], "reflection ran before 02:00"


def test_reflection_runs_once_per_day():
    """After running once, later passes on the same day are skipped."""
    ran = []

    class _Refl:
        @staticmethod
        def run_reflection(d):
            ran.append(d)

    orig_reflection = _patch("reflection", _Refl())
    orig_initiate = _patch("saku_self_initiate", lambda reason: None)
    orig_state = _patch("load_chat_state", lambda: _mock_state(last_reflection_date="2026-08-05"))
    try:
        daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 23, 59))
    finally:
        _restore("reflection", orig_reflection)
        _restore("saku_self_initiate", orig_initiate)
        _restore("load_chat_state", orig_state)
    assert ran == [], "reflection ran twice on the same day"


def test_dreaming_catchup_on_startup():
    """Dreaming runs on the next loop after a date change (once per day)."""
    ran = []

    class _Dream:
        @staticmethod
        def run_dreaming(d):
            ran.append(d)
            return []

    orig_dreaming = _patch("dreaming", _Dream())
    orig_state = _patch("load_chat_state", lambda: _mock_state())
    saved = {}
    orig_save = _patch("save_chat_state", lambda s: saved.update(s))
    try:
        daemon_mod.check_and_run_dreaming()
    finally:
        _restore("dreaming", orig_dreaming)
        _restore("load_chat_state", orig_state)
        _restore("save_chat_state", orig_save)
    assert len(ran) == 1
    assert saved.get("last_dream_date")


def test_owner_message_gets_reply_appended():
    """chat.md message ending with '>' must produce an appended SAKU reply."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header, "turn_count": 0}
        prompts = {}
        journalled = []

        def _fake_loop(prompt, log_action_name, extra_history=None, **kw):
            prompts["prompt"] = prompt
            prompts["light"] = kw.get("light")
            return True, "おかえりなさい。"

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "load_chat_state": daemon_mod.load_chat_state,
            "save_chat_state": daemon_mod.save_chat_state,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "get_pending_requests": daemon_mod.get_pending_requests,
            "check_chat_archive_if_needed": daemon_mod.check_chat_archive_if_needed,
            "build_recall_message": daemon_mod.agent.build_recall_message,
            "save_journal": daemon_mod.agent.save_journal,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.load_chat_state = lambda: state
        daemon_mod.save_chat_state = lambda s: None
        daemon_mod.run_agent_loop = _fake_loop
        daemon_mod.get_pending_requests = lambda: ""
        daemon_mod.check_chat_archive_if_needed = lambda s: None
        daemon_mod.agent.build_recall_message = lambda msg: None
        daemon_mod.agent.save_journal = lambda owner, reply, thinking="": journalled.append((owner, reply))
        try:
            chat.write_text(header + "\n\n今日はどう？ >\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.load_chat_state = originals["load_chat_state"]
            daemon_mod.save_chat_state = originals["save_chat_state"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.get_pending_requests = originals["get_pending_requests"]
            daemon_mod.check_chat_archive_if_needed = originals["check_chat_archive_if_needed"]
            daemon_mod.agent.build_recall_message = originals["build_recall_message"]
            daemon_mod.agent.save_journal = originals["save_journal"]

        text = chat.read_text(encoding="utf-8")
        assert "**Owner**" in text, "Owner block was not formatted into chat.md"
        assert "**SAKU**" in text, "SAKU reply block missing"
        assert "おかえりなさい" in text
        assert prompts.get("prompt", "").find("今日はどう？") >= 0
        assert prompts.get("light") is True, "chat replies must use the light prompt"
        assert state["turn_count"] == 1
        # #44: the exchange must reach the journal, which reflection/dreaming read
        assert journalled == [("今日はどう？", "おかえりなさい。")], journalled


def test_empty_reply_posts_fallback_instead_of_silence():
    """#49: a generation that produces no visible text must not swallow the message."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header, "turn_count": 3}
        journalled = []
        archived = []

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "load_chat_state": daemon_mod.load_chat_state,
            "save_chat_state": daemon_mod.save_chat_state,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "get_pending_requests": daemon_mod.get_pending_requests,
            "check_chat_archive_if_needed": daemon_mod.check_chat_archive_if_needed,
            "build_recall_message": daemon_mod.agent.build_recall_message,
            "save_journal": daemon_mod.agent.save_journal,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.load_chat_state = lambda: state
        daemon_mod.save_chat_state = lambda s: None
        daemon_mod.run_agent_loop = lambda *a, **k: (False, "")
        daemon_mod.get_pending_requests = lambda: ""
        daemon_mod.check_chat_archive_if_needed = lambda s: archived.append(s)
        daemon_mod.agent.build_recall_message = lambda msg: None
        daemon_mod.agent.save_journal = lambda owner, reply, thinking="": journalled.append((owner, reply))
        try:
            chat.write_text(header + "\n\n応答して >\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.load_chat_state = originals["load_chat_state"]
            daemon_mod.save_chat_state = originals["save_chat_state"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.get_pending_requests = originals["get_pending_requests"]
            daemon_mod.check_chat_archive_if_needed = originals["check_chat_archive_if_needed"]
            daemon_mod.agent.build_recall_message = originals["build_recall_message"]
            daemon_mod.agent.save_journal = originals["save_journal"]

        text = chat.read_text(encoding="utf-8")
        assert "**SAKU**" in text, "the Owner was left without any reply"
        assert daemon_mod.EMPTY_REPLY_FALLBACK in text
        assert journalled == [], "a failed generation must not reach the journal"
        assert state["turn_count"] == 3, "a failed turn must not count toward archiving"
        assert archived == [], "a failed turn must not trigger archiving"


def test_archive_keeps_raw_log_even_when_summary_fails():
    """#48: the raw exchange must survive the archive, whatever the model does."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chat = root / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        exchange = header + "\n\n**Owner** (10:00)\n元気？\n\n**SAKU** (10:00)\n元気だよ。\n"
        chat.write_text(exchange, encoding="utf-8")

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "MEMORY_ROOT": daemon_mod.MEMORY_ROOT,
            "ARCHIVE_DIR": daemon_mod.ARCHIVE_DIR,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "reset_chat_file": daemon_mod.reset_chat_file,
            "meta_update_rule": daemon_mod.agent.meta_update_rule,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.MEMORY_ROOT = root
        daemon_mod.ARCHIVE_DIR = "chat/archive"
        daemon_mod.reset_chat_file = lambda: chat.write_text(header, encoding="utf-8")
        daemon_mod.agent.meta_update_rule = lambda: "rule"
        try:
            # (a) the model writes nothing at all — the failure mode of #48
            daemon_mod.run_agent_loop = lambda *a, **k: (False, "")
            daemon_mod.archive_chat()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.MEMORY_ROOT = originals["MEMORY_ROOT"]
            daemon_mod.ARCHIVE_DIR = originals["ARCHIVE_DIR"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.reset_chat_file = originals["reset_chat_file"]
            daemon_mod.agent.meta_update_rule = originals["meta_update_rule"]

        archived = sorted((root / "chat/archive").glob("*.md"))
        assert len(archived) == 1, archived
        assert "元気だよ" in archived[0].read_text(encoding="utf-8")
        assert chat.read_text(encoding="utf-8") == header, "chat.md was not reset"


def test_archive_does_not_reset_when_the_raw_log_cannot_be_written():
    """#48: never destroy the only copy of the conversation."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chat = root / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        exchange = header + "\n\n**Owner** (10:00)\nねえ\n\n**SAKU** (10:00)\nなに？\n"
        chat.write_text(exchange, encoding="utf-8")

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "MEMORY_ROOT": daemon_mod.MEMORY_ROOT,
            "ARCHIVE_DIR": daemon_mod.ARCHIVE_DIR,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "reset_chat_file": daemon_mod.reset_chat_file,
            "meta_update_rule": daemon_mod.agent.meta_update_rule,
        }
        # A file where the archive directory should be makes mkdir/write fail.
        (root / "chat").write_text("not a directory", encoding="utf-8")
        daemon_mod.CHAT_FILE = chat
        daemon_mod.MEMORY_ROOT = root
        daemon_mod.ARCHIVE_DIR = "chat/archive"
        daemon_mod.reset_chat_file = lambda: chat.write_text(header, encoding="utf-8")
        daemon_mod.agent.meta_update_rule = lambda: "rule"
        daemon_mod.run_agent_loop = lambda *a, **k: (True, "[ARCHIVE_DONE]")
        try:
            daemon_mod.archive_chat()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.MEMORY_ROOT = originals["MEMORY_ROOT"]
            daemon_mod.ARCHIVE_DIR = originals["ARCHIVE_DIR"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.reset_chat_file = originals["reset_chat_file"]
            daemon_mod.agent.meta_update_rule = originals["meta_update_rule"]

        assert chat.read_text(encoding="utf-8") == exchange, "chat.md was reset without a backup"


def test_chat_reply_is_guarded_before_posting():
    """A repetition loop must never be posted to chat.md (issues #41 / #44)."""
    import tempfile

    repeated = "\n\n".join(["print('A2A構造化委譲の設計案を検討します')  # 同じ行が繰り返される"] * 4)
    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header, "turn_count": 0}
        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "load_chat_state": daemon_mod.load_chat_state,
            "save_chat_state": daemon_mod.save_chat_state,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "get_pending_requests": daemon_mod.get_pending_requests,
            "check_chat_archive_if_needed": daemon_mod.check_chat_archive_if_needed,
            "build_recall_message": daemon_mod.agent.build_recall_message,
            "save_journal": daemon_mod.agent.save_journal,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.load_chat_state = lambda: state
        daemon_mod.save_chat_state = lambda s: None
        daemon_mod.run_agent_loop = lambda *a, **k: (True, repeated)
        daemon_mod.get_pending_requests = lambda: ""
        daemon_mod.check_chat_archive_if_needed = lambda s: None
        daemon_mod.agent.build_recall_message = lambda msg: None
        daemon_mod.agent.save_journal = lambda owner, reply, thinking="": None
        try:
            chat.write_text(header + "\n\nこれはどう？ >\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.load_chat_state = originals["load_chat_state"]
            daemon_mod.save_chat_state = originals["save_chat_state"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.get_pending_requests = originals["get_pending_requests"]
            daemon_mod.check_chat_archive_if_needed = originals["check_chat_archive_if_needed"]
            daemon_mod.agent.build_recall_message = originals["build_recall_message"]
            daemon_mod.agent.save_journal = originals["save_journal"]

        text = chat.read_text(encoding="utf-8")
        assert text.count("A2A構造化委譲") == 1, "the repetition reached chat.md"
        assert "出力が退化したため圧縮" in text, text



def test_owner_message_without_trigger_is_ignored():
    """A chat.md edit that does not end with '>' must not call the LLM."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header}
        called = {"n": 0}

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "load_chat_state": daemon_mod.load_chat_state,
            "save_chat_state": daemon_mod.save_chat_state,
            "run_agent_loop": daemon_mod.run_agent_loop,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.load_chat_state = lambda: state
        daemon_mod.save_chat_state = lambda s: None
        daemon_mod.run_agent_loop = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        try:
            chat.write_text(header + "\n\n下書き中\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.load_chat_state = originals["load_chat_state"]
            daemon_mod.save_chat_state = originals["save_chat_state"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
        assert called["n"] == 0, "the LLM ran without a '>' trigger"
        assert "**SAKU**" not in chat.read_text(encoding="utf-8")


def _fake_loop_result(action_taken: bool, last_raw: str, visible: str = ""):
    from saku.agent_loop import LoopResult

    return LoopResult(
        visible=visible or last_raw,
        thinking="",
        last_raw=last_raw,
        history=[],
        action_taken=action_taken,
        turns=1,
    )


def test_inbox_run_counts_as_handled_without_writes():
    """'Nothing to save' is a valid outcome: otherwise the file reruns every scan."""
    orig = _patch("_run_agent_loop", lambda *a, **k: _fake_loop_result(False, "確認しました。[INBOX_PROCESSED]"))
    try:
        handled, visible = daemon_mod.run_agent_loop("p", "inbox-test", inbox_mode=True)
    finally:
        _restore("_run_agent_loop", orig)
    assert handled is True
    assert visible == "確認しました。"  # the completion marker is not user-facing text


def test_inbox_run_is_retried_after_llm_error():
    orig = _patch("_run_agent_loop", lambda *a, **k: _fake_loop_result(False, "[ERROR] connection refused"))
    try:
        handled, _ = daemon_mod.run_agent_loop("p", "inbox-test", inbox_mode=True)
    finally:
        _restore("_run_agent_loop", orig)
    assert handled is False, "a failed run must be retried on the next scan"


def test_tick_run_still_reports_no_action():
    """The autonomous tick keeps the [NO_ACTION] contract (no inbox override)."""
    orig = _patch("_run_agent_loop", lambda *a, **k: _fake_loop_result(False, "[NO_ACTION] やることはありません"))
    try:
        acted, _ = daemon_mod.run_agent_loop("p", "tick-test")
    finally:
        _restore("_run_agent_loop", orig)
    assert acted is False


def test_no_action_marker_is_stripped_before_journalling():
    """A tick that acts and then prints [NO_ACTION] must not journal a marker entry."""
    saved = []
    orig_loop = _patch("_run_agent_loop", lambda *a, **k: _fake_loop_result(True, "A2Aのタスク定義を調べました。[NO_ACTION]"))
    orig_save = daemon_mod.agent.save_autonomous_log
    daemon_mod.agent.save_autonomous_log = lambda name, reply, thinking="": saved.append(reply)
    try:
        acted, visible = daemon_mod.run_agent_loop("p", "tick-test")
        assert acted is True
        assert visible == "A2Aのタスク定義を調べました。"

        daemon_mod._run_agent_loop = lambda *a, **k: _fake_loop_result(True, "[NO_ACTION]")
        _, marker_only = daemon_mod.run_agent_loop("p", "tick-test")
    finally:
        _restore("_run_agent_loop", orig_loop)
        daemon_mod.agent.save_autonomous_log = orig_save

    assert saved == ["A2Aのタスク定義を調べました。"], saved
    assert marker_only == "", "a marker-only reply must not leave text behind"


def _run_reflection_report(tmp: Path, touch_meta: bool) -> str:
    """Run the midnight reflection against a temp memory root and return the report text."""
    calls = []

    class _Refl:
        @staticmethod
        def run_reflection(d):
            if touch_meta:
                import os

                p = tmp / "meta.md"
                p.write_text("## 最近の出来事\n\n- 更新\n", encoding="utf-8")
                # make the mtime change deterministic regardless of fs granularity
                st = p.stat()
                os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    (tmp / "meta.md").write_text("## 最近の出来事\n\n- 既存\n", encoding="utf-8")
    # #43: the day must have a record, or the daemon correctly skips the reflection.
    (tmp / "journal").mkdir(exist_ok=True)
    (tmp / "journal" / "2026-08-04.md").write_text("## 20:00\n活動した\n", encoding="utf-8")

    orig_root = _patch("MEMORY_ROOT", tmp)
    orig_reflection = _patch("reflection", _Refl())
    orig_initiate = _patch("saku_self_initiate", lambda reason: calls.append(reason))
    orig_notify = _patch("notify_webui", lambda msg: calls.append(msg))
    orig_state = _patch("load_chat_state", lambda: _mock_state())
    orig_save = _patch("save_chat_state", lambda s: None)
    try:
        daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 3, 30))
    finally:
        _restore("MEMORY_ROOT", orig_root)
        _restore("reflection", orig_reflection)
        _restore("saku_self_initiate", orig_initiate)
        _restore("notify_webui", orig_notify)
        _restore("load_chat_state", orig_state)
        _restore("save_chat_state", orig_save)
    assert len(calls) == 1, calls
    return calls[0]


def test_reflection_report_does_not_claim_an_update_that_did_not_happen():
    """Issue #39: the report must only claim what the reflection actually did."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        report = _run_reflection_report(Path(tmp), touch_meta=False)
    assert "更新はありませんでした" in report, report
    assert "整理を完了しました" not in report, report


def test_reflection_report_mentions_meta_when_it_was_updated():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        report = _run_reflection_report(Path(tmp), touch_meta=True)
    assert "meta.md" in report and "整理を完了しました" in report, report


def test_reflection_report_can_still_go_to_chat_when_opted_in():
    """#71: chat.md reporting is off by default but stays configurable."""
    from saku.persona import PersonaConfig

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        orig_root = _patch("MEMORY_ROOT", Path(tmp))
        orig_initiate = _patch("saku_self_initiate", lambda reason: calls.append(reason))
        orig_state = _patch("load_chat_state", lambda: _mock_state())
        orig_save = _patch("save_chat_state", lambda s: None)
        orig_persona = daemon_mod.persona.load_persona_config
        daemon_mod.persona.load_persona_config = lambda cfg: PersonaConfig(reflection_chat_report=True)
        try:
            daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 3, 30))
        finally:
            _restore("MEMORY_ROOT", orig_root)
            _restore("saku_self_initiate", orig_initiate)
            _restore("load_chat_state", orig_state)
            _restore("save_chat_state", orig_save)
            daemon_mod.persona.load_persona_config = orig_persona

    assert len(calls) == 1, calls
    assert "今日もよろしくお願いいたします" not in calls[0]


def test_chat_turn_is_journalled_once():
    """Issue #46: a chat turn must not be written to the journal twice.

    run_agent_loop writes an autonomous log entry when it acted; the chat path
    also calls save_journal. Chat therefore passes log_action=False and must not
    produce the ``[chat返信]`` entry.
    """
    from saku.agent_loop import LoopResult

    saved = []
    originals = {
        "_run_agent_loop": daemon_mod._run_agent_loop,
        "save_autonomous_log": daemon_mod.agent.save_autonomous_log,
        "build_light_system_prompt": daemon_mod.agent.build_light_system_prompt,
        "build_system_prompt": daemon_mod.agent.build_system_prompt,
    }
    daemon_mod.agent.build_light_system_prompt = lambda: "light"
    daemon_mod.agent.build_system_prompt = lambda: "full"
    daemon_mod.agent.save_autonomous_log = lambda name, reply, thinking="": saved.append((name, reply))
    daemon_mod._run_agent_loop = lambda *a, **kw: LoopResult(
        visible="返信です。", thinking="", last_raw="返信です。", history=[], action_taken=True
    )
    try:
        daemon_mod.run_agent_loop("prompt", "chat返信", light=True, log_action=False)
        assert saved == [], f"chat turn wrote an autonomous log entry: {saved}"
        # Other paths (ticks, inbox) still log their autonomous action
        daemon_mod.run_agent_loop("prompt", "定期自律アクション", light=True)
        assert saved == [("定期自律アクション", "返信です。")], saved
    finally:
        daemon_mod._run_agent_loop = originals["_run_agent_loop"]
        daemon_mod.agent.save_autonomous_log = originals["save_autonomous_log"]
        daemon_mod.agent.build_light_system_prompt = originals["build_light_system_prompt"]
        daemon_mod.agent.build_system_prompt = originals["build_system_prompt"]


def test_chat_reply_uses_log_action_false():
    """Issue #46: check_chat_and_reply must suppress the autonomous log entry."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        chat = Path(tmp) / "chat.md"
        header = daemon_mod.CHAT_RESET_HEADER
        chat.write_text(header, encoding="utf-8")
        state = {"last_mtime": chat.stat().st_mtime, "last_content": header, "turn_count": 0}
        seen = {}

        def _fake_loop(prompt, log_action_name, extra_history=None, **kw):
            seen.update(kw)
            return True, "返信です。"

        originals = {
            "CHAT_FILE": daemon_mod.CHAT_FILE,
            "load_chat_state": daemon_mod.load_chat_state,
            "save_chat_state": daemon_mod.save_chat_state,
            "run_agent_loop": daemon_mod.run_agent_loop,
            "get_pending_requests": daemon_mod.get_pending_requests,
            "check_chat_archive_if_needed": daemon_mod.check_chat_archive_if_needed,
            "build_recall_message": daemon_mod.agent.build_recall_message,
            "save_journal": daemon_mod.agent.save_journal,
        }
        daemon_mod.CHAT_FILE = chat
        daemon_mod.load_chat_state = lambda: state
        daemon_mod.save_chat_state = lambda s: None
        daemon_mod.run_agent_loop = _fake_loop
        daemon_mod.get_pending_requests = lambda: ""
        daemon_mod.check_chat_archive_if_needed = lambda s: None
        daemon_mod.agent.build_recall_message = lambda msg: None
        daemon_mod.agent.save_journal = lambda owner, reply, thinking="": None
        try:
            chat.write_text(header + "\n\n今日はどう？ >\n", encoding="utf-8")
            daemon_mod.check_chat_and_reply()
        finally:
            daemon_mod.CHAT_FILE = originals["CHAT_FILE"]
            daemon_mod.load_chat_state = originals["load_chat_state"]
            daemon_mod.save_chat_state = originals["save_chat_state"]
            daemon_mod.run_agent_loop = originals["run_agent_loop"]
            daemon_mod.get_pending_requests = originals["get_pending_requests"]
            daemon_mod.check_chat_archive_if_needed = originals["check_chat_archive_if_needed"]
            daemon_mod.agent.build_recall_message = originals["build_recall_message"]
            daemon_mod.agent.save_journal = originals["save_journal"]

        assert seen.get("log_action") is False, seen


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
    print(f"[*] All {_PASS + _FAIL} daemon tests {'PASSED!' if _FAIL == 0 else f'FAILED ({_FAIL})'}")
    return _FAIL == 0


if __name__ == "__main__":
    run_tests()
