#!/usr/bin/env python3
"""Tests for the daemon heartbeat (#75): saku.heartbeat + daemon/diagnostic wiring.

Temporary directories only; no daemon is started and no network is touched.
"""

import sys
import tempfile
import time
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import daemon as daemon_mod
from saku import heartbeat
from _isolate_logging import isolate_daemon_logging

# The daemon module configures file logging at import time, against the Owner's
# real state/saku.log. Redirect it before any test exercises the logger (#42).
isolate_daemon_logging(daemon_mod.logger)


def test_beat_writes_and_reads_back():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = heartbeat.beat(root, interval=5)
        assert path == root / "state" / "heartbeat.json"
        hb = heartbeat.read(root)
        assert hb["interval"] == 5
        assert hb["beats"] == 1
        assert hb["pid"] > 0
        assert hb["last_alive_iso"]


def test_beat_preserves_start_and_counts_beats():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        heartbeat.beat(root, interval=5)
        first = heartbeat.read(root)
        heartbeat.beat(root, interval=5)
        second = heartbeat.read(root)
        assert second["beats"] == 2
        assert second["started_at"] == first["started_at"]
        assert second["last_alive"] >= first["last_alive"]


def test_beat_is_atomic_no_tmp_left_behind():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        heartbeat.beat(root)
        assert not list(root.glob("state/*.tmp"))


def test_check_ok_stale_and_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert heartbeat.check(root)["status"] == "missing"

        heartbeat.beat(root)
        assert heartbeat.check(root, max_age=900)["status"] == "ok"

        # Pretend the last beat was an hour ago.
        now = time.time()
        hb = heartbeat.read(root)
        hb["last_alive"] = now - 3600
        (root / "state/heartbeat.json").write_text(
            __import__("json").dumps(hb), encoding="utf-8"
        )
        assert heartbeat.check(root, max_age=900, now=now)["status"] == "stale"


def test_check_marks_dead_when_pid_is_gone():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        heartbeat.beat(root)
        hb = heartbeat.read(root)
        hb["pid"] = 999999  # no such process
        import json

        (root / "state/heartbeat.json").write_text(json.dumps(hb), encoding="utf-8")
        result = heartbeat.check(root, max_age=900)
        assert result["status"] == "dead"
        assert result["pid_alive"] is False


def test_pid_alive_for_the_current_process():
    import os

    assert heartbeat.pid_alive(os.getpid()) is True
    assert heartbeat.pid_alive(0) is False
    assert heartbeat.pid_alive(None) is False


def test_gap_notice_only_when_previous_run_is_old():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # No file: a first start, not an outage.
        assert heartbeat.gap_notice(root) is None

        heartbeat.beat(root)
        assert heartbeat.gap_notice(root, max_age=900) is None

        now = time.time()
        hb = heartbeat.read(root)
        hb["last_alive"] = now - 7200
        import json

        (root / "state/heartbeat.json").write_text(json.dumps(hb), encoding="utf-8")
        notice = heartbeat.gap_notice(root, max_age=900, now=now)
        assert notice and "2.0h" in notice


def test_status_line_never_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert "missing" in heartbeat.status_line(root)
        heartbeat.beat(root)
        assert "alive" in heartbeat.status_line(root)


def test_daemon_check_heartbeat_gap_notifies_once():
    """The daemon reports a gap before its first beat overwrites the evidence."""
    import json

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_hb = {"pid": 4242, "last_alive": time.time() - 7200, "beats": 3}
        (root / "state").mkdir()
        (root / "state" / "heartbeat.json").write_text(
            json.dumps(old_hb), encoding="utf-8"
        )

        originals = {
            "MEMORY_ROOT": daemon_mod.MEMORY_ROOT,
            "HEARTBEAT_FILE": daemon_mod.HEARTBEAT_FILE,
            "HEARTBEAT_ENABLED": daemon_mod.HEARTBEAT_ENABLED,
            "HEARTBEAT_MAX_AGE": daemon_mod.HEARTBEAT_MAX_AGE,
            "HEARTBEAT_NOTIFY_ON_GAP": daemon_mod.HEARTBEAT_NOTIFY_ON_GAP,
            "notify_webui": daemon_mod.notify_webui,
        }
        notices = []
        daemon_mod.MEMORY_ROOT = root
        daemon_mod.HEARTBEAT_FILE = "state/heartbeat.json"
        daemon_mod.HEARTBEAT_ENABLED = True
        daemon_mod.HEARTBEAT_MAX_AGE = 900
        daemon_mod.HEARTBEAT_NOTIFY_ON_GAP = True
        daemon_mod.notify_webui = lambda msg: notices.append(msg)
        try:
            gap = daemon_mod.check_heartbeat_gap()
        finally:
            daemon_mod.MEMORY_ROOT = originals["MEMORY_ROOT"]
            daemon_mod.HEARTBEAT_FILE = originals["HEARTBEAT_FILE"]
            daemon_mod.HEARTBEAT_ENABLED = originals["HEARTBEAT_ENABLED"]
            daemon_mod.HEARTBEAT_MAX_AGE = originals["HEARTBEAT_MAX_AGE"]
            daemon_mod.HEARTBEAT_NOTIFY_ON_GAP = originals["HEARTBEAT_NOTIFY_ON_GAP"]
            daemon_mod.notify_webui = originals["notify_webui"]

        assert gap and "空いています" in gap
        assert notices and notices[0] == gap
        # The gap check immediately starts beating, so a second check is quiet.
        assert heartbeat.read(root)["beats"] >= 1


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} heartbeat tests PASSED!")


if __name__ == "__main__":
    run_tests()
