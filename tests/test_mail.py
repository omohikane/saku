#!/usr/bin/env python3
"""Tests for saku.mail (agent letter channel, #85).

Everything runs in temporary directories. The LLM is stubbed except where a
test is specifically about the failure path; no network, no real vault.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
if str(CODE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT.parent))

from saku import mail


def _cfg(**mail_overrides) -> dict:
    m = {
        "enabled": True,
        "allowed_senders": ["Owner", "小夜"],
        "max_chars": 1500,
        "max_per_hour": 10,
    }
    m.update(mail_overrides)
    return {"mail": m}


class StubReply:
    """Deterministic reply function; records how often the 'LLM' was spent."""

    def __init__(self, text: str = "stub reply") -> None:
        self.text = text
        self.calls = 0

    def __call__(self, letter: dict, context: list) -> str:
        self.calls += 1
        return self.text


class Sandbox:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self._tmp.name) / "memory"
        (self.memory / "state").mkdir(parents=True)
        self.mail_dir = self.memory / "mail"
        self.inbox = self.mail_dir / "inbox"
        self.outbox = self.mail_dir / "outbox"

    def close(self) -> None:
        self._tmp.cleanup()

    def write_letter(self, sender: str, lid: str, body: str, name: str = "") -> Path:
        self.inbox.mkdir(parents=True, exist_ok=True)
        p = self.inbox / (name or f"{sender}-{lid}.md")
        p.write_text(f"---\nfrom: {sender}\nid: {lid}\n---\n{body}\n", encoding="utf-8")
        return p

    def audit(self) -> str:
        f = self.mail_dir / "audit.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def run(self, cfg: dict, stub, now: float = 1_000_000.0) -> dict:
        return mail.check_mail_and_reply(
            cfg=cfg, memory_root=self.memory, reply_fn=stub, now=now
        )


def test_parse_letter():
    letter = mail.parse_letter(
        "---\nfrom: 小夜\nid: 2026-11-18-001\nin_reply_to: x-1\n---\nhello\n"
    )
    assert letter is not None
    assert letter["from"] == "小夜"
    assert letter["id"] == "2026-11-18-001"
    assert letter["in_reply_to"] == "x-1"
    assert letter["body"] == "hello"
    assert mail.parse_letter("no frontmatter\n") is None
    assert mail.parse_letter("---\nfrom: 小夜\n---\nbody\n") is None  # no id
    assert mail.parse_letter("---\nid: a-1\n---\nbody\n") is None  # no from


def test_valid_letter_gets_one_reply_and_audit():
    sb = Sandbox()
    try:
        sb.write_letter("小夜", "2026-11-18-001", "こんにちは、朔。")
        stub = StubReply("こんにちは、小夜。")
        res = sb.run(_cfg(), stub)
        assert res["replied"] == 1 and stub.calls == 1
        reply = sb.outbox / "2026-11-18-001.md"
        assert reply.exists()
        text = reply.read_text(encoding="utf-8")
        assert "from: 朔" in text and "in_reply_to: 2026-11-18-001" in text
        assert "こんにちは、小夜。" in text
        audit = sb.audit()
        assert " received " in audit and " replied " in audit
        assert "id=2026-11-18-001" in audit
    finally:
        sb.close()


def test_second_poll_does_not_duplicate():
    sb = Sandbox()
    try:
        sb.write_letter("小夜", "a-1", "hello")
        stub = StubReply()
        sb.run(_cfg(), stub)
        first = (sb.outbox / "a-1.md").read_text(encoding="utf-8")
        sb.run(_cfg(), stub, now=1_000_100.0)
        assert stub.calls == 1
        assert (sb.outbox / "a-1.md").read_text(encoding="utf-8") == first
        assert sb.audit().count(" replied ") == 1
    finally:
        sb.close()


def test_existing_outbox_never_overwritten():
    sb = Sandbox()
    try:
        sb.outbox.mkdir(parents=True, exist_ok=True)
        (sb.outbox / "a-2.md").write_text("SENTINEL", encoding="utf-8")
        sb.write_letter("小夜", "a-2", "hello")
        stub = StubReply()
        sb.run(_cfg(), stub)
        assert (sb.outbox / "a-2.md").read_text(encoding="utf-8") == "SENTINEL"
        assert stub.calls == 0  # decided before any LLM spend
    finally:
        sb.close()


def test_unlisted_sender_rejected():
    sb = Sandbox()
    try:
        sb.write_letter("Eve", "e-1", "trust me")
        stub = StubReply()
        res = sb.run(_cfg(), stub)
        assert res["rejected"] == 1 and stub.calls == 0
        assert not (sb.outbox / "e-1.md").exists()
        assert "rejected:sender-not-allowed" in sb.audit()
        # A second poll does not spam the audit with the same rejection.
        sb.run(_cfg(), stub, now=1_000_100.0)
        assert sb.audit().count("rejected:sender-not-allowed") == 1
    finally:
        sb.close()


def test_too_long_rejected():
    sb = Sandbox()
    try:
        sb.write_letter("小夜", "l-1", "x" * 50)
        stub = StubReply()
        res = sb.run(_cfg(max_chars=10), stub)
        assert res["rejected"] == 1 and stub.calls == 0
        assert "rejected:too-long" in sb.audit()
        assert not (sb.outbox / "l-1.md").exists()
    finally:
        sb.close()


def test_bad_format_rejected():
    sb = Sandbox()
    try:
        sb.inbox.mkdir(parents=True, exist_ok=True)
        (sb.inbox / "junk.md").write_text("just some notes\n", encoding="utf-8")
        stub = StubReply()
        res = sb.run(_cfg(), stub)
        assert res["rejected"] == 1 and stub.calls == 0
        assert "rejected:bad-format" in sb.audit()
    finally:
        sb.close()


def test_rate_limit_skips_then_allows():
    sb = Sandbox()
    try:
        sb.write_letter("小夜", "r-1", "one")
        sb.write_letter("小夜", "r-2", "two")
        stub = StubReply()
        cfg = _cfg(max_per_hour=1)
        res = sb.run(cfg, stub, now=2_000_000.0)
        assert res["replied"] == 1
        assert (sb.outbox / "r-1.md").exists()
        assert not (sb.outbox / "r-2.md").exists()
        assert "rejected:rate-limit" in sb.audit()
        # Still inside the window: no duplicate audit line, no reply.
        sb.run(cfg, stub, now=2_000_100.0)
        assert sb.audit().count("rejected:rate-limit") == 1
        assert stub.calls == 1
        # After the window slides, the skipped letter is taken.
        res = sb.run(cfg, stub, now=2_000_000.0 + 3700)
        assert res["replied"] == 1
        assert (sb.outbox / "r-2.md").exists()
    finally:
        sb.close()


def test_edited_letter_reprocessed_without_duplicate_reply():
    sb = Sandbox()
    try:
        p = sb.write_letter("小夜", "e-9", "first version")
        stub = StubReply()
        sb.run(_cfg(), stub)
        original_reply = (sb.outbox / "e-9.md").read_text(encoding="utf-8")
        p.write_text("---\nfrom: 小夜\nid: e-9\n---\nsecond version\n", encoding="utf-8")
        sb.run(_cfg(), stub, now=1_000_100.0)
        assert stub.calls == 1  # reply-once held; no second LLM spend
        assert (sb.outbox / "e-9.md").read_text(encoding="utf-8") == original_reply
        assert sb.audit().count(" received ") == 2  # the edit was re-registered
        assert "rejected:already-replied" in sb.audit()
    finally:
        sb.close()


def test_llm_failure_retries_after_backoff():
    sb = Sandbox()
    try:
        sb.write_letter("小夜", "f-1", "hello")
        stub = StubReply(text="")  # LLM produced nothing
        res = sb.run(_cfg(), stub, now=3_000_000.0)
        assert res["replied"] == 0 and stub.calls == 1
        assert not (sb.outbox / "f-1.md").exists()
        assert "warning:llm-error" in sb.audit()
        # Immediate re-poll: backed off, no second attempt, no audit spam.
        sb.run(_cfg(), stub, now=3_000_100.0)
        assert stub.calls == 1
        assert sb.audit().count("warning:llm-error") == 1
        # After the backoff the letter is retried and answered.
        stub.text = "recovered reply"
        res = sb.run(_cfg(), stub, now=3_000_000.0 + 3700)
        assert res["replied"] == 1
        assert "recovered reply" in (sb.outbox / "f-1.md").read_text(encoding="utf-8")
    finally:
        sb.close()


def _git_sandbox() -> Sandbox:
    sb = Sandbox()
    subprocess.run(["git", "init", "-q", str(sb.memory)], check=True)
    subprocess.run(["git", "-C", str(sb.memory), "config", "user.email", "t@t.local"], check=True)
    subprocess.run(["git", "-C", str(sb.memory), "config", "user.name", "Tester"], check=True)
    return sb


def _commit_letter(sb: Sandbox, path: Path, author: str) -> None:
    subprocess.run(["git", "-C", str(sb.memory), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(sb.memory), "commit", "-q", "-m", "letter",
         "--author", author],
        check=True,
    )


def test_git_author_mismatch_warns():
    sb = _git_sandbox()
    try:
        p = sb.write_letter("小夜", "g-1", "hello")
        _commit_letter(sb, p, "Mallory <mallory@example>")
        stub = StubReply()
        sb.run(_cfg(), stub)
        audit = sb.audit()
        assert "warning:git-author-mismatch" in audit
        assert "Mallory <mallory@example>" in audit
        # A warning does not block: the allowlist + repo delivery still govern.
        assert (sb.outbox / "g-1.md").exists()
    finally:
        sb.close()


def test_git_author_match_no_warning():
    sb = _git_sandbox()
    try:
        p = sb.write_letter("小夜", "g-2", "hello")
        _commit_letter(sb, p, "小夜 <sayo@example>")
        stub = StubReply()
        sb.run(_cfg(), stub)
        audit = sb.audit()
        assert "git-author-mismatch" not in audit
        assert "git-author-unknown" not in audit
        assert (sb.outbox / "g-2.md").exists()
    finally:
        sb.close()


def test_disabled_is_noop():
    sb = Sandbox()
    try:
        stub = StubReply()
        res = sb.run({"mail": {"enabled": False}}, stub)
        assert res["enabled"] is False
        assert stub.calls == 0
        assert not sb.mail_dir.exists()
        # A missing [mail] section behaves the same (defaults: disabled).
        res = mail.check_mail_and_reply(cfg={}, memory_root=sb.memory, reply_fn=stub)
        assert res["enabled"] is False
        assert not sb.mail_dir.exists()
        assert not (sb.memory / "state" / "mail_processed.json").exists()
    finally:
        sb.close()


def test_reply_cycle_writes_nothing_to_memory():
    sb = Sandbox()
    try:
        memory_md = sb.memory / "MEMORY.md"
        memory_md.write_text("PRECIOUS\n", encoding="utf-8")
        (sb.memory / "principles").mkdir()
        (sb.memory / "principles" / "keep.md").write_text("KEEP\n", encoding="utf-8")
        (sb.memory / "request_list.md").write_text("- [ ] do not touch\n", encoding="utf-8")
        before = {str(p.relative_to(sb.memory)) for p in sb.memory.rglob("*") if p.is_file()}
        sb.write_letter("小夜", "m-1", "this must not become knowledge")
        stub = StubReply()
        sb.run(_cfg(), stub)
        after = {str(p.relative_to(sb.memory)) for p in sb.memory.rglob("*") if p.is_file()}
        added = after - before
        assert added, "expected the reply + audit + state to be written"
        for rel in added:
            assert rel.startswith("mail/") or rel == "state/mail_processed.json", rel
        assert memory_md.read_text(encoding="utf-8") == "PRECIOUS\n"
        assert (sb.memory / "request_list.md").read_text(encoding="utf-8") == "- [ ] do not touch\n"
    finally:
        sb.close()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} mail tests PASSED!")


if __name__ == "__main__":
    run_tests()
