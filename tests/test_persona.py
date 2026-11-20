#!/usr/bin/env python3
"""Tests for saku.persona (#71: continuity and conversational style)."""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
if str(CODE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT.parent))

from saku import core, daemon, persona

META = """# Meta

## 最近の出来事
- 2026-11-10: サーバーに移行した。
- 2026-11-17: 認証の話をした。

## 次にやりたいこと
- 記憶の連続性を直す。

## 現在の状態
- 元気。
"""

MEMORY = """# MEMORY

- 2026-11-04: OwnerはVPNを大事にしている <!-- s:3 last:2026-11-04 -->
- 2026-10-01: 単発の好み <!-- s:2 last:2026-10-01 -->
"""


def _memory_root(tmp: str, meta: str = META, memory: str = MEMORY) -> Path:
    root = Path(tmp) / "memory"
    root.mkdir(parents=True)
    if meta is not None:
        (root / "meta.md").write_text(meta, encoding="utf-8")
    if memory is not None:
        (root / "MEMORY.md").write_text(memory, encoding="utf-8")
    return root


def test_defaults():
    cfg = persona.load_persona_config({})
    assert cfg.continuity_enabled is True
    assert cfg.style_contract is True
    assert cfg.reflection_chat_report is False
    assert cfg.continuity_chars == 1800


def test_continuity_includes_recent_and_durable_memories():
    with tempfile.TemporaryDirectory() as tmp:
        block = persona.continuity_block(_memory_root(tmp))
        assert "最近の出来事" in block
        assert "サーバーに移行した" in block
        assert "次にやりたいこと" in block
        assert "VPNを大事にしている" in block
        # a score below the threshold is not continuity
        assert "単発の好み" not in block


def test_continuity_respects_budget():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = persona.load_persona_config({"persona": {"continuity_chars": 120}})
        block = persona.continuity_block(_memory_root(tmp), cfg)
        assert 0 < len(block) <= 120


def test_sourced_entries_reach_continuity():
    """#125: the writer's own format must be readable by the continuity parser."""
    from saku import dreaming

    plain = dreaming.format_entry("2026-08-03", "静かな朝が好き", 5, "2026-11-18")
    sourced = dreaming.format_entry(
        "2026-08-03", "静かな朝が好き", 5, "2026-11-18", src="journal/2026-08-03"
    )
    assert "src:" in sourced
    assert persona._memory_items(plain, min_score=3, limit=8) == ["静かな朝が好き"]
    assert persona._memory_items(sourced, min_score=3, limit=8) == ["静かな朝が好き"]


def test_unknown_or_reordered_metadata_keeps_the_body():
    """A future metadata field must not silently drop a durable memory."""
    from saku import dreaming

    line = "- 2026-08-03: 静かな朝が好き <!-- src:journal/2026-08-03 s:5 last:2026-11-18 conf:0.9 -->"
    parsed = dreaming.parse_entry(line)
    assert parsed is not None
    assert parsed["text"] == "静かな朝が好き"
    assert parsed["score"] == 5
    assert parsed["src"] == "journal/2026-08-03"
    assert persona._memory_items(line, min_score=3, limit=8) == ["静かな朝が好き"]


def test_legacy_entry_parses_but_stays_below_the_score_floor():
    from saku import dreaming

    parsed = dreaming.parse_entry("- 2026-08-03: 古い記憶")
    assert parsed is not None and parsed["score"] is None and parsed["src"] is None
    assert persona._memory_items("- 2026-08-03: 古い記憶", min_score=3, limit=8) == []


def test_others_block_names_configured_agents_without_tokens():
    """#73/#120: the Owner-facing prompt must know its external contacts."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "children").mkdir()
        (root / "children" / "mei.md").write_text("# mei", encoding="utf-8")
        (root / "mail").mkdir()
        (root / "mail" / "talk.log").write_text(
            "[2026-11-19 21:00:00] [talk] 小夜: received\n", encoding="utf-8"
        )
        cfg = {
            "talk": {"callers": [{"name": "小夜", "token_env": "SHOULD_NOT_APPEAR"}]},
            "identity": {"agents": {"sayo": {"token_env": "ALSO_SECRET", "scopes": ["mail"]}}},
        }
        block = persona.others_block(root, cfg)
        assert "小夜" in block and "sayo" in block
        assert "2026-11-19" in block
        assert "mei" in block
        assert "SHOULD_NOT_APPEAR" not in block and "ALSO_SECRET" not in block
        assert "saku say --to" in block


def test_others_block_is_empty_without_agents():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        assert persona.others_block(Path(tmp), {}) == ""


def test_continuity_can_be_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = persona.load_persona_config({"persona": {"continuity_enabled": False}})
        assert persona.continuity_block(_memory_root(tmp), cfg) == ""


def test_continuity_missing_files_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "empty"
        root.mkdir()
        assert persona.continuity_block(root) == ""


def test_style_contract_has_the_rules():
    text = persona.STYLE_CONTRACT
    assert "最初の一文" in text
    assert "挨拶" in text
    assert "作業報告" in text
    assert "質問は最大1つ" in text


def test_light_prompt_contains_style_and_continuity():
    with tempfile.TemporaryDirectory() as tmp:
        root = _memory_root(tmp)
        saved_root, saved_cfg, saved_cache = core.MEMORY_ROOT, core._cfg, core._prompt_cache["static"]
        try:
            core.MEMORY_ROOT = root
            core._cfg = {"persona": {"continuity_enabled": True, "style_contract": True}}
            core._prompt_cache["static"] = None
            prompt = core.build_light_system_prompt()
            assert "会話の作法" in prompt
            assert "サーバーに移行した" in prompt
        finally:
            core.MEMORY_ROOT, core._cfg = saved_root, saved_cfg
            core._prompt_cache["static"] = saved_cache


def test_parse_chat_history_drops_leading_assistant():
    content = """**SAKU** (02:00)
昨日の振り返りを完了しました。

**Owner** (09:00)
おはよう

**SAKU** (09:01)
おはようございます。
"""
    history = daemon.parse_chat_history(content)
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "おはよう"
    assert len(history) == 2


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} persona tests PASSED!")


if __name__ == "__main__":
    run_tests()
