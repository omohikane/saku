#!/usr/bin/env python3
"""
Unit tests for the weekly trend review (saku.weekly + the daemon gate, issue #45).

No LLM is involved: the agent loop is mocked and the memory root is a temporary
directory, so nothing is written to the live vault.
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
from saku import daemon as daemon_mod
from saku import weekly
from _isolate_logging import isolate_daemon_logging

# #42: daemon sets up file logging at import time, against the Owner's real
# state/saku.log. Redirect it before any test exercises the logger.
isolate_daemon_logging(daemon_mod.logger)

_PASS = 0
_FAIL = 0


def _patch(module, name, value):
    original = getattr(module, name)
    setattr(module, name, value)
    return original


def _restore(module, name, original):
    setattr(module, name, original)


def _loop_result(text: str):
    from saku.agent_loop import LoopResult

    return LoopResult(
        visible=text, thinking="", last_raw=text, history=[], action_taken=True, turns=1
    )


def _growth(**overrides):
    base = saku_config.GrowthConfig()
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ── collect_recent ────────────────────────────────────────

def test_collect_recent_reads_journal_and_monologue():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "journal").mkdir()
        (root / "monologue").mkdir()
        (root / "journal" / "2026-01-04.md").write_text("日記A", encoding="utf-8")
        (root / "monologue" / "2026-01-03.md").write_text("独り言B", encoding="utf-8")

        orig_root = _patch(weekly, "SAKU_ROOT", root)
        try:
            records, dates = weekly.collect_recent(7, 10000, datetime(2026, 1, 4).date())
        finally:
            _restore(weekly, "SAKU_ROOT", orig_root)

    assert dates == ["2026-01-04", "2026-01-03"], dates
    assert "日記A" in records and "独り言B" in records
    assert records.index("独り言B") < records.index("日記A"), "records must be oldest-first"


def test_collect_recent_respects_the_budget():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "journal").mkdir()
        for day in ("2026-01-04", "2026-01-03", "2026-01-02"):
            (root / "journal" / f"{day}.md").write_text("あ" * 200, encoding="utf-8")

        orig_root = _patch(weekly, "SAKU_ROOT", root)
        try:
            records, dates = weekly.collect_recent(7, 500, datetime(2026, 1, 4).date())
            _, all_dates = weekly.collect_recent(7, 10000, datetime(2026, 1, 4).date())
        finally:
            _restore(weekly, "SAKU_ROOT", orig_root)

    assert all_dates == ["2026-01-04", "2026-01-03", "2026-01-02"]
    assert len(dates) < len(all_dates), "the character budget was ignored"
    assert dates[0] == "2026-01-04", "the newest day must be kept"


def test_collect_recent_returns_nothing_for_an_empty_week():
    with tempfile.TemporaryDirectory() as tmp:
        orig_root = _patch(weekly, "SAKU_ROOT", Path(tmp))
        try:
            records, dates = weekly.collect_recent(7, 10000, datetime(2026, 1, 4).date())
        finally:
            _restore(weekly, "SAKU_ROOT", orig_root)
    assert records == "" and dates == []


# ── prompt ────────────────────────────────────────────────

def test_prompt_asks_for_trends_and_names_the_target():
    prompt = weekly.build_weekly_review_prompt(
        "2026-W01", ["2026-01-04", "2026-01-03"], "記録本文", "## 次にやりたいこと\n- A2A設計",
        "principles/weekly-2026-W01.md",
    )
    assert "principles/weekly-2026-W01.md" in prompt
    assert "2026-01-03 〜 2026-01-04" in prompt, prompt.splitlines()[3]
    assert "繰り返している" in prompt
    assert "次にやりたいこと" in prompt
    assert "A2A設計" in prompt, "the self-model intentions must be inlined for review"


# ── run_weekly_review ─────────────────────────────────────

def test_run_weekly_review_reports_a_written_note():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "journal").mkdir()
        (root / "principles").mkdir()
        (root / "journal" / "2026-01-04.md").write_text("記録", encoding="utf-8")

        def _fake_loop(*a, **k):
            (root / "principles" / "weekly-2026-W01.md").write_text("# 週次レビュー\n", encoding="utf-8")
            return _loop_result("まとめました。[WEEKLY_REVIEW_DONE]")

        originals = [
            ("weekly", "SAKU_ROOT", _patch(weekly, "SAKU_ROOT", root)),
            ("weekly", "_run_agent_loop", _patch(weekly, "_run_agent_loop", _fake_loop)),
            ("weekly", "load_growth_config", _patch(weekly.saku_config, "load_growth_config", lambda c: _growth())),
            ("agent", "build_system_prompt", _patch(weekly.agent, "build_system_prompt", lambda: "sys")),
        ]
        try:
            result = weekly.run_weekly_review(datetime(2026, 1, 4, 3, 30))
        finally:
            _restore(weekly, "SAKU_ROOT", originals[0][2])
            _restore(weekly, "_run_agent_loop", originals[1][2])
            _restore(weekly.saku_config, "load_growth_config", originals[2][2])
            _restore(weekly.agent, "build_system_prompt", originals[3][2])

    assert result["written"] is True, result
    assert result["target_path"] == "principles/weekly-2026-W01.md", result
    assert result["week"] == "2026-W01" and result["dates"] == ["2026-01-04"]


def test_run_weekly_review_reports_no_note_when_nothing_was_written():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "journal").mkdir()
        (root / "journal" / "2026-01-04.md").write_text("記録", encoding="utf-8")

        originals = [
            _patch(weekly, "SAKU_ROOT", root),
            _patch(weekly, "_run_agent_loop", lambda *a, **k: _loop_result("考えました。[WEEKLY_REVIEW_DONE]")),
            _patch(weekly.saku_config, "load_growth_config", lambda c: _growth()),
            _patch(weekly.agent, "build_system_prompt", lambda: "sys"),
        ]
        try:
            result = weekly.run_weekly_review(datetime(2026, 1, 4, 3, 30))
        finally:
            _restore(weekly, "SAKU_ROOT", originals[0])
            _restore(weekly, "_run_agent_loop", originals[1])
            _restore(weekly.saku_config, "load_growth_config", originals[2])
            _restore(weekly.agent, "build_system_prompt", originals[3])

    assert result["written"] is False
    assert result["reason"] == "no note written", result


def test_run_weekly_review_skips_a_week_without_records():
    with tempfile.TemporaryDirectory() as tmp:
        originals = [
            _patch(weekly, "SAKU_ROOT", Path(tmp)),
            _patch(weekly.saku_config, "load_growth_config", lambda c: _growth()),
        ]
        called = []
        orig_loop = _patch(weekly, "_run_agent_loop", lambda *a, **k: called.append(1))
        try:
            result = weekly.run_weekly_review(datetime(2026, 1, 4, 3, 30))
        finally:
            _restore(weekly, "SAKU_ROOT", originals[0])
            _restore(weekly.saku_config, "load_growth_config", originals[1])
            _restore(weekly, "_run_agent_loop", orig_loop)

    assert result["reason"] == "no records"
    assert called == [], "the LLM must not be called when there is nothing to read"


# ── daemon gate ───────────────────────────────────────────

def _run_gate(now, result=None, last_date="", gcfg=None):
    """Run the daemon gate and return (calls, reports, state)."""
    calls, reports = [], []
    state = {"last_weekly_review_date": last_date}

    class _Weekly:
        @staticmethod
        def run_weekly_review(_now):
            calls.append(_now)
            return result if result is not None else {"written": False, "reason": "no records"}

    originals = [
        _patch(daemon_mod, "weekly", _Weekly()),
        _patch(daemon_mod, "load_chat_state", lambda: state),
        _patch(daemon_mod, "save_chat_state", lambda s: None),
        _patch(daemon_mod, "saku_self_initiate", lambda reason: reports.append(reason)),
        _patch(daemon_mod, "notify_webui", lambda msg: reports.append(msg)),
        _patch(daemon_mod.agent.saku_config, "load_growth_config", lambda c: gcfg or _growth()),
    ]
    try:
        daemon_mod.check_and_run_weekly_review(now)
    finally:
        _restore(daemon_mod, "weekly", originals[0])
        _restore(daemon_mod, "load_chat_state", originals[1])
        _restore(daemon_mod, "save_chat_state", originals[2])
        _restore(daemon_mod, "saku_self_initiate", originals[3])
        _restore(daemon_mod, "notify_webui", originals[4])
        _restore(daemon_mod.agent.saku_config, "load_growth_config", originals[5])
    return calls, reports, state


def test_weekly_review_runs_on_the_configured_day_and_records_it():
    calls, reports, state = _run_gate(
        datetime(2026, 1, 4, 3, 30),  # Sunday
        result={"written": True, "week": "2026-W01", "target_path": "principles/weekly-2026-W01.md"},
    )
    assert len(calls) == 1, calls
    assert state.get("last_weekly_review_date") == "2026-01-04"
    assert len(reports) == 1 and "principles/weekly-2026-W01.md" in reports[0], reports


def test_weekly_review_skips_other_days():
    calls, reports, _ = _run_gate(datetime(2026, 1, 5, 3, 30))  # Monday
    assert calls == [] and reports == [], (calls, reports)


def test_weekly_review_waits_for_the_configured_hour():
    calls, _, _ = _run_gate(datetime(2026, 1, 4, 2, 30))
    assert calls == [], "the review ran before the configured hour"


def test_weekly_review_runs_once_per_day():
    calls, _, _ = _run_gate(datetime(2026, 1, 4, 23, 0), last_date="2026-01-04")
    assert calls == [], "the review ran twice on the same day"


def test_weekly_review_respects_the_weekday_setting():
    calls, _, _ = _run_gate(
        datetime(2026, 1, 7, 4, 0),  # Wednesday
        gcfg=_growth(weekly_review_weekday="wed"),
    )
    assert len(calls) == 1, calls


def test_weekly_review_can_be_disabled():
    calls, _, _ = _run_gate(datetime(2026, 1, 4, 3, 30), gcfg=_growth(weekly_review_enabled=False))
    assert calls == []


def test_weekly_review_report_does_not_claim_a_note_that_was_not_written():
    _, reports, _ = _run_gate(
        datetime(2026, 1, 4, 3, 30),
        result={"written": False, "reason": "no note written"},
    )
    assert len(reports) == 1
    assert "出力はありませんでした" in reports[0], reports[0]
    assert "まとめました" not in reports[0], reports[0]


def test_weekly_review_report_is_honest_about_an_empty_period():
    _, reports, state = _run_gate(datetime(2026, 1, 4, 3, 30), result={"written": False, "reason": "no records"})
    assert "記録が無かった" in reports[0], reports[0]
    assert state.get("last_weekly_review_date") == "2026-01-04", "a quiet week must still be recorded"


def test_weekly_review_report_can_be_suppressed():
    calls, reports, state = _run_gate(
        datetime(2026, 1, 4, 3, 30),
        result={"written": True, "week": "2026-W01", "target_path": "principles/weekly-2026-W01.md"},
        gcfg=_growth(weekly_review_report=False),
    )
    assert len(calls) == 1 and reports == []
    assert state.get("last_weekly_review_date") == "2026-01-04"


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
    print(f"[*] All {_PASS + _FAIL} weekly review tests {'PASSED!' if _FAIL == 0 else f'FAILED ({_FAIL})'}")
    return _FAIL == 0


if __name__ == "__main__":
    run_tests()
