#!/usr/bin/env python3
"""Tests for delivering SAKU's words to the Owner or an agent (#120 stage 2)."""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import identity, mail, outbox, speech


def _cfg(tmp: Path, **speech_cfg) -> dict:
    sayo = tmp / "sayo.token"
    sayo.write_text("sayo-secret", encoding="utf-8")
    talker = tmp / "talker.token"
    talker.write_text("talk-secret", encoding="utf-8")
    cfg = {
        "identity": {
            "agents": {
                "sayo": {"token_file": str(sayo), "scopes": ["talk", "mail", "read"]},
            }
        },
        "talk": {
            "callers": [{"name": "talker", "token_file": str(talker)}],
        },
        "speech": {"max_chars": 100, "max_per_hour": 5},
    }
    cfg["speech"].update(speech_cfg)
    return cfg


def test_owner_delivery_uses_the_outbox():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        result = speech.deliver("こんばんは", memory_root=root, cfg=cfg)
        assert result["ok"] and result["channel"] == "webui"
        entries = outbox.items(root)
        assert [(e.kind, e.source, e.text) for e in entries] == [
            (outbox.KIND_SPONTANEOUS, "speech", "こんばんは")
        ]
        record = identity.tail(root, 1)[0]
        assert record["action"] == "speech.send" and record["allowed"] is True


def test_agent_delivery_writes_a_new_thread_letter():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        result = speech.deliver("小夜、少し話せる？", to="sayo", memory_root=root, cfg=cfg)
        assert result["ok"] and result["channel"] == "mail"

        path = Path(result["path"])
        assert path.is_file() and path.parent == root / "mail" / "outbox"
        letter = mail.parse_letter(path.read_text(encoding="utf-8"))
        assert letter["from"] == speech.FROM_NAME
        assert letter["body"] == "小夜、少し話せる？"
        assert "to: sayo" in path.read_text(encoding="utf-8")
        assert "in_reply_to" not in path.read_text(encoding="utf-8")

        record = identity.tail(root, 1)[0]
        assert record["channel"] == "mail" and record["allowed"] is True


def test_unknown_recipient_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        result = speech.deliver("hi", to="stranger", memory_root=root, cfg=cfg)
        assert not result["ok"]
        assert "unknown recipient" in result["detail"]
        assert not (root / "mail" / "outbox").exists()
        record = identity.tail(root, 1)[0]
        assert record["allowed"] is False


def test_agent_without_mail_scope_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        # A plain talk caller only has the talk scope (#73).
        result = speech.deliver("hi", to="talker", memory_root=root, cfg=cfg)
        assert not result["ok"]
        assert "no mail scope" in result["detail"]
        assert not (root / "mail" / "outbox").exists()


def test_kill_switch_silences_speech():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root)
        (root / "state").mkdir(parents=True)
        (root / "state" / "STOP").write_text("stop", encoding="utf-8")
        result = speech.deliver("hi", memory_root=root, cfg=cfg)
        assert not result["ok"]
        assert "kill switch" in result["detail"]
        assert outbox.items(root) == []


def test_length_and_hourly_caps_apply_per_recipient():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root, max_chars=10, max_per_hour=2)

        assert not speech.deliver("x" * 11, memory_root=root, cfg=cfg)["ok"]
        assert speech.deliver("one", memory_root=root, cfg=cfg)["ok"]
        assert speech.deliver("two", memory_root=root, cfg=cfg)["ok"]
        blocked = speech.deliver("three", memory_root=root, cfg=cfg)
        assert not blocked["ok"] and "hourly limit" in blocked["detail"]
        # The cap is per recipient, so an agent is still reachable.
        assert speech.deliver("小夜へ", to="sayo", memory_root=root, cfg=cfg)["ok"]


def test_letters_do_not_overwrite_each_other():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = _cfg(root, max_per_hour=10)
        first = speech.deliver("1", to="sayo", memory_root=root, cfg=cfg)
        second = speech.deliver("2", to="sayo", memory_root=root, cfg=cfg)
        assert first["path"] != second["path"]
        assert len(list((root / "mail" / "outbox").glob("*.md"))) == 2


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} speech tests PASSED!")


if __name__ == "__main__":
    run_tests()
