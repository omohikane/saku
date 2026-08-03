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


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} dreaming tests PASSED!")


if __name__ == "__main__":
    run_tests()
