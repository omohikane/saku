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
        # #123: an unclosed tool call is not executed (a cut stream must not
        # cause side effects); the error goes back to history instead.
        res3 = transport.exec_tools("[[ECHO]]\nhello\n", root, root)
        assert res3 and "not closed" in res3[0], res3
        assert "[ECHO] echo:hello" not in "".join(res3), res3


def test_single_bracket_tool_call_is_executed():
    """#47: [READ_FILE ...] (single bracket) must run, not leak into the reply."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools_dir = root / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )
        res = transport.exec_tools('[ECHO path="x"]\nhello\n', root, root)
        assert any("echo:hello" in r for r in res), res
        assert transport.list_tool_calls('[ECHO path="x"]') == ["ECHO"]


def test_single_bracket_call_stripped_from_visible_text():
    """#47: a single-bracket call must never reach chat.md / the journal."""
    assert thinking.strip_tool_blocks('では読みます。\n[READ_FILE path="meta.md"]') == "では読みます。"
    assert thinking.strip_tool_blocks('[WRITE_FILE path="principles/x.md"]\n本文') == "本文"
    # ordinary prose brackets are not touched
    assert thinking.strip_tool_blocks("[NOTE] これは本文です") == "[NOTE] これは本文です"
    # documented double-bracket form still works
    assert thinking.strip_tool_blocks('[[READ_FILE path="x.md"]]\n[[END]]\nこんにちは') == "こんにちは"


def test_internal_prompt_fragments_are_stripped():
    """#51: echoed prompt scaffolding must not reach chat.md / the journal."""
    leak = (
        "[system] chat.md上の会話履歴（直前）:\n"
        "- 16:50: 「アーカイブ後のチャットです」 → 16:51に回答済み。\n\n"
        "[system] 関連する記憶（自動想起）:\n"
        "- principles/2026-06-18-web-research.md: 2. Value of context-aware adaptation\n"
        "\n"
        "本文としての答えです。"
    )
    out = thinking.strip_tool_blocks(leak)
    assert out == "本文としての答えです。", out


def test_narration_line_is_stripped():
    leak = (
        "Ownerのメッセージは「A2A」について検索して要点だけ教えてという指示です。\n"
        "要点は次のとおりです。"
    )
    out = thinking.strip_tool_blocks(leak)
    assert "指示です" not in out, out
    assert "要点は次のとおりです。" in out, out


def test_recall_block_is_stripped_but_normal_text_survives():
    recall = (
        "# 関連する記憶（自動想起）\n"
        "- (MEMORY.md) 2026-10-15: 何かの記録\n"
        "\n"
        "必要なら `SEARCH_NOTES` で更に掘り、想起した内容に基づいて答えてください。\n"
    )
    assert thinking.strip_tool_blocks(recall) == ""


def test_internal_markers_are_reported():
    assert thinking.internal_markers_in("[system] 何か") != []
    assert thinking.internal_markers_in("普通の返事です。") == []


def test_executable_text_drops_examples_and_quotes():
    text = (
        "説明します。\n\n```\n[[WRITE_FILE path=\"a.md\"]]\nx\n[[END]]\n```\n\n"
        "インラインは `[[DELETE_FILE path=\"b.md\"]]` です。\n"
        "> 引用された [[APPEND_FILE path=\"c.md\"]]\n"
        "[[ECHO]]\nreal\n[[END]]\n"
    )
    out = transport.executable_text(text)
    assert "WRITE_FILE" not in out
    assert "DELETE_FILE" not in out
    assert "APPEND_FILE" not in out
    assert "[[ECHO]]" in out


