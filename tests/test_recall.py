#!/usr/bin/env python3
"""
Tests for saku.recall (memory prefetch for the light prompt, refs #32).

No LLM: recall is a pure keyword search over a temporary memory root.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import recall
from saku import config as cfg_mod

MEMORY_MD = """# MEMORY — 長期記憶

## 好み・傾向
- 2026-08-20: Ownerは朝の会話を好む <!-- s:2 last:2026-08-20 -->

## 進行中のプロジェクト
- 2026-08-25: ローカルLLMのベンチマークを進行中 <!-- s:1 last:2026-08-25 -->
"""

PRINCIPLE = "# 原則\n\n- 推測で断定しない\n"


def _make_root():
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "MEMORY.md").write_text(MEMORY_MD, encoding="utf-8")
    (root / "principles").mkdir()
    (root / "principles" / "2026-08-01-honesty.md").write_text(PRINCIPLE, encoding="utf-8")
    (root / "wiki").mkdir()
    (root / "wiki" / "local-llm.md").write_text(
        "# ローカルLLM 2026\nQwen3.8-Flash-Next が候補。\n", encoding="utf-8"
    )
    return root


def test_terms_filters_common_words():
    ts = recall.terms("これはローカルLLMの話です")
    assert "ローカルllm" in ts or "ローカル" in ts
    assert "です" not in ts
    assert "これ" not in ts


def test_recall_returns_relevant_lines():
    root = _make_root()
    block = recall.recall(root, "ローカルLLMの状況は？")
    assert "関連する記憶" in block
    assert "ローカルLLM" in block
    assert "SEARCH_NOTES" in block


def test_recall_respects_item_budget():
    root = _make_root()
    block = recall.recall(root, "ローカルLLM 進行中 朝の会話", items=1)
    assert block.count("- (") == 1


def test_recall_respects_char_budget():
    root = _make_root()
    block = recall.recall(root, "ローカルLLMの状況は？", items=10, max_chars=60)
    assert len(block) < 400  # a couple of entries at most, not the whole file


def test_recall_empty_when_no_match():
    root = _make_root()
    assert recall.recall(root, "まったく無関係な話題xyz") == ""


def test_recall_strips_entry_metadata():
    root = _make_root()
    block = recall.recall(root, "朝の会話")
    assert "<!--" not in block
    assert "Ownerは朝の会話を好む" in block


def test_archived_entries_are_not_current_knowledge():
    """#128: an entry moved to ## Archive must not answer a normal query."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "MEMORY.md").write_text(
            "# MEMORY\n\n## 永続的な事実\n"
            "- 2026-01-01: 現在の設定はA <!-- s:3 last:2026-11-01 -->\n"
            "\n## Archive\n"
            "- 2025-01-01: 古い設定はB <!-- s:1 last:2025-01-01 -->\n",
            encoding="utf-8",
        )
        current = recall.recall(root, "設定はどうなってる？")
        assert "現在の設定はA" in current
        assert "古い設定はB" not in current

        past = recall.recall(root, "以前の設定はどうだった？")
        assert "古い設定はB" in past
        assert "（過去）" in past
        assert "現在の設定はA" in past


def test_archive_detection_is_memory_md_only():
    """A heading named Archive in another note is not a decay marker."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "principles").mkdir()
        (root / "principles" / "x.md").write_text(
            "# 原則\n\n## Archive\n- 設定の記録\n", encoding="utf-8"
        )
        block = recall.recall(root, "設定")
        assert "設定の記録" in block
        assert "（過去）" not in block


def test_iter_memory_items_reports_status():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "MEMORY.md").write_text(
            "## 好み・傾向\n- 2026-01-01: 現在の設定はAです <!-- s:2 last:2026-01-01 -->\n"
            "## Archive\n- 2025-01-01: 過去の設定はBでした <!-- s:0 last:2025-01-01 -->\n",
            encoding="utf-8",
        )
        items = list(recall.iter_memory_items(root, ["MEMORY.md"]))
        assert [i["status"] for i in items] == ["active", "archived"]
        assert items[0]["text"].startswith("現在の設定はAです")
        assert items[1]["text"].startswith("過去の設定はBでした")


def test_load_recall_config_defaults():
    d = cfg_mod.load_recall_config({})
    assert d.enabled is True
    assert d.items == 3
    assert d.max_chars == 600
    custom = cfg_mod.load_recall_config({"recall": {"enabled": False, "items": 1}})
    assert custom.enabled is False
    assert custom.items == 1


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} recall tests PASSED!")


if __name__ == "__main__":
    run_tests()
