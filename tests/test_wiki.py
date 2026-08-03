#!/usr/bin/env python3
"""
Tests for saku.wiki (self-organized knowledge base).
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import wiki


def test_slugify():
    assert wiki.slugify("予測符号化") == "予測符号化"
    assert wiki.slugify("Predictive Coding") == "predictive-coding"
    assert wiki.slugify("予測符号化（Predictive Coding）") != ""


def test_create_note():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wiki"
        res = wiki.create_note(root, "予測符号化", "脳は予測誤差を最小化する", tags="認知科学", links="[[自由エネルギー原理]]")
        assert "[OK]" in res
        p = root / "予測符号化.md"
        assert p.exists()
        content = p.read_text(encoding="utf-8")
        assert "# 予測符号化" in content
        assert "tags: 認知科学" in content
        assert "links: [[自由エネルギー原理]]" in content


def test_regenerate_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wiki"
        wiki.create_note(root, "予測符号化", "内容A")
        wiki.create_note(root, "自由エネルギー原理", "内容B")
        res = wiki.regenerate_index(root)
        assert "2 notes" in res
        index = root / "_index.md"
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "[[予測符号化]]" in content
        assert "[[自由エネルギー原理]]" in content


def test_update_link():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "wiki"
        wiki.create_note(root, "予測符号化", "内容A")
        res = wiki.update_link(root, "予測符号化", "[[ホームネットワーク]]")
        assert "[OK]" in res
        content = (root / "予測符号化.md").read_text(encoding="utf-8")
        assert "links: [[ホームネットワーク]]" in content
        # idempotent
        res2 = wiki.update_link(root, "予測符号化", "[[ホームネットワーク]]")
        assert "already present" in res2


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} wiki tests PASSED!")


if __name__ == "__main__":
    run_tests()
