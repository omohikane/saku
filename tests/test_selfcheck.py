#!/usr/bin/env python3
"""
Unit tests for the self-verification loop (saku.selfcheck, issue #102).

No LLM: the module compares SAKU's own completion claims against the
filesystem and git. Git tests build a throwaway repository per test.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import reflection, selfcheck

_PASS = 0
_FAIL = 0


def _git_repo(tmp: Path) -> tuple[Path, str]:
    """Create a git repo with one commit; return (repo_root, commit_hash)."""
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp)})
    for args in (["init", "-q"], ["add", "a.txt"],
                 ["-c", "user.email=t@example.com", "-c", "user.name=t",
                  "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                       capture_output=True)
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         check=True, env=env, capture_output=True, text=True)
    return repo, out.stdout.strip()


def test_saku_voice_skips_owner_thinking_and_audit():
    journal = (
        "# Journal — 2026-11-19\n"
        "\n## 09:00\n\n**Owner**\nprinciples/owner.md に記録しました。\n"
        "\n**SAKU**\nprinciples/mine.md に記録しました。\n"
        "\n<details><summary>内部思考</summary>\n\n"
        "principles/thought.md に記録しました。\n\n</details>\n"
        "\n## 10:00\n\n**Owner**\nありがとう。\n"
        "\n## 11:00 [自己検証]\n\n**SAKU**\n"
        "- 未確認: [commit] `fd5712b`: 「修正をコミット fd5712b に反映しました」\n"
    )
    voice = selfcheck.saku_voice(journal)
    assert "principles/mine.md" in voice
    assert "owner.md" not in voice
    assert "thought.md" not in voice
    assert "fd5712b" not in voice  # the audit's own quote is not re-audited


def test_commit_claims_are_detected():
    text = "修正をコミット fd5712b に反映しました。\n"
    found = selfcheck.claimed_commits(text)
    assert [ref for ref, _ in found] == ["fd5712b"]


def test_commit_claims_need_a_claim_context():
    # A bare hash mention with no completion/commit context is not a claim.
    assert selfcheck.claimed_commits("参考: ハッシュ a1dca8f の件。\n") == []
    # A pure-number token is not a hash even next to a claim verb.
    assert selfcheck.claimed_commits("20261119 件を送信しました。\n") == []


def test_bare_and_write_claim_extraction():
    claims = selfcheck.extract_claims("", "バグを修正しました。\n")
    assert [(c.kind, c.evidence) for c in claims] == [(selfcheck.KIND_BARE, "")]

    claims = selfcheck.extract_claims("", "`principles/x.md` に教訓を記録しました。\n")
    assert [(c.kind, c.evidence) for c in claims] == [
        (selfcheck.KIND_WRITE, "principles/x.md")
    ]


def test_write_claim_verification():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "principles").mkdir()
        (root / "principles" / "real.md").write_text("- 教訓\n", encoding="utf-8")
        text = (
            "principles/real.md に記録しました。\n"
            "principles/ghost.md に記録しました。\n"
        )
        result = selfcheck.audit_texts("2026-11-19", "", text, root, [])
        by_path = {c.evidence: c.status for c in result.claims}
        assert by_path["principles/real.md"] == selfcheck.STATUS_VERIFIED
        assert by_path["principles/ghost.md"] == selfcheck.STATUS_UNBACKED
        assert [c.evidence for c in result.flagged] == ["principles/ghost.md"]


def test_commit_claim_verification_with_real_git():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo, head = _git_repo(root)
        short = head[:7]
        text = (
            f"修正をコミット {short} に反映しました。\n"
            "修正をコミット fd5712b に反映しました。\n"
        )
        result = selfcheck.audit_texts("2026-11-19", "", text, root, [repo])
        by_ref = {c.evidence: c.status for c in result.claims}
        assert by_ref[short] == selfcheck.STATUS_VERIFIED
        # The #69 case: a confident hash that no repository knows.
        assert by_ref["fd5712b"] == selfcheck.STATUS_UNBACKED


def test_commit_claim_unchecked_without_a_repository():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text = "修正をコミット fd5712b に反映しました。\n"
        result = selfcheck.audit_texts("2026-11-19", "", text, root, [root])
        # root is not a git repository: git cannot answer, so the claim is
        # unchecked -- never "unbacked" on no evidence either way.
        assert result.claims[0].status == selfcheck.STATUS_UNCHECKED
        assert result.flagged == []


def test_ledger_recurrence_needs_distinct_days():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "selfcheck" / "ledger.json"

        def day_result():
            return selfcheck.AuditResult(
                date="x",
                claims=[selfcheck.Claim(selfcheck.KIND_COMMIT, "fd5712b", "…",
                                        selfcheck.STATUS_UNBACKED)],
            )

        recurring = selfcheck.record_and_find_recurring(
            ledger_path, "2026-11-18", day_result(), 2)
        assert recurring == []
        # Recording the same day twice must not fake a second day.
        recurring = selfcheck.record_and_find_recurring(
            ledger_path, "2026-11-18", day_result(), 2)
        assert recurring == []
        recurring = selfcheck.record_and_find_recurring(
            ledger_path, "2026-11-19", day_result(), 2)
        assert recurring == [selfcheck.KIND_COMMIT]


def test_render_audit_flags_unbacked_and_stays_quiet_without_claims():
    quiet = selfcheck.AuditResult(date="2026-11-19", claims=[])
    assert selfcheck.render_audit(quiet, []) == ""

    result = selfcheck.AuditResult(
        date="2026-11-19",
        claims=[
            selfcheck.Claim(selfcheck.KIND_COMMIT, "fd5712b",
                            "修正をコミット fd5712b に反映しました",
                            selfcheck.STATUS_UNBACKED),
            selfcheck.Claim(selfcheck.KIND_BARE, "", "バグを修正しました",
                            selfcheck.STATUS_UNCHECKED),
        ],
    )
    rendered = selfcheck.render_audit(result, [selfcheck.KIND_COMMIT])
    assert "未確認: 2件" in rendered
    assert "fd5712b" in rendered
    assert "証拠の提示なし" in rendered
    assert "再発" in rendered


def test_append_audit_writes_dated_journal_section():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        selfcheck.append_audit_to_journal(root, "2026-11-19", "**自己検証**\n本文")
        journal = (root / "journal" / "2026-11-19.md").read_text(encoding="utf-8")
        assert journal.startswith("# Journal — 2026-11-19")
        assert "[自己検証]" in journal
        assert "本文" in journal


def test_reflection_prompt_includes_audit_when_present():
    plain = reflection.build_reflection_prompt("2026-11-19", "日記", "独り言")
    assert "自己検証の結果" not in plain
    with_audit = reflection.build_reflection_prompt(
        "2026-11-19", "日記", "独り言", audit_content="- 未確認: 1件")
    assert "自己検証の結果" in with_audit
    assert "未確認: 1件" in with_audit


def test_config_defaults():
    from saku import config as cfg_mod

    cfg = cfg_mod.load_selfcheck_config({})
    assert cfg.enabled is True
    assert cfg.recurrence_threshold == 2
    cfg = cfg_mod.load_selfcheck_config(
        {"selfcheck": {"enabled": False, "recurrence_threshold": 3}})
    assert cfg.enabled is False
    assert cfg.recurrence_threshold == 3


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
    print(f"[*] All {_PASS + _FAIL} selfcheck tests {'PASSED!' if _FAIL == 0 else f'FAILED ({_FAIL})'}")
    return _FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
