#!/usr/bin/env python3
"""
Tests for saku.dreaming (memory promotion: journal/monologue -> MEMORY.md).
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import dreaming


def test_insert_after_heading():
    content = "# MEMORY\n\n## 好み・傾向\n\n## 重要な学び\n"
    out = dreaming._insert_after_heading(content, "好み・傾向", "- 2026-08-03: テスト")
    assert out.index("- 2026-08-03: テスト") < out.index("## 重要な学び")


def test_insert_new_section():
    content = "# MEMORY\n\n## 好み・傾向\n"
    out = dreaming._insert_after_heading(content, "進行中のプロジェクト", "- 2026-08-03: 新規")
    assert "## 進行中のプロジェクト" in out
    assert "- 2026-08-03: 新規" in out


def test_append_items_creates_and_dedupes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        items = [("好み・傾向", "Ownerは朝の会話を好む"), ("重要な学び", "予測符号化の原理")]
        added1 = dreaming.append_items(path, items, "2026-08-03")
        assert len(added1) == 2
        content = path.read_text(encoding="utf-8")
        assert "## 好み・傾向" in content
        assert "- 2026-08-03: Ownerは朝の会話を好む" in content
        # dedupe: same item again -> nothing new
        added2 = dreaming.append_items(path, items, "2026-08-04")
        assert added2 == []


def test_leading_date_is_not_duplicated():
    """#53: a promoted text that repeats the entry's own date must be cleaned."""
    assert dreaming.strip_leading_date("2026-10-15: 東京ゲームショウの観察", "2026-10-15") == "東京ゲームショウの観察"
    assert dreaming.strip_leading_date("2026-10-15 何かの記録", "2026-10-15") == "何かの記録"
    # a different date carries information and stays
    assert dreaming.strip_leading_date("2026-10-14: 出張の記録", "2026-10-15") == "2026-10-14: 出張の記録"
    assert dreaming.strip_leading_date("日付の無い内容", "2026-10-15") == "日付の無い内容"


def test_append_items_does_not_duplicate_the_date():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        added = dreaming.append_items(path, [("重要な学び", "2026-10-15: 学んだこと")], "2026-10-15")
        assert added == ["学んだこと"], added
        content = path.read_text(encoding="utf-8")
        assert "- 2026-10-15: 学んだこと" in content
        assert "2026-10-15: 2026-10-15" not in content


def test_parse_memory_items():
    raw = """前書きの雑談。

[MEMORY]
category: 進行中プロジェクト
content: 認知科学エッセンスを blog/ で作成中
[/MEMORY]

[MEMORY]
category: 好み・傾向
content: Ownerは簡潔さを好む
[/MEMORY]

[NO_MEMORY]
"""
    items = dreaming._MEMORY_ITEM_RE.findall(raw)
    assert len(items) == 2
    cat, content = items[0]
    assert cat.strip() == "進行中プロジェクト"
    assert "認知科学エッセンス" in content


def test_format_and_parse_entry():
    line = dreaming.format_entry("2026-08-03", "Ownerは簡潔さを好む", 2, "2026-08-30")
    assert line == "- 2026-08-03: Ownerは簡潔さを好む <!-- s:2 last:2026-08-30 -->"
    parsed = dreaming.parse_entry(line)
    assert parsed["date"] == "2026-08-03"
    assert parsed["text"] == "Ownerは簡潔さを好む"
    assert parsed["score"] == 2
    assert parsed["last"] == "2026-08-30"


def test_parse_entry_legacy_without_metadata():
    parsed = dreaming.parse_entry("- 2026-06-01: 昔の項目")
    assert parsed["text"] == "昔の項目"
    assert parsed["score"] is None
    assert parsed["last"] is None
    assert dreaming.parse_entry("## 見出し") is None


def test_score_for_category():
    assert dreaming.score_for("方針・決定") > dreaming.score_for("進行中プロジェクト")
    assert dreaming.score_for("方針・決定", reinforcements=2) == dreaming.score_for("方針・決定") + 2
    assert dreaming.score_for("unknown", reinforcements=-5) == dreaming.DEFAULT_SCORE


def test_append_items_writes_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        added = dreaming.append_items(path, [("方針・決定", "ローカルLLMを優先する")], "2026-08-03")
        assert added == ["ローカルLLMを優先する"]
        content = path.read_text(encoding="utf-8")
        assert "<!-- s:3 last:2026-08-03 -->" in content


