#!/usr/bin/env python3
"""Tests for the identity/permission layer and audit log (#73)."""

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

from saku import identity


def _cfg(tmp: Path) -> dict:
    token_file = tmp / "sayo.token"
    token_file.write_text("agent-secret", encoding="utf-8")
    os.environ["SAKU_TEST_AGENT_TOKEN"] = "env-secret"
    return {
        "identity": {
            "agents": {
                "envagent": {
                    "token_env": "SAKU_TEST_AGENT_TOKEN",
                    "scopes": ["talk", "read", "request"],
                }
            }
        },
        "talk": {"callers": [{"name": "sayo", "token_file": str(token_file)}]},
    }


def test_owner_holds_every_scope():
    for scope in identity.GRANTABLE_SCOPES:
        assert identity.OWNER.can(scope)
    assert identity.OWNER.is_owner
    assert not identity.UNKNOWN.can(identity.SCOPE_READ)


def test_talk_callers_become_talk_scoped_agents():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp))
        specs = identity.agent_specs(cfg)
        assert specs["sayo"]["scopes"] == ["talk"]
        principal = identity.principal_for_agent(cfg, "sayo")
        assert principal.kind == identity.KIND_AGENT
        assert principal.can(identity.SCOPE_TALK)
        assert not principal.can(identity.SCOPE_MAIL)
        assert not principal.can(identity.SCOPE_REQUEST)


def test_identity_entry_overrides_a_talk_caller():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp))
        cfg["identity"]["agents"]["sayo"] = {"scopes": ["talk", "mail"]}
        principal = identity.principal_for_agent(cfg, "sayo")
        assert principal.can(identity.SCOPE_MAIL)
        assert not principal.can(identity.SCOPE_APPROVE)


def test_unknown_scopes_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp))
        cfg["identity"]["agents"]["envagent"]["scopes"] = ["talk", "root", "approve"]
        principal = identity.principal_for_agent(cfg, "envagent")
        assert principal.can(identity.SCOPE_TALK)
        assert "root" not in principal.scopes
        assert principal.can(identity.SCOPE_APPROVE), "explicitly granted is honoured"


def test_resolve_token_from_env_and_file():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp))
        env_principal = identity.resolve_token("env-secret", cfg)
        assert env_principal and env_principal.name == "envagent"
        file_principal = identity.resolve_token("agent-secret", cfg)
        assert file_principal and file_principal.name == "sayo"
        assert identity.resolve_token("wrong", cfg) is None
        assert identity.resolve_token("", cfg) is None
        try:
            del os.environ["SAKU_TEST_AGENT_TOKEN"]
        except KeyError:
            pass


def test_reserved_operator_token_is_not_an_agent():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(Path(tmp))
        assert identity.resolve_token("agent-secret", cfg, reserved=("agent-secret",)) is None


def test_audit_appends_and_tails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        previous = os.environ.pop("SAKU_AUDIT_FILE", None)
        try:
            identity.audit(root, identity.OWNER, "chat.turn", channel="webui", detail="len=3")
            identity.audit(
                root, identity.UNKNOWN, "tool", channel="agent", allowed=False,
                detail="[DENY] x",
            )
            records = identity.tail(root, 10)
            assert len(records) == 2
            assert records[0]["principal"] == "owner" and records[0]["allowed"] is True
            assert records[1]["allowed"] is False
            # Append-only JSONL.
            lines = (root / "state" / "audit.log").read_text(encoding="utf-8").splitlines()
            assert len(lines) == 2 and json.loads(lines[0])["action"] == "chat.turn"
        finally:
            if previous is not None:
                os.environ["SAKU_AUDIT_FILE"] = previous


def test_audit_failure_never_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        (root / "state" / "audit.log").mkdir()  # a directory where a file belongs
        identity.audit(root, identity.OWNER, "chat.turn")  # must not raise


def test_tool_execution_is_audited_with_the_acting_principal():
    import saku.transport as transport

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )
        with identity.as_principal(identity.SYSTEM):
            transport.exec_tools("[[ECHO]]\nhi\n[[END]]", root, root)
        records = identity.tail(root, 10)
        assert records and records[-1]["action"] == "tool"
        assert records[-1]["principal"] == "saku"
        assert records[-1]["allowed"] is True

        with identity.as_principal(identity.UNKNOWN):
            transport.exec_tools(
                '[[WRITE_FILE path="meta.md"]]\nx\n[[END]]', root, root
            )
        records = identity.tail(root, 10)
        assert records[-1]["allowed"] is False, records[-1]


def test_talk_caller_can_lose_the_talk_scope():
    from saku import talk

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        token_file = root / "sayo.token"
        token_file.write_text("agent-secret", encoding="utf-8")
        cfg = {
            "talk": {"callers": [{"name": "sayo", "token_file": str(token_file)}]},
            "identity": {"agents": {"sayo": {"scopes": ["read"]}}},
        }
        engine = talk.TalkEngine(cfg, root, reply_fn=lambda *a, **k: "hi")
        try:
            engine.talk("sayo", "こんにちは")
        except talk.TalkRejected as e:
            assert "not permitted" in str(e)
        else:
            raise AssertionError("a caller without the talk scope was allowed to talk")


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} identity tests PASSED!")


if __name__ == "__main__":
    run_tests()
