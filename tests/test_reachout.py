#!/usr/bin/env python3
"""Tests for generating a reason to speak and delivering it (#120)."""

import json
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import identity, outbox, reachout, speech


def _cfg(tmp: Path, **reachout_cfg) -> dict:
    sayo = tmp / "sayo.token"
    sayo.write_text("sayo-secret", encoding="utf-8")
    cfg = {
        "identity": {"agents": {"sayo": {"token_file": str(sayo), "scopes": ["talk", "mail"]}}},
        "presence": {"enabled": False},
        "reachout": {"enabled": True, "recipients": ["owner"], "min_interval_hours": 1},
        "speech": {"max_per_hour": 10},
    }
    cfg["reachout"].update(reachout_cfg)
    return cfg


def _chat(text):
    calls = []

    def _fn(history, llm):
        calls.append(history[-1]["content"])
        return text

    return _fn, calls


def test_disabled_reachout_does_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root, enabled=False)
        assert reachout.run(cfg=cfg, memory_root=root) == []
        assert outbox.items(root) == []


def test_a_reason_is_delivered_and_the_interval_blocks_the_next_one():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        chat, calls = _chat("少し相談したいことがあるんだ。")
        now = 1_000_000.0
        results = reachout.run(
            cfg=cfg, memory_root=root, now=now, chat_fn=chat
        )
        assert results and results[0]["ok"], results
        entries = outbox.items(root)
        assert [e.text for e in entries] == ["少し相談したいことがあるんだ。"]
        assert calls, "the model was asked for a reason"

        # The interval is per recipient and persisted.
        again = reachout.run(cfg=cfg, memory_root=root, now=now + 60, chat_fn=chat)
        assert again and again[0]["detail"] == "not due"
        assert len(outbox.items(root)) == 1


def test_no_reason_means_silence_and_no_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        chat, _ = _chat("[NO_REASON]")
        results = reachout.run(cfg=cfg, memory_root=root, now=1_000_000.0, chat_fn=chat)
        assert results and not results[0]["ok"]
        assert results[0]["detail"] == "no reason"
        assert outbox.items(root) == []
        state = json.loads((root / "state" / "reachout.json").read_text(encoding="utf-8")) \
            if (root / "state" / "reachout.json").exists() else {"recipients": {}}
        assert state["recipients"] == {}


def test_dry_run_shows_the_message_without_delivering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        chat, _ = _chat("これは本番では送られない")
        results = reachout.run(
            cfg=cfg, memory_root=root, now=1_000_000.0, dry_run=True, chat_fn=chat
        )
        assert results[0]["dry_run"] is True
        assert outbox.items(root) == []
        assert not (root / "state" / "reachout.json").exists()


def test_presence_keeps_the_owner_path_quiet():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        cfg["presence"] = {"enabled": True, "sleep_start": "00:00", "sleep_end": "23:59"}
        chat, calls = _chat("話しかけない")
        results = reachout.run(cfg=cfg, memory_root=root, now=1_000_000.0, chat_fn=chat)
        assert not results[0]["ok"]
        assert "presence=sleeping" in results[0]["detail"]
        assert calls == [], "the model is not even asked while the Owner sleeps"


def test_agent_recipient_receives_a_letter():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root, recipients=["sayo"])
        chat, _ = _chat("小夜、例の件だけど。")
        results = reachout.run(cfg=cfg, memory_root=root, now=1_000_000.0, chat_fn=chat)
        assert results[0]["ok"] and results[0]["channel"] == "mail"
        letters = list((root / "mail" / "outbox").glob("*.md"))
        assert len(letters) == 1
        assert "to: sayo" in letters[0].read_text(encoding="utf-8")
        record = identity.tail(root, 1)[0]
        assert record["action"] == "speech.send" and record["allowed"] is True


def test_kill_switch_stops_reachout():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        (root / "state").mkdir(parents=True)
        (root / "state" / "STOP").write_text("stop", encoding="utf-8")
        chat, calls = _chat("送られない")
        results = reachout.run(cfg=cfg, memory_root=root, now=1_000_000.0, chat_fn=chat)
        assert not results[0]["ok"] and "kill switch" in results[0]["detail"]
        assert calls == []
        assert outbox.items(root) == []


def test_unconfigured_target_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        chat, calls = _chat("x")
        results = reachout.run(
            cfg=cfg, memory_root=root, to="sayo", now=1_000_000.0, chat_fn=chat
        )
        assert not results[0]["ok"] and results[0]["detail"] == "not configured"
        assert calls == []


def test_compose_strips_bookkeeping_and_caps_length():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp), max_chars=10)
        chat, _ = _chat("[REASON]\nこれは長すぎるメッセージです")
        assert reachout.compose("owner", "ctx", cfg=cfg, chat_fn=chat) is None
        chat, _ = _chat("[REASON]\n短い")
        assert reachout.compose("owner", "ctx", cfg=cfg, chat_fn=chat) == "短い"


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} reachout tests PASSED!")


if __name__ == "__main__":
    run_tests()