def test_thinking_and_examples_never_execute():
    """#123: tool syntax in a think block, a fence or a quote must not run."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        marker = root / "executed.txt"
        (root / "tools" / "probe.py").write_text(
            "from pathlib import Path\n"
            "def run(base, path, body='', **kw):\n"
            "    Path(base, 'executed.txt').write_text(body)\n"
            "    return 'ran'\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            return (
                "<think>\n[[PROBE]]\nfrom-think\n[[END]]\n</think>\n"
                "```\n[[PROBE]]\nfrom-fence\n[[END]]\n```\n"
                "> [[PROBE]]\n> from-quote\n> [[END]]\n"
                "以上です。"
            )

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

        assert not marker.exists(), "a non-actionable mention executed a tool"
        assert "以上です。" in result.visible


def test_allowed_tools_empty_disables_execution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        marker = root / "executed.txt"
        (root / "tools" / "probe.py").write_text(
            "from pathlib import Path\n"
            "def run(base, path, body='', **kw):\n"
            "    Path(base, 'executed.txt').write_text(body)\n"
            "    return 'ran'\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            return "[[PROBE]]\nx\n[[END]]"

        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            result = agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root, allowed_tools=set(),
            )
        finally:
            agent_loop.chat_stream = original

        assert not marker.exists(), "tools-disabled still executed a tool"
        assert "not allowed" in result.history[-2]["content"]


def test_unclosed_tool_call_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        marker = root / "executed.txt"
        (root / "tools" / "probe.py").write_text(
            "from pathlib import Path\n"
            "def run(base, path, body='', **kw):\n"
            "    Path(base, 'executed.txt').write_text(body)\n"
            "    return 'ran'\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            return "[[PROBE]]\nhalf-generated"

        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root,
            )
        finally:
            agent_loop.chat_stream = original

        assert not marker.exists(), "an unclosed call from a cut reply was executed"


def test_incomplete_stream_does_not_run_tools():
    """#127: an EOF without [DONE] must not start side effects."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        marker = root / "executed.txt"
        (root / "tools" / "probe.py").write_text(
            "from pathlib import Path\n"
            "def run(base, path, body='', **kw):\n"
            "    Path(base, 'executed.txt').write_text(body)\n"
            "    return 'ran'\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            if status is not None:
                status["complete"] = False
                status["error"] = False
            return "[[PROBE]]\nx\n[[END]]"

        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        logs = []
        try:
            agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root, log=logs.append,
            )
        finally:
            agent_loop.chat_stream = original

        assert not marker.exists(), "tools ran on an incomplete stream"
        assert any("incomplete stream" in msg for msg in logs), logs


def test_run_agent_loop_no_tools():
    calls = {"n": 0}

    def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
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

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
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


def test_visible_text_is_not_concatenated_across_turns():
    """#54: only the last turn's text is shown — never every turn joined.

    The real failure: turn 1 answered with a numbered list *and* called a tool, turn 2
    restated the same answer, and chat.md got both ("要点は…" twice).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            has_result = any(
                m.get("role") == "user" and "[system] tool results:" in str(m.get("content", ""))
                for m in history
            )
            if has_result:
                return "最終回答です。"
            return "（前置き）まず調べます。\n\n[[ECHO]]\nhi\n[[END]]"

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

        assert result.visible == "最終回答です。", result.visible
        assert "前置き" not in result.visible


def test_visible_text_survives_a_final_empty_turn():
    """#54: if the closing turn says nothing, the previous turn's answer is kept."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            has_result = any(
                m.get("role") == "user" and "[system] tool results:" in str(m.get("content", ""))
                for m in history
            )
            return "" if has_result else "調べた結果の回答です。\n\n[[ECHO]]\nhi\n[[END]]"

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

        assert result.visible == "調べた結果の回答です。", result.visible


def test_compaction_is_not_announced_when_nothing_is_dropped():
    """A no-op compaction must stay quiet: it used to warn on every chat turn."""
    logged: list[str] = []
    ctx = ContextConfig(compaction_trigger=0.7, keep_recent_tokens=10_000,
                        prune_tool_results=False, max_tool_result_chars=2000)
    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100)
    history = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": "x" * 400} for _ in range(3)
    ]
    original = agent_loop.chat_stream
    agent_loop.chat_stream = lambda h, c, on_token=None, status=None: "回答"
    try:
        agent_loop.run_agent_loop(history, llm, ctx, Path("/tmp"), Path("/tmp"), log=logged.append)
    finally:
        agent_loop.chat_stream = original

    assert not [m for m in logged if "compacted" in m], logged