def test_append_items_reinforces_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        item = [("好み・傾向", "Ownerは朝の会話を好む")]
        dreaming.append_items(path, item, "2026-08-03")
        added = dreaming.append_items(path, item, "2026-08-10")
        assert added == []  # no duplicate entry
        content = path.read_text(encoding="utf-8")
        assert content.count("Ownerは朝の会話を好む") == 1
        assert "<!-- s:3 last:2026-08-10 -->" in content  # score bumped, last-seen refreshed


def test_review_archives_stale_low_score():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        path.write_text(
            "# MEMORY — 長期記憶\n\n"
            "## 好み・傾向\n"
            "- 2025-01-01: 古い低スコア <!-- s:2 last:2025-01-05 -->\n\n"
            "## 重要な学び\n"
            "- 2025-01-01: 重要な学び <!-- s:5 last:2025-01-01 -->\n"
            "- 2025-06-01: 最近の学び <!-- s:1 last:2025-06-25 -->\n",
            encoding="utf-8",
        )
        decayed = dreaming.review(path, today="2025-07-01", ttl_days=90, min_score=2)
        assert decayed == ["古い低スコア"]
        content = path.read_text(encoding="utf-8")
        # moved, never deleted
        assert "古い低スコア" in content
        assert content.index("## Archive") < content.index("古い低スコア")
        # high-score and recent entries stay in the foreground
        assert "重要な学び" in content.split("## Archive")[0]
        assert "最近の学び" in content.split("## Archive")[0]


def test_review_ignores_legacy_entries():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "MEMORY.md"
        path.write_text(
            "# MEMORY — 長期記憶\n\n## 好み・傾向\n- 2025-01-01: メタデータ無しの既存項目\n",
            encoding="utf-8",
        )
        assert dreaming.review(path, today="2025-07-01", ttl_days=90, min_score=2) == []
        assert "メタデータ無しの既存項目" in path.read_text(encoding="utf-8")


def test_load_dreaming_config_defaults():
    from saku import config as cfg_mod

    d = cfg_mod.load_dreaming_config({"dreaming": {"ttl_days": 30}})
    assert d.ttl_days == 30
    assert d.review_enabled is True
    assert d.min_score == 2
    defaults = cfg_mod.load_dreaming_config({})
    assert defaults.ttl_days == 90


def test_extract_items_from_text_parses_memory_blocks():
    """#68: the shared extractor reads the same [MEMORY] shape as dreaming."""
    from types import SimpleNamespace

    raw = """[MEMORY]
category: 好み・傾向
content: 朝は静かな環境を好む
[/MEMORY]
[MEMORY]
category: 存在しないカテゴリ
content: これは捨てる
[/MEMORY]"""

    captured = {}

    def _fake_loop(history, llm_cfg, cfg, mem, code, **kw):
        captured["prompt"] = history[-1]["content"]
        captured["kwargs"] = kw
        return SimpleNamespace(last_raw=raw, visible="", thinking="")

    originals = (dreaming.run_agent_loop, dreaming.agent.build_system_prompt)
    dreaming.run_agent_loop = _fake_loop
    dreaming.agent.build_system_prompt = lambda: "system"
    try:
        items = dreaming.extract_items_from_text(
            "朝の作業ログ", date="2026-11-18", kind="daily", llm_cfg=object()
        )
    finally:
        dreaming.run_agent_loop, dreaming.agent.build_system_prompt = originals

    assert items == [("好み・傾向", "朝は静かな環境を好む")]
    assert "デイリーノート" in captured["prompt"]
    assert "2026-11-18" in captured["prompt"]
    # Machine-read output must not be streamed to stdout.
    assert callable(captured["kwargs"].get("on_visible"))


def test_extract_items_from_text_returns_empty_on_llm_error():
    from types import SimpleNamespace

    originals = (dreaming.run_agent_loop, dreaming.agent.build_system_prompt)
    dreaming.run_agent_loop = lambda *a, **k: SimpleNamespace(
        last_raw="[ERROR] empty reply", visible="", thinking=""
    )
    dreaming.agent.build_system_prompt = lambda: "system"
    try:
        items = dreaming.extract_items_from_text("x", date="2026-11-18", llm_cfg=object())
    finally:
        dreaming.run_agent_loop, dreaming.agent.build_system_prompt = originals
    assert items == []


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} dreaming tests PASSED!")


if __name__ == "__main__":
    run_tests()
