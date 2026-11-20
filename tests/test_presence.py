#!/usr/bin/env python3
"""Tests for the presence hint (#82): where the Owner probably is."""

import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import presence
from saku.config import PresenceConfig


def _ts(year, month, day, hour, minute=0) -> float:
    return time.mktime(datetime(year, month, day, hour, minute).timetuple())


def _root(tmp: Path) -> Path:
    root = tmp / "memory"
    (root / "journal").mkdir(parents=True)
    (root / "state").mkdir(parents=True)
    return root


def test_manual_override_wins_and_expires():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        cfg = PresenceConfig(sleep_start="00:00", sleep_end="00:01")
        now = _ts(2026, 11, 18, 10, 0)

        presence.set_manual(root, "sleeping", minutes=30, cfg=cfg, now=now)
        assert presence.load(root, cfg, now=now).state == "sleeping"
        assert presence.load(root, cfg, now=now + 10 * 60).state == "sleeping"
        # After the window it falls back to the normal rules.
        later = presence.load(root, cfg, now=now + 31 * 60)
        assert later.source != "manual"

        presence.set_manual(root, "clear", cfg=cfg, now=now)
        assert presence.read_manual(root, cfg, now=now) is None


def test_sleep_window_is_quiet():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        cfg = PresenceConfig(sleep_start="00:30", sleep_end="06:30")
        p = presence.load(root, cfg, now=_ts(2026, 11, 18, 3, 0))
        assert p.state == "sleeping"
        assert p.can_speak is False
        assert presence.load(root, cfg, now=_ts(2026, 11, 18, 8, 0)).state != "sleeping"


def test_work_hours_depend_on_configuration():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        cfg = PresenceConfig(work_days=[0, 1, 2, 3, 4], work_start="09:00", work_end="18:00")
        # 2026-11-18 is a Wednesday.
        p = presence.load(root, cfg, now=_ts(2026, 11, 18, 10, 0))
        assert p.state == "working"
        assert p.can_speak is False, "quiet during work by default"

        weekend = presence.load(root, cfg, now=_ts(2026, 11, 21, 10, 0))
        assert weekend.state != "working"

        talking = presence.load(
            root,
            PresenceConfig(work_days=[0, 1, 2, 3, 4], work_start="09:00", work_end="18:00",
                           speak_while_working=True),
            now=_ts(2026, 11, 18, 10, 0),
        )
        assert talking.can_speak is True


def test_away_after_inactivity():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        chat = root / "chat.md"
        chat.write_text("hi", encoding="utf-8")
        now = _ts(2026, 11, 18, 20, 0)
        old = now - 4 * 3600
        os.utime(chat, (old, old))

        p = presence.load(
            root,
            PresenceConfig(away_after_minutes=180, sleep_start="00:00", sleep_end="00:01",
                           work_days=[]),
            now=now,
        )
        assert p.state == "away"
        assert p.can_speak is True, "an away Owner can still be reached later"

        recent = now - 10 * 60
        os.utime(chat, (recent, recent))
        assert presence.load(
            root,
            PresenceConfig(away_after_minutes=180, sleep_start="00:00", sleep_end="00:01",
                           work_days=[]),
            now=now,
        ).state == "present"


def test_disabled_presence_always_allows_speech():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        p = presence.load(root, PresenceConfig(enabled=False), now=_ts(2026, 11, 18, 3, 0))
        assert p.state == "present"
        assert p.source == "disabled"
        assert p.can_speak is True


def test_ui_activity_is_an_activity_source():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        now = _ts(2026, 11, 18, 20, 0)
        presence.record_ui_activity(root, now=now - 60)
        p = presence.load(
            root,
            PresenceConfig(away_after_minutes=30, sleep_start="00:00", sleep_end="00:01",
                           work_days=[]),
            now=now,
        )
        assert p.state == "present"
        assert p.last_activity


def test_set_manual_rejects_unknown_states():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(Path(tmp))
        try:
            presence.set_manual(root, "moon")
        except ValueError as e:
            assert "unknown presence state" in str(e)
        else:
            raise AssertionError("unknown state was accepted")


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} presence tests PASSED!")


if __name__ == "__main__":
    run_tests()