def test_compaction_is_announced_when_history_is_dropped():
    logged: list[str] = []
    ctx = ContextConfig(compaction_trigger=0.7, keep_recent_tokens=50,
                        prune_tool_results=False, max_tool_result_chars=2000)
    llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100)
    history = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": "あ" * 200} for _ in range(3)
    ]
    original = agent_loop.chat_stream
    agent_loop.chat_stream = lambda h, c, on_token=None, status=None: "回答"
    try:
        agent_loop.run_agent_loop(history, llm, ctx, Path("/tmp"), Path("/tmp"), log=logged.append)
    finally:
        agent_loop.chat_stream = original

    assert [m for m in logged if "compacted" in m], logged


def test_unbacked_write_claims_are_reported():
    """#52: a reply that says it wrote a file nothing backs must be flagged."""
    logged: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = lambda h, c, on_token=None, status=None: (
            "`principles/2026-10-16-none.md` に教訓を記録しました。"
        )
        try:
            result = agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root, log=logged.append,
            )
        finally:
            agent_loop.chat_stream = original

    assert result.unbacked_claims == ["principles/2026-10-16-none.md"]
    assert any("nothing backed" in m for m in logged), logged


def test_backed_write_claims_are_not_reported():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "principles").mkdir()
        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            # What a successful WRITE_FILE tool call leaves behind.
            (root / "principles" / "2026-10-16-real.md").write_text("- 学び\n", encoding="utf-8")
            return "`principles/2026-10-16-real.md` に記録しました。"

        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            result = agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root,
            )
        finally:
            agent_loop.chat_stream = original

    assert result.unbacked_claims == []


def test_run_agent_loop_no_action_marker():
    def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
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
    def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
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


def test_status_events_report_progress():
    """on_status reports "thinking" and tool execution so a UI can show liveness."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            has_result = any(
                m.get("role") == "user" and "[system] tool results:" in str(m.get("content", ""))
                for m in history
            )
            return "完了" if has_result else "[[ECHO]]\nhi\n[[END]]"

        statuses = []
        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root,
                on_status=statuses.append,
            )
        finally:
            agent_loop.chat_stream = original

        assert statuses.count("thinking") >= 1
        assert "tool: ECHO" in statuses


def test_list_tool_calls():
    from saku import transport

    raw = '[[SEARCH_NOTES]]\nfoo\n[[END]]\nそして\n[[WEB_SEARCH query="x"]]\n[[END]]'
    assert transport.list_tool_calls(raw) == ["SEARCH_NOTES", "WEB_SEARCH"]
    # [[END]] is a closing marker, not a tool; duplicates collapse
    assert transport.list_tool_calls("[[A]]\n[[END]]\n[[A]]\n[[END]]") == ["A"]
    assert transport.list_tool_calls("ただの文章") == []


def test_final_answer_rescue_when_all_turns_use_tools():
    """A tool-only reply loop must still surface an answer to the caller."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        (root / "tools" / "echo.py").write_text(
            "def run(base, path, body='', **kw):\n    return 'echo:' + body\n",
            encoding="utf-8",
        )

        calls = {"n": 0}

        def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
            calls["n"] += 1
            # The rescue call carries the "no more tools" instruction
            last = str(history[-1].get("content", ""))
            if "ツールはもう使えません" in last:
                return "集めた情報から: 回答です。"
            return f"[[ECHO]]\ncall{calls['n']}\n[[END]]"

        llm = LlmConfig(name="mock", api_url="http://x", working_budget_tokens=100000)
        original = agent_loop.chat_stream
        agent_loop.chat_stream = fake_chat_stream
        try:
            result = agent_loop.run_agent_loop(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
                llm, _make_ctx(), root, root, max_turns=2,
            )
        finally:
            agent_loop.chat_stream = original

        assert "回答です" in result.visible
        assert calls["n"] == 3  # 2 tool turns + 1 rescue


def test_no_rescue_when_visible_answer_exists():
    def fake_chat_stream(history, llm_cfg, on_token=None, status=None):
        return "普通の回答"

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
    assert result.visible == "普通の回答"


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
