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
    ran = []

    class _Refl:
        @staticmethod
        def run_reflection(d):
            ran.append(d)

    orig_reflection = _patch("reflection", _Refl())
    orig_initiate = _patch("saku_self_initiate", lambda reason: None)
    orig_state = _patch("load_chat_state", lambda: _mock_state())
    saved = {}
    orig_save = _patch("save_chat_state", lambda s: saved.update(s))
    try:
        daemon_mod.check_and_run_midnight_reflection(datetime(2026, 8, 5, 3, 30))
    finally:
        _restore("reflection", orig_reflection)
        _restore("saku_self_initiate", orig_initiate)
        _restore("load_chat_state", orig_state)
        _restore("save_chat_state", orig_save)
    assert ran, "reflection did not run when started after 02:00"
    assert saved.get("last_reflection_date") == "2026-08-05"


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
