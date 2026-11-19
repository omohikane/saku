#!/usr/bin/env python3
"""Tests for the skill learning loop (#104): saku.skills + its wiring.

Everything runs in temporary directories with a stubbed LLM; no network,
no real vault. The daemon test patches the agent loop and the learning
entry point, following test_daemon.py's patching style.
"""

import json
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku import provenance
from saku import recall
from saku import reflection
from saku import skills

GOOD_DOC = """---
name: restart-llm-server
description: LLMサーバが応答しないときに再起動する手順
---

# LLMサーバの再起動

## 前提
- サーバマシンにアクセスできること

## 手順
1. プロセスの状態を確認する
2. サービスを再起動する

## 検証
- ヘルスチェックが200を返すこと
"""


class StubChat:
    """Deterministic chat function; records calls like the mail stub."""

    def __init__(self, text: str = GOOD_DOC, exc: Exception | None = None) -> None:
        self.text = text
        self.exc = exc
        self.calls = 0
        self.last_messages = None

    def __call__(self, messages, llm_cfg) -> str:
        self.calls += 1
        self.last_messages = messages
        if self.exc is not None:
            raise self.exc
        return self.text


def _scfg(**overrides) -> cfg_mod.SkillsConfig:
    base = {"learning_enabled": True, "max_chars": 3000, "max_result_chars": 3000}
    base.update(overrides)
    return cfg_mod.SkillsConfig(**base)


def _distill(root: Path, chat: StubChat, **kw):
    args = {
        "memory_root": root,
        "llm_cfg": None,
        "scfg": _scfg(),
        "source_ref": "task/2026-11-19-3",
        "fallback_slug": "learned-2026-11-19-3",
        "chat_fn": chat,
    }
    args.update(kw)
    return skills.distill_skill("LLMサーバを再起動して", "再起動し、200を確認した", **args)


# ── Pure helpers ────────────────────────────────────────


def test_slugify():
    assert skills.slugify("Restart LLM Server!") == "restart-llm-server"
    assert skills.slugify("  fix__the  THING  ") == "fix-the-thing"
    assert skills.slugify("サーバー再起動") == ""  # nothing ASCII survives
    assert skills.slugify("") == ""


def test_parse_skill_document_ok():
    name, desc, body = skills.parse_skill_document(GOOD_DOC)
    assert name == "restart-llm-server"
    assert "再起動する手順" in desc
    assert body.startswith("# LLMサーバの再起動")
    assert "ヘルスチェック" in body


def test_parse_strips_thinking_and_tool_syntax():
    raw = "<think>考え中</think>\n" + GOOD_DOC + '\n[[WRITE_FILE path="x.md"]]\n'
    parsed = skills.parse_skill_document(raw)
    assert parsed is not None
    assert "考え中" not in parsed[2]
    assert "WRITE_FILE" not in parsed[2]


def test_parse_rejects_malformed_documents():
    assert skills.parse_skill_document("") is None
    assert skills.parse_skill_document("[ERROR] connection refused") is None
    assert skills.parse_skill_document("ただの文章です") is None
    # Front matter without a description.
    assert skills.parse_skill_document("---\nname: x\n---\n\n# T\n本文\n") is None
    # Front matter without a body.
    assert (
        skills.parse_skill_document("---\nname: x\ndescription: y\n---\n") is None
    )


def test_render_records_provenance():
    text = skills.render_skill_file("my-skill", "説明", "# T\n本文", "task/2026-11-19-3")
    assert text.startswith("---\nname: my-skill\ndescription: 説明\n---\n")
    assert provenance.recorded_ref(text) == "task/2026-11-19-3"
    assert "> source: task/2026-11-19-3" in text


def test_task_provenance_kind():
    assert provenance.KIND_TASK in provenance.KNOWN_KINDS
    ref = provenance.make_ref(provenance.KIND_TASK, "2026-11-19-3")
    assert ref == "task/2026-11-19-3"


def test_build_distill_prompt_caps_the_report():
    prompt = skills.build_distill_prompt("タスク", "あ" * 100, max_result_chars=10)
    assert "タスク" in prompt
    assert "[... truncated]" in prompt
    assert "name:" in prompt and "description:" in prompt


# ── Config ──────────────────────────────────────────────


def test_skills_config_defaults_to_disabled():
    d = cfg_mod.load_skills_config({})
    assert d.learning_enabled is False  # merging must not change a running instance
    assert d.max_chars == 3000
    custom = cfg_mod.load_skills_config({"skills": {"learning_enabled": True}})
    assert custom.learning_enabled is True


# ── Distillation ────────────────────────────────────────


def test_distill_writes_skill_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chat = StubChat()
        path = _distill(root, chat)
        assert path == root / "skills" / "restart-llm-server.md"
        text = path.read_text(encoding="utf-8")
        assert "name: restart-llm-server" in text
        assert provenance.recorded_ref(text) == "task/2026-11-19-3"
        assert "サービスを再起動する" in text
        # The distillation prompt carried the task and SAKU's own report.
        user = chat.last_messages[-1]["content"]
        assert "LLMサーバを再起動して" in user
        assert "200を確認した" in user


def test_distill_never_overwrites_existing_skill():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert _distill(root, StubChat()) is not None
        before = (root / "skills" / "restart-llm-server.md").read_text(encoding="utf-8")
        assert _distill(root, StubChat()) is None  # revision is reflection's job
        after = (root / "skills" / "restart-llm-server.md").read_text(encoding="utf-8")
        assert before == after


