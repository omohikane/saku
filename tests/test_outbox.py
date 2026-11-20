#!/usr/bin/env python3
"""Tests for the Owner delivery queue (#120)."""

import json
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import daemon as daemon_mod
from saku import outbox
from saku.config import OutboxConfig
from _isolate_logging import isolate_daemon_logging

isolate_daemon_logging(daemon_mod.logger)


def test_add_and_unread():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entry = outbox.add(root, "こんばんは", kind=outbox.KIND_SPONTANEOUS)
        assert entry and entry.id
        assert outbox.unread_count(root) == 1
        assert outbox.items(root)[0].text == "こんばんは"
        assert outbox.add(root, "   ") is None, "empty messages are not queued"


def test_delivered_and_read_are_distinct():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = outbox.add(root, "a", kind=outbox.KIND_NOTICE)
        outbox.add(root, "b", kind=outbox.KIND_SPONTANEOUS)
        assert outbox.mark_delivered(root, [first.id]) == 1
        entries = {e.text: e for e in outbox.items(root)}
        assert entries["a"].delivered_at > 0
        assert entries["b"].delivered_at == 0
        assert outbox.unread_count(root) == 2, "delivered is not read"

        assert outbox.mark_read(root, [first.id]) == 1
        assert outbox.unread_count(root) == 1


def test_mark_read_by_kind_spares_notices():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outbox.add(root, "speech", kind=outbox.KIND_SPONTANEOUS)
        outbox.add(root, "notice", kind=outbox.KIND_NOTICE)
        assert outbox.mark_read(root, kind=outbox.KIND_SPONTANEOUS) == 1
        remaining = [e.text for e in outbox.items(root) if not e.read_at]
        assert remaining == ["notice"]


def test_retention_and_max_items_prune_old_entries():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = OutboxConfig(max_items=3, retention_days=1)
        now = 1_000_000.0
        for i in range(5):
            outbox.add(root, f"m{i}", cfg=cfg, now=now + i)
        assert [e.text for e in outbox.items(root, cfg=cfg)] == ["m2", "m3", "m4"]

        # A day later, everything is older than the retention window.
        outbox.add(root, "later", cfg=cfg, now=now + 3 * 86400)
        assert [e.text for e in outbox.items(root, cfg=cfg)] == ["later"]


def test_outbox_file_is_written_atomically():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outbox.add(root, "x")
        assert not list((root / "state").glob("*.tmp"))
        payload = json.loads((root / "state" / "outbox.json").read_text(encoding="utf-8"))
        assert payload["version"] == 1 and payload["items"][0]["text"] == "x"


def test_corrupt_outbox_does_not_raise():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "state" / "outbox.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert outbox.items(root) == []
        assert outbox.add(root, "recovered").text == "recovered"


def test_notify_webui_delivers_through_the_outbox():
    """#120: notices and spontaneous speech share one durable path."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original = daemon_mod.MEMORY_ROOT
        daemon_mod.MEMORY_ROOT = root
        try:
            daemon_mod.notify_webui("再起動しました", kind=outbox.KIND_NOTICE, source="heartbeat")
            daemon_mod.notify_webui("少し話そう", kind=outbox.KIND_SPONTANEOUS, source="initiation")
        finally:
            daemon_mod.MEMORY_ROOT = original

        entries = outbox.items(root)
        assert [(e.kind, e.source) for e in entries] == [
            (outbox.KIND_NOTICE, "heartbeat"),
            (outbox.KIND_SPONTANEOUS, "initiation"),
        ]


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} outbox tests PASSED!")


if __name__ == "__main__":
    run_tests()
