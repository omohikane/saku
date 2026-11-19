#!/usr/bin/env python3
"""Tests for #126: the context budget and the recall cap must actually cap."""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import context, recall
from saku.config import ContextConfig


def test_enforce_budget_shrinks_one_huge_message():
    huge = "a" * 20000  # ~5000 tokens
    history = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": huge},
    ]
    budget = 1000
    out, shaved = context.enforce_budget(history, budget)
    assert context.total_tokens(out) <= budget
    assert shaved > 0
    assert "truncated to fit" in out[-1]["content"]
    # The original history is not mutated.
    assert history[1]["content"] == huge


def test_enforce_budget_drops_old_messages_before_the_request():
    history = [{"role": "system", "content": "sys"}]
    for i in range(20):
        history.append({"role": "user", "content": f"old {i} " + "x" * 2000})
    history.append({"role": "user", "content": "最新の依頼"})
    out, _shaved = context.enforce_budget(history, 500)
    assert context.total_tokens(out) <= 500
    assert out[0]["role"] == "system"
    assert "最新の依頼" in str(out[-1]["content"]) or "最新の依頼" in str(out[0]["content"])


def test_enforce_budget_is_a_noop_when_it_already_fits():
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out, shaved = context.enforce_budget(history, 1000)
    assert out == history
    assert shaved == 0


def test_prune_flag_only_disables_proactive_trimming():
    ctx = ContextConfig(prune_tool_results=False, max_tool_result_chars=10)
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "[system] tool results:\n" + "y" * 500},
        {"role": "user", "content": "[system] tool results:\n" + "z" * 500},
        {"role": "user", "content": "[system] tool results:\n" + "w" * 500},
    ]
    assert context.trim_old_tool_results(history, ctx) == history

    # The budget pass still shrinks them: disabling pruning must not disable
    # the hard limit.
    out, shaved = context.enforce_budget(history, 200, ctx)
    assert context.total_tokens(out) <= 200
    assert shaved > 0


def test_enforce_budget_shrinks_the_system_prompt_as_a_last_resort():
    history = [
        {"role": "system", "content": "s" * 20000},
        {"role": "user", "content": "hi"},
    ]
    out, _shaved = context.enforce_budget(history, 300)
    assert context.total_tokens(out) <= 300


def test_recall_cap_includes_the_first_and_longest_entry():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "MEMORY.md").write_text(
            "# MEMORY\n\n## 永続的な事実\n- " + "ローカルLLM" + "の詳細" * 400 + "\n",
            encoding="utf-8",
        )
        block = recall.recall(root, "ローカルLLMの詳細", items=5, max_chars=400)
        assert block
        assert len(block) <= 400
        assert "…" in block


def test_recall_returns_nothing_when_the_budget_cannot_hold_a_line():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "MEMORY.md").write_text(
            "# MEMORY\n\n## 永続的な事実\n- ローカルLLMの詳細\n", encoding="utf-8"
        )
        assert recall.recall(root, "ローカルLLM", items=5, max_chars=10) == ""


def test_agent_loop_applies_the_budget_before_calling_the_llm():
    from saku import agent_loop as al_mod
    from saku.config import LlmConfig

    seen = {}

    def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
        seen["tokens"] = context.total_tokens(history)
        return "ok"

    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=200)
    original = al_mod.chat_stream
    al_mod.chat_stream = fake_chat_stream
    try:
        al_mod.run_agent_loop(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "z" * 20000},
            ],
            llm,
            ContextConfig(),
            Path("/tmp"),
            Path("/tmp"),
        )
    finally:
        al_mod.chat_stream = original

    assert seen["tokens"] <= 200, seen


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} budget tests PASSED!")


if __name__ == "__main__":
    run_tests()
