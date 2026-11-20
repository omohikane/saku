#!/usr/bin/env python3
"""Tests for the channel-independent conversation thread (#120 follow-up)."""

import json
import os
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import conversation


def _tmp_file() -> Path:
    d = Path(tempfile.mkdtemp(prefix="saku-conv-test-"))
    return d / "conversation.jsonl"


def test_append_and_recent_across_channels():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(_tmp_file())
        try:
            conversation.append(root, channel="webui", principal="owner",
                                 role="user", text="こんにちは", now=1_000.0)
            conversation.append(root, channel="webui", principal="owner",
                                 role="assistant", text="やあ", now=1_001.0)
            conversation.append(root, channel="chat.md", principal="owner",
                                 role="user", text="別の場所から", now=1_002.0)
            entries = conversation.recent(root, limit=10, principal="owner")
            assert [e["text"] for e in entries] == ["こんにちは", "やあ", "別の場所から"]

            only_web = conversation.recent(root, limit=10, channels=["webui"])
            assert len(only_web) == 2
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_history_messages_are_seeded_across_channels():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(_tmp_file())
        try:
            conversation.append(root, channel="chat.md", principal="owner",
                                 role="user", text="昨日の続き", now=1_000.0)
            conversation.append(root, channel="chat.md", principal="owner",
                                 role="assistant", text="覚えてる", now=1_001.0)
            messages = conversation.history_messages(root, principal="owner", limit=10)
            assert messages == [
                {"role": "user", "content": "昨日の続き"},
                {"role": "assistant", "content": "覚えてる"},
            ]
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_seeded_history_starts_with_the_user():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(_tmp_file())
        try:
            conversation.append(root, channel="webui", principal="owner",
                                 role="assistant", text="orphan", now=1_000.0)
            conversation.append(root, channel="webui", principal="owner",
                                 role="user", text="次の発言", now=1_001.0)
            messages = conversation.history_messages(root, principal="owner", limit=10)
            assert messages[0]["role"] == "user"
            assert [m["content"] for m in messages] == ["次の発言"]
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_other_principals_are_not_injected_into_the_owner_thread():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(_tmp_file())
        try:
            conversation.append(root, channel="mail", principal="小夜",
                                 role="user", text="手紙です", now=1_000.0)
            assert conversation.history_messages(root, principal="owner") == []
            assert [m["content"] for m in conversation.history_messages(root, principal="小夜")] == [
                "手紙です"
            ]
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_tool_results_and_empty_turns_are_not_stored():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(_tmp_file())
        try:
            assert not conversation.append(root, channel="webui", principal="owner",
                                           role="system", text="sys")
            assert not conversation.append(root, channel="webui", principal="owner",
                                           role="user", text="  ")
            assert conversation.recent(root) == []
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_the_log_is_capped():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _tmp_file()
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(path)
        cfg = {"conversation": {"max_entries": 3}}
        try:
            for i in range(8):
                conversation.append(root, channel="webui", principal="owner",
                                    role="user", text=f"m{i}", cfg=cfg, now=1_000.0 + i)
            entries = conversation.recent(root, limit=10, cfg=cfg)
            assert [e["text"] for e in entries] == ["m5", "m6", "m7"]
            assert len(path.read_text(encoding="utf-8").splitlines()) == 3
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def test_disabled_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _tmp_file()
        old = os.environ.pop("SAKU_CONVERSATION_FILE", None)
        os.environ["SAKU_CONVERSATION_FILE"] = str(path)
        try:
            assert not conversation.append(
                root, channel="webui", principal="owner", role="user", text="x",
                cfg={"conversation": {"enabled": False}},
            )
            assert conversation.recent(root) == []
        finally:
            os.environ.pop("SAKU_CONVERSATION_FILE", None)
            if old is not None:
                os.environ["SAKU_CONVERSATION_FILE"] = old


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} conversation tests PASSED!")


if __name__ == "__main__":
    run_tests()