def test_distill_drops_malformed_output():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert _distill(root, StubChat("形式に従わない文章")) is None
        assert not (root / "skills").exists()


def test_distill_survives_llm_failures():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert _distill(root, StubChat("[ERROR] llama-server not reachable")) is None
        assert _distill(root, StubChat(exc=RuntimeError("boom"))) is None
        assert not (root / "skills").exists()


def test_distill_japanese_name_uses_fallback_slug():
    doc = GOOD_DOC.replace("name: restart-llm-server", "name: サーバー再起動")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _distill(root, StubChat(doc))
        assert path == root / "skills" / "learned-2026-11-19-3.md"
        text = path.read_text(encoding="utf-8")
        assert "name: learned-2026-11-19-3" in text  # front matter stays spec-valid


# ── Daemon entry point ──────────────────────────────────


def test_learn_disabled_by_default_spends_no_llm():
    with tempfile.TemporaryDirectory() as tmp:
        chat = StubChat()
        out = skills.learn_from_scheduled_task(
            {"id": "3", "task": "LLMサーバを再起動して"},
            "完了した",
            cfg={},  # no [skills] section at all
            memory_root=Path(tmp),
            llm_cfg=None,
            today="2026-11-19",
            chat_fn=chat,
        )
        assert out is None
        assert chat.calls == 0


def test_learn_enabled_writes_with_task_ref():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chat = StubChat()
        out = skills.learn_from_scheduled_task(
            {"id": "3", "task": "LLMサーバを再起動して"},
            "完了した",
            cfg={"skills": {"learning_enabled": True}},
            memory_root=root,
            llm_cfg=None,
            today="2026-11-19",
            chat_fn=chat,
        )
        assert out is not None and out.exists()
        assert provenance.recorded_ref(out.read_text(encoding="utf-8")) == "task/2026-11-19-3"
        assert chat.calls == 1


def test_learn_never_raises():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = {"skills": {"learning_enabled": True}}
        # No task text: nothing to learn, no LLM spent.
        chat = StubChat()
        assert (
            skills.learn_from_scheduled_task(
                {"id": "3"}, "x", cfg=cfg, memory_root=root, llm_cfg=None,
                today="2026-11-19", chat_fn=chat,
            )
            is None
        )
        assert chat.calls == 0
        # LLM explodes: swallowed, reported as None.
        assert (
            skills.learn_from_scheduled_task(
                {"id": "3", "task": "何か"}, "x", cfg=cfg, memory_root=root,
                llm_cfg=None, today="2026-11-19",
                chat_fn=StubChat(exc=RuntimeError("boom")),
            )
            is None
        )


# ── Recall surfacing ────────────────────────────────────


def test_recall_surfaces_skills_with_default_sources():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "skills").mkdir()
        (root / "skills" / "restart-llm-server.md").write_text(
            "# LLMサーバの再起動\n\n- 再起動の前にヘルスチェックを確認する\n",
            encoding="utf-8",
        )
        block = recall.recall(root, "LLMサーバを再起動したい")
        assert "skills/restart-llm-server.md" in block
        assert "再起動" in block


# ── Reflection wiring ───────────────────────────────────


def test_reflection_skill_review_is_opt_in():
    plain = reflection.build_reflection_prompt("2026-08-30", "日記", "独り言")
    assert "> source: journal/2026-08-30" in plain
    assert "source: task/" not in plain  # default prompt unchanged
    reviewed = reflection.build_reflection_prompt(
        "2026-08-30", "日記", "独り言", skill_review=True
    )
    assert "source: task/" in reviewed
    assert "journal/2026-08-30" in reviewed


# ── Daemon wiring ───────────────────────────────────────


def test_daemon_learns_only_from_successful_scheduled_tasks():
    from saku import daemon as daemon_mod
    from _isolate_logging import isolate_daemon_logging

    isolate_daemon_logging(daemon_mod.logger)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        sched = root / "state" / "schedule.json"
        items = [
            {"id": "1", "task": "due task", "when": "2000-01-01 00:00", "status": "pending"},
            {"id": "2", "task": "future task", "when": "2999-01-01 00:00", "status": "pending"},
        ]
        sched.write_text(json.dumps(items), encoding="utf-8")

        learned = []
        results = [(True, "completion report"), (False, "")]

        orig_root = daemon_mod.MEMORY_ROOT
        orig_ks = daemon_mod.killswitch_stopped
        orig_loop = daemon_mod.run_agent_loop
        orig_learn = daemon_mod.saku_skills.learn_from_scheduled_task
        daemon_mod.MEMORY_ROOT = root
        daemon_mod.killswitch_stopped = lambda: False
        daemon_mod.run_agent_loop = lambda *a, **k: results.pop(0)
        daemon_mod.saku_skills.learn_from_scheduled_task = (
            lambda item, visible, **kw: learned.append((item["id"], visible))
        )
        try:
            daemon_mod.check_scheduled_tasks()
            daemon_mod.check_scheduled_tasks()  # second pass: item 1 is done, nothing due
        finally:
            daemon_mod.MEMORY_ROOT = orig_root
            daemon_mod.killswitch_stopped = orig_ks
            daemon_mod.run_agent_loop = orig_loop
            daemon_mod.saku_skills.learn_from_scheduled_task = orig_learn

        assert learned == [("1", "completion report")]
        saved = json.loads(sched.read_text(encoding="utf-8"))
        assert saved[0]["status"] == "done"
        assert saved[1]["status"] == "pending"


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} skills tests PASSED!")


if __name__ == "__main__":
    run_tests()
