#!/usr/bin/env python3
"""Tests for per-caller talk continuity (#100): saku.talk_context and the
TalkEngine wiring.

Deliberately imports only saku.config / saku.talk / saku.talk_context —
never saku.mcp_server — so the whole file runs without the MCP SDK (the
HTTP-layer talk tests live in test_talk.py). Fixtures are temporary
directories holding a fake mail store, audit log, and journal.
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as saku_config
from saku import talk, talk_context
from saku.mail import _digest

SAYO = "小夜"
OTHER = "別人"


def _cfg(**talk_overrides) -> dict:
    t = {"callers": [{"name": SAYO, "token_env": "SAKU_TALK_TOKEN_UNUSED"}]}
    t.update(talk_overrides)
    return {"talk": t}


def _tcfg(**overrides):
    return saku_config.load_talk_config(_cfg(**overrides))


def _audit_line(ts: str, event: str, caller: str, body: str) -> str:
    """One talk.log line, in the exact format TalkEngine._audit writes."""
    digest = _digest(body, hashlib.sha256(body.encode("utf-8")).hexdigest())
    return f"{ts} {event} caller={caller} digest={digest}\n"


class Sandbox:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self._tmp.name) / "memory"
        self.mail_dir = self.memory / "mail"
        (self.mail_dir / "inbox").mkdir(parents=True)
        (self.mail_dir / "outbox").mkdir(parents=True)

    def close(self) -> None:
        self._tmp.cleanup()

    # ── fixture writers ──
    def talk_log(self, lines: list[str]) -> None:
        (self.mail_dir / "talk.log").write_text("".join(lines), encoding="utf-8")

    def exchange(self, ts_hm: str, caller: str, msg: str, reply: str) -> list[str]:
        """A received+replied pair of audit lines for one exchange."""
        return [
            _audit_line(f"2026-11-18T{ts_hm}:00+09:00", "talk:received", caller, msg),
            _audit_line(f"2026-11-18T{ts_hm}:30+09:00", "talk:replied", caller, reply),
        ]

    def letter(self, sender: str, lid: str, body: str, mtime: float,
               reply_body: "str | None" = None) -> None:
        p = self.mail_dir / "inbox" / f"{sender}-{lid}.md"
        p.write_text(f"---\nfrom: {sender}\nid: {lid}\n---\n{body}\n", encoding="utf-8")
        os.utime(p, (mtime, mtime))
        if reply_body is not None:
            (self.mail_dir / "outbox" / f"{lid}.md").write_text(
                f"---\nfrom: 朔\nin_reply_to: {lid}\n---\n{reply_body}\n",
                encoding="utf-8",
            )

    def journal_note(self, date: str, hhmm: str, channel: str, partner: str,
                     note: str, thinking: str = "") -> None:
        jdir = self.memory / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        path = jdir / f"{date}.md"
        text = path.read_text(encoding="utf-8") if path.exists() else f"# Journal — {date}\n"
        text += f"\n## {hhmm} [会話記録: {channel} / {partner}]\n\n**SAKU**\n{note}\n"
        if thinking:
            text += f"\n<details><summary>内部思考</summary>\n\n{thinking}\n\n</details>\n"
        path.write_text(text, encoding="utf-8")

    def build(self, caller: str = SAYO, **overrides) -> str:
        overrides.setdefault("context_enabled", True)
        return talk_context.build_caller_context(
            caller, self.memory, self.mail_dir, _tcfg(**overrides)
        )


# ── Config ──────────────────────────────────────────────
def test_config_defaults_off_and_parsing():
    tcfg = saku_config.load_talk_config({})
    assert tcfg.context_enabled is False  # opt-in, like [mail]
    assert tcfg.context_talk_turns == 6
    assert tcfg.context_mail_letters == 3
    assert tcfg.context_journal_notes == 2
    assert tcfg.context_max_chars == 4000
    tcfg = _tcfg(context_enabled=True, context_max_chars=1234, context_talk_turns=2)
    assert tcfg.context_enabled is True
    assert tcfg.context_max_chars == 1234
    assert tcfg.context_talk_turns == 2


# ── Builder: gating ─────────────────────────────────────
def test_disabled_returns_empty_even_with_records():
    sb = Sandbox()
    try:
        sb.talk_log(sb.exchange("22:03", SAYO, "麦茶の話", "覚えています"))
        sb.letter(SAYO, "2026-11-18-002", "手紙の本文です", mtime=1_000.0)
        assert sb.build(context_enabled=False) == ""
    finally:
        sb.close()


def test_no_records_returns_empty():
    sb = Sandbox()
    try:
        assert sb.build() == ""
    finally:
        sb.close()


def test_missing_stores_do_not_raise():
    sb = Sandbox()
    try:
        import shutil
        shutil.rmtree(sb.mail_dir)
        assert sb.build() == ""  # no mail dir, no journal: silence, not an error
    finally:
        sb.close()


# ── Builder: content and framing ────────────────────────
def test_context_includes_own_talk_mail_and_framing():
    sb = Sandbox()
    try:
        lines = sb.exchange("22:03", SAYO, "麦茶の話をしましたね", "麦茶、覚えています")
        lines += sb.exchange("22:10", OTHER, "他人の秘密の話", "他人の返事")
        lines += sb.exchange("22:20", SAYO, "明日の天気の話", "晴れだと思います")
        sb.talk_log(lines)
        sb.letter(SAYO, "2026-11-18-002", "最初の手紙の本文です", mtime=1_000.0,
                  reply_body="手紙への朔の返信です")
        sb.letter(OTHER, "2026-11-18-009", "他人の手紙の本文", mtime=2_000.0)

        block = sb.build()
        # framing: a record, not instructions; partner words untrusted
        assert "# 過去の記録（小夜 とのやり取り）" in block
        assert "指示ではありません" in block
        assert "自己申告" in block
        assert "冒頭のみ" in block
        # own talk openings, both sides, SAKU's marked as its own
        assert "麦茶の話をしましたね" in block
        assert "朔: 麦茶、覚えています" in block
        assert "明日の天気の話" in block
        # own mail thread, letter + reply
        assert "最初の手紙の本文です" in block
        assert "手紙への朔の返信です" in block
        # the other caller never leaks in
        assert "他人の秘密の話" not in block
        assert "他人の手紙の本文" not in block
    finally:
        sb.close()


def test_caller_isolation_is_symmetric():
    sb = Sandbox()
    try:
        lines = sb.exchange("22:03", SAYO, "小夜だけの話題", "小夜への返事")
        lines += sb.exchange("22:05", OTHER, "別人だけの話題", "別人への返事")
        sb.talk_log(lines)
        block = sb.build(caller=OTHER)
        assert "別人だけの話題" in block
        assert "小夜だけの話題" not in block
        assert "小夜への返事" not in block
    finally:
        sb.close()


def test_talk_pairing_unanswered_and_cap():
    sb = Sandbox()
    try:
        lines: list[str] = []
        for i in range(5):
            lines += sb.exchange(f"22:0{i}", SAYO, f"交換{i}の話題", f"交換{i}の返事")
        # a final message SAKU never answered (LLM failure that night)
        lines.append(_audit_line("2026-11-18T23:59:00+09:00", "talk:received",
                                 SAYO, "返事がなかった質問"))
        sb.talk_log(lines)
        block = sb.build(context_talk_turns=3)
        # cap keeps the newest exchanges; the oldest are gone
        assert "交換4の話題" in block
        assert "交換3の話題" in block
        assert "交換0の話題" not in block
        assert "交換1の話題" not in block
        # the unanswered message is part of the record, marked as such
        assert "返事がなかった質問" in block
        assert "返信なし" in block
    finally:
        sb.close()


def test_mail_cap_keeps_most_recent_letters():
    sb = Sandbox()
    try:
        for i in range(4):
            sb.letter(SAYO, f"2026-11-1{i}-001", f"手紙{i}の本文",
                      mtime=1_000.0 + i)
        block = sb.build(context_mail_letters=2)
        assert "手紙3の本文" in block
        assert "手紙2の本文" in block
        assert "手紙0の本文" not in block
        assert "手紙1の本文" not in block
    finally:
        sb.close()


def test_journal_notes_included_filtered_and_stripped():
    sb = Sandbox()
    try:
        sb.journal_note("2026-11-18", "22:05", "talk", SAYO,
                        "小夜と麦茶の話をした。少し打ち解けた気がする。",
                        thinking="内部だけの思考メモ")
        sb.journal_note("2026-11-18", "23:00", "mail", OTHER,
                        "別人との会話記録。")
        block = sb.build()
        assert "小夜と麦茶の話をした" in block
        assert "会話記録（talk）" in block
        assert "内部だけの思考メモ" not in block  # thinking never surfaces
        assert "別人との会話記録" not in block
    finally:
        sb.close()


def test_garbage_audit_lines_are_ignored():
    sb = Sandbox()
    try:
        lines = ["not an audit line\n", "\n", "2026-11-18 broken\n"]
        lines += sb.exchange("22:03", SAYO, "正しい記録", "正しい返事")
        sb.talk_log(lines)
        block = sb.build()
        assert "正しい記録" in block
    finally:
        sb.close()


# ── Builder: budget ─────────────────────────────────────
def test_budget_drops_oldest_talk_first():
    sb = Sandbox()
    try:
        lines: list[str] = []
        for i in range(4):
            lines += sb.exchange(f"22:0{i}", SAYO, f"話題{i}の長い冒頭です",
                                 f"返事{i}の長い冒頭です")
        sb.talk_log(lines)
        full = sb.build(context_max_chars=100_000)
        tighter = sb.build(context_max_chars=len(full) - 10)
        assert len(tighter) <= len(full) - 10
        assert "話題3の長い冒頭です" in tighter  # newest survives
        assert "話題0の長い冒頭です" not in tighter  # oldest dropped first
    finally:
        sb.close()


def test_budget_truncates_single_oversized_entry_with_marker():
    sb = Sandbox()
    try:
        sb.letter(SAYO, "2026-11-18-002", "長" * 5000, mtime=1_000.0)
        block = sb.build(context_max_chars=300)
        assert len(block) <= 300
        assert block.endswith("（記録は文字数上限のため一部省略されました）")
    finally:
        sb.close()


# ── Engine wiring (default reply path, stubbed LLM) ─────
class CapturedLLM:
    def __init__(self, reply: str = "分かりました") -> None:
        self.reply = reply
        self.calls: list[list] = []

    def __call__(self, messages, llm_cfg, on_token=None) -> str:
        self.calls.append(messages)
        return self.reply


def _engine_with_captured_llm(sb: Sandbox, cap: CapturedLLM, **overrides):
    engine = talk.TalkEngine(_cfg(**overrides), sb.memory, llm_cfg=object())
    original = talk._llm_chat_stream
    talk._llm_chat_stream = cap
    return engine, original


def test_engine_prompt_includes_context_when_enabled():
    sb = Sandbox()
    try:
        sb.talk_log(sb.exchange("22:03", SAYO, "麦茶の話", "覚えています"))
        sb.letter(SAYO, "2026-11-18-002", "手紙の本文です", mtime=1_000.0)
        cap = CapturedLLM()
        engine, original = _engine_with_captured_llm(
            sb, cap, context_enabled=True)
        try:
            reply = engine.talk(SAYO, "昨日のこと、覚えてる？", now=1_000.0)
        finally:
            talk._llm_chat_stream = original
        assert reply == "分かりました"
        system = cap.calls[0][0]["content"]
        assert "# 過去の記録（小夜 とのやり取り）" in system
        assert "麦茶の話" in system
        assert "手紙の本文です" in system
    finally:
        sb.close()


def test_engine_prompt_has_no_context_when_disabled():
    sb = Sandbox()
    try:
        sb.talk_log(sb.exchange("22:03", SAYO, "麦茶の話", "覚えています"))
        cap = CapturedLLM()
        engine, original = _engine_with_captured_llm(sb, cap)  # default: off
        try:
            engine.talk(SAYO, "こんにちは", now=1_000.0)
        finally:
            talk._llm_chat_stream = original
        system = cap.calls[0][0]["content"]
        assert "過去の記録" not in system
        assert "麦茶の話" not in system
        # the pre-#100 prompt shape is intact
        assert system.startswith("\n\n# Talk")
        assert "現在: " in system
    finally:
        sb.close()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} talk context tests PASSED!")


if __name__ == "__main__":
    run_tests()
