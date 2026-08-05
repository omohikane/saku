#!/usr/bin/env python3
"""
Unit tests for the new saku package modules:
context.py / thinking.py / transport.py / agent_loop.py

Runs without an LLM by mocking chat_stream.
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

from saku import agent_loop, context, thinking, transport
from saku.config import ContextConfig, LlmConfig


def _make_ctx() -> ContextConfig:
    return ContextConfig(compaction_trigger=0.7, keep_recent_tokens=2000,
                         prune_tool_results=True, max_tool_result_chars=2000)


def test_estimate_tokens():
    # ASCII is ~4 chars/token
    assert context.estimate_tokens("x" * 30) == 8
    assert context.estimate_tokens("") >= 1
    # Japanese/CJK is ~1 token/char (was drastically underestimated before)
    assert context.estimate_tokens("日本語" * 10) == 30
    assert context.estimate_tokens("日本語") == 3


def test_trim_old_tool_results():
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "user", "content": "[system] tool results:\n" + "y" * 5000},
        {"role": "user", "content": "[system] tool results:\n" + "z" * 5000},
        {"role": "user", "content": "[system] tool results:\n" + "w" * 5000},
        {"role": "assistant", "content": "a"},
    ]
    ctx = ContextConfig(max_tool_result_chars=1000)
    out = context.trim_old_tool_results(history, ctx)
    # The two most recent entries keep full content; the older one is truncated
    assert len(out[2]["content"]) < 2000
    assert len(out[3]["content"]) > 4900
    assert len(out[4]["content"]) > 4900
    # Non-tool messages are unchanged
    assert out[0]["content"] == "sys"


def test_truncate_history_keeps_system():
    history = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"m{i}" + "x" * 50} for i in range(10)
    ]
    out, dropped = context.truncate_history(history, keep_recent_tokens=100)
    assert out[0]["content"] == "sys"
    assert dropped > 0
    assert len(out) < len(history)


def test_split_thinking():
    t, v = thinking.split_thinking("a <think>inner</think> b")
    assert t == "inner"
    assert "a" in v and "b" in v and "inner" not in v and "<think>" not in v
    t, v = thinking.split_thinking("no think")
    assert t == ""
    assert v == "no think"


def test_exec_tools_unknown_and_fake():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools_dir = root / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )
        res = transport.exec_tools("[[ECHO]]\nhello\n[[END]]", root, root)
        assert "[ECHO] echo:hello" in res[0], res
        res2 = transport.exec_tools("[[NOPE]]\nx\n[[END]]", root, root)
        assert "unknown tool" in res2[0], res2
        # unclosed tool is auto-closed and executed (no "not closed" error loop)
        res3 = transport.exec_tools("[[ECHO]]\nhello\n", root, root)
        assert "[ECHO] echo:hello" in res3[0], res3
        assert not any("not closed" in r for r in res3), res3


def test_run_agent_loop_no_tools():
    calls = {"n": 0}

    def fake_chat_stream(history, llm_cfg, on_token=None):
        calls["n"] += 1
        return "まっすぐな回答"

    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
    original = agent_loop.chat_stream
    agent_loop.chat_stream = fake_chat_stream
    try:
        result = agent_loop.run_agent_loop(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
            llm, _make_ctx(), Path("/tmp"), Path("/tmp"),
        )
    finally:
        agent_loop.chat_stream = original

    assert result.visible == "まっすぐな回答"
    assert result.action_taken is True
    assert calls["n"] == 1


def test_run_agent_loop_with_tool_then_answer():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None):
            has_result = any(
                m.get("role") == "user" and "[system] tool results:" in str(m.get("content", ""))
                for m in history
            )
            return "完了" if has_result else "[[ECHO]]\nhi\n[[END]]"

        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            result = agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root,
            )
        finally:
            agent_loop.chat_stream = original

        assert "完了" in result.visible
        assert result.action_taken is True
        assert result.turns == 2


def test_run_agent_loop_no_action_marker():
    def fake_chat_stream(history, llm_cfg, on_token=None):
        return "[NO_ACTION]"

    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
    original = agent_loop.chat_stream
    agent_loop.chat_stream = fake_chat_stream
    try:
        result = agent_loop.run_agent_loop(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            llm, _make_ctx(), Path("/tmp"), Path("/tmp"),
            no_action_markers=["[NO_ACTION]", "[INBOX_PROCESSED]"],
        )
    finally:
        agent_loop.chat_stream = original

    assert result.action_taken is False
    assert "[NO_ACTION]" in result.visible


def test_run_agent_loop_error_breaks():
    def fake_chat_stream(history, llm_cfg, on_token=None):
        return "[ERROR] llama-server not reachable"

    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
    original = agent_loop.chat_stream
    agent_loop.chat_stream = fake_chat_stream
    try:
        result = agent_loop.run_agent_loop(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            llm, _make_ctx(), Path("/tmp"), Path("/tmp"),
        )
    finally:
        agent_loop.chat_stream = original

    assert result.last_raw.startswith("[ERROR]")
    assert result.visible == ""


def test_strip_tool_blocks():
    from saku.thinking import strip_tool_blocks

    assert strip_tool_blocks('[[READ_FILE path="x.md"]]\n[[END]]\nこんにちは') == "こんにちは"
    assert strip_tool_blocks('[[WRITE_FILE path="a.md"]]\nbody\n[[END]]\n記録') == "記録"
    assert strip_tool_blocks("[[LIST_DIR]]\n[[END]]") == ""
    assert strip_tool_blocks("plain text") == "plain text"
    assert strip_tool_blocks('[[READ_FILE path="meta.md"]]\n\nこんにちは') == "こんにちは"
    assert strip_tool_blocks("[[LIST_DIR]]\n続き") == "続き"
    assert strip_tool_blocks('[[APPEND_FILE path="a.md" heading="次にやりたいこと"]]\n- item\n[[END]]\n完了') == "完了"


class _FakeResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for c in self._chunks:
            yield f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}".encode("utf-8")


def test_chat_stream_suppresses_tool_syntax():
    import saku.llm as llm

    chunks = [
        "こんにちは",
        '[[READ_FILE path="x.md"]]',
        "\n",
        "[[END]]",
        "\n",
        "記録を確認しました",
    ]
    original = llm.requests.post
    llm.requests.post = lambda *a, **k: _FakeResp(chunks)
    received = []
    try:
        llm.chat_stream(
            [{"role": "user", "content": "hi"}],
            LlmConfig(api_url="http://x"),
            on_token=received.append,
        )
    finally:
        llm.requests.post = original

    shown = "".join(received)
    assert "[[READ_FILE" not in shown
    assert "[[END]]" not in shown
    assert "こんにちは" in shown
    assert "記録を確認しました" in shown


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} agent_loop/context/transport tests PASSED!")


if __name__ == "__main__":
    run_tests()
