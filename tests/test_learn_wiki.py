#!/usr/bin/env python3
"""Wiki stage of `saku learn` (#68): concept notes, append-not-overwrite, index.

Temporary wiki roots only. The concept pass is stubbed, so no LLM is called and
no network is touched.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import learning


def _concept(title, body, tags="集中, 朝", links="[[MEMORY]]"):
    return {"title": title, "body": body, "tags": tags, "links": links}


def _wiki(tmp: Path) -> Path:
    root = tmp / "wiki"
    root.mkdir()
    return root


def test_new_concept_note_has_tags_source_and_index_entry():
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        report = learning.apply_concepts(
            [_concept("静かな朝の集中", "朝は静かな環境のほうが集中しやすい。")],
            wiki_root=root,
            date="2026-11-18",
        )
        assert report["created"] == ["静かな朝の集中"]

        note = (root / "静かな朝の集中.md").read_text(encoding="utf-8")
        assert note.startswith("# 静かな朝の集中")
        assert "> tags: auto, learn, 集中, 朝" in note
        assert "> created: 2026-11-18" in note
        assert "> source: daily/2026-11-18" in note
        assert "> links: [[MEMORY]]" in note
        assert "朝は静かな環境のほうが集中しやすい。" in note

        index = (root / "_index.md").read_text(encoding="utf-8")
        assert "- [[静かな朝の集中]] 静かな朝の集中" in index


def test_same_concept_twice_appends_nothing_and_keeps_source():
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        concept = _concept("静かな朝の集中", "朝は静かな環境のほうが集中しやすい。")
        learning.apply_concepts([concept], wiki_root=root, date="2026-11-18")
        before = (root / "静かな朝の集中.md").read_text(encoding="utf-8")

        second = learning.apply_concepts(
            [_concept("静かな朝の集中", "朝は静かな環境のほうが集中しやすい。新事実。")],
            wiki_root=root,
            date="2026-11-19",
        )
        assert second["updated"] == ["静かな朝の集中"]
        after = (root / "静かな朝の集中.md").read_text(encoding="utf-8")
        assert "新事実" in after
        assert after.count("朝は静かな環境のほうが集中しやすい。") == 1
        assert "> created: 2026-11-18" in after
        assert "> source: daily/2026-11-18" in after, "provenance is never rewritten"
        assert after.count("## 追記") == 1
        # A run that adds nothing changes nothing.
        third = learning.apply_concepts(
            [_concept("静かな朝の集中", "朝は静かな環境のほうが集中しやすい。")],
            wiki_root=root,
            date="2026-11-20",
        )
        assert third["updated"] == []
        assert third["skipped"][0][1] == "already recorded"
        assert (root / "静かな朝の集中.md").read_text(encoding="utf-8") == after


def test_updated_never_moves_backwards():
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        learning.apply_concepts(
            [_concept("生活リズム", "朝は静かな環境が良い。")],
            wiki_root=root,
            date="2026-11-18",
        )
        # A later processing run over an older note must not rewind `updated`.
        learning.apply_concepts(
            [_concept("生活リズム", "夜は早めに休むと調子が良い。")],
            wiki_root=root,
            date="2026-11-17",
        )
        text = (root / "生活リズム.md").read_text(encoding="utf-8")
        assert "> updated: 2026-11-18" in text
        assert "夜は早めに休むと調子が良い。" in text


def test_notation_variant_collides_with_existing_note():
    """表記ゆれ (spacing/full-width) must append, not create a second note."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        (root / "ローカルllm-2026年.md").write_text(
            "# ローカルLLM (2026年)\n\n> tags: auto, dreaming\n> created: 2026-10-01\n"
            "> updated: 2026-10-01\n\n---\n\n既存の要約。\n",
            encoding="utf-8",
        )
        report = learning.apply_concepts(
            [_concept("ローカルllm 2026年", "2026年のローカルLLMの傾向。")],
            wiki_root=root,
            date="2026-11-18",
        )
        assert report["created"] == []
        assert report["updated"] == ["ローカルllm 2026年"]
        assert not (root / "ローカルllm2026年.md").exists()
        text = (root / "ローカルllm-2026年.md").read_text(encoding="utf-8")
        assert "> created: 2026-10-01" in text
        assert "2026年のローカルLLMの傾向。" in text


def test_build_concepts_parses_blocks_and_never_dispatches_tools():
    """The concept pass must not run tool calls: [WIKI] used to hit the WIKI tool."""
    raw = """[CONCEPT]
title: 静かな朝の集中
tags: 集中, 朝
links: [[MEMORY]], [[生活リズム]]
body: 朝は静かな環境のほうが集中しやすい。
[/CONCEPT]"""

    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        (root / "生活リズム.md").write_text("# 生活リズム\n", encoding="utf-8")

        seen = {}

        def _fake_stream(history, llm_cfg, on_token=None):
            seen["prompt"] = history[-1]["content"]
            return raw

        originals = (
            learning.dreaming._chat_stream,
            learning.dreaming.run_agent_loop,
            learning.dreaming.agent.build_system_prompt,
        )
        learning.dreaming._chat_stream = _fake_stream
        learning.dreaming.run_agent_loop = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("concept pass must not use tool dispatch")
        )
        learning.dreaming.agent.build_system_prompt = lambda: "system"
        try:
            concepts = learning.build_concepts(
                [("好み・傾向", "朝は静かな環境が良い")],
                date="2026-11-18",
                wiki_root=root,
                llm_cfg=object(),
            )
        finally:
            (
                learning.dreaming._chat_stream,
                learning.dreaming.run_agent_loop,
                learning.dreaming.agent.build_system_prompt,
            ) = originals

    assert concepts == [
        {
            "title": "静かな朝の集中",
            "tags": "集中, 朝",
            "links": "[[MEMORY]], [[生活リズム]]",
            "body": "朝は静かな環境のほうが集中しやすい。",
        }
    ]
    # Existing notes are offered to the model so it can reuse the exact title.
    assert "生活リズム" in seen["prompt"]


def test_invalid_titles_are_rejected():
    assert not learning.valid_concept_title("")
    assert not learning.valid_concept_title("2026-11-18 まとめ")
    assert not learning.valid_concept_title("MEMORY")
    assert not learning.valid_concept_title("x" * 41)
    assert learning.valid_concept_title("構造化委譲")

    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        report = learning.apply_concepts(
            [
                _concept("2026-11-18 まとめ", "日次のまとめ。"),
                _concept("MEMORY", "記憶の要約。"),
                _concept("", "空。"),
            ],
            wiki_root=root,
            date="2026-11-18",
        )
        assert report["created"] == []
        assert len(report["skipped"]) == 3
        assert not list(root.glob("*.md"))


def test_contact_details_are_dropped_from_concept_bodies():
    body = "連絡先は 090-1234-5678 です。朝は静かな環境が良い。"
    cleaned = learning.clean_concept_body(body)
    assert "090-1234-5678" not in cleaned
    assert "朝は静かな環境が良い。" in cleaned

    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        learning.apply_concepts(
            [_concept("連絡先の整理", body)], wiki_root=root, date="2026-11-18"
        )
        text = (root / "連絡先の整理.md").read_text(encoding="utf-8")
        assert "090-1234-5678" not in text
        assert "朝は静かな環境が良い。" in text


def test_empty_concepts_write_nothing_and_touch_no_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        report = learning.apply_concepts([], wiki_root=root, date="2026-11-18")
        assert report == {"created": [], "updated": [], "skipped": []}
        assert not (root / "_index.md").exists()


def test_index_ignores_archive_and_itself():
    with tempfile.TemporaryDirectory() as tmp:
        root = _wiki(Path(tmp))
        (root / ".archive").mkdir()
        (root / ".archive" / "old.md").write_text("# old", encoding="utf-8")
        learning.apply_concepts(
            [_concept("構造化委譲", "委譲の構造。")], wiki_root=root, date="2026-11-18"
        )
        index = (root / "_index.md").read_text(encoding="utf-8")
        assert "[[old]]" not in index
        assert "[[_index]]" not in index
        assert "[[構造化委譲]]" in index


def test_dry_run_never_calls_the_concept_pass():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        memory = vault / "_saku" / "memory"
        daily = vault / "01_Notes" / "Daily_Note"
        memory.mkdir(parents=True)
        daily.mkdir(parents=True)
        (daily / "2026-11-18.md").write_text(
            "---\ndate: 2026-11-18\n---\n" + "静かな朝のほうが集中できる。" * 30,
            encoding="utf-8",
        )

        originals = (
            learning.dreaming.extract_items_from_text_result,
            learning.build_concepts,
            learning.dreaming.agent.build_system_prompt,
        )
        learning.dreaming.extract_items_from_text_result = lambda *a, **k: (
            learning.dreaming.Extraction(
                status="ok", items=[("好み・傾向", "静かな朝のほうが集中できる")]
            )
        )

        def _boom(*a, **k):
            raise AssertionError("concept pass must not run in a dry run")

        learning.build_concepts = _boom
        learning.dreaming.agent.build_system_prompt = lambda: "system"
        try:
            report = learning.run_cli(
                {"learning": {"dir": str(daily), "min_chars": 10}},
                Path(tmp),
                memory,
                apply=False,
                verbose=False,
            )
        finally:
            (
                learning.dreaming.extract_items_from_text_result,
                learning.build_concepts,
                learning.dreaming.agent.build_system_prompt,
            ) = originals

        assert report["items"] == 1
        assert not (memory / "wiki").exists()


def test_apply_stage_writes_wiki_through_run_cli():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        memory = vault / "_saku" / "memory"
        daily = vault / "01_Notes" / "Daily_Note"
        memory.mkdir(parents=True)
        daily.mkdir(parents=True)
        (daily / "2026-11-18.md").write_text(
            "---\ndate: 2026-11-18\n---\n" + "静かな朝のほうが集中できる。" * 30,
            encoding="utf-8",
        )

        originals = (
            learning.dreaming.extract_items_from_text_result,
            learning.build_concepts,
            learning.dreaming.agent.build_system_prompt,
        )
        learning.dreaming.extract_items_from_text_result = lambda *a, **k: (
            learning.dreaming.Extraction(
                status="ok", items=[("好み・傾向", "静かな朝のほうが集中できる")]
            )
        )
        learning.build_concepts = lambda *a, **k: [
            _concept("静かな朝の集中", "朝は静かな環境のほうが集中しやすい。")
        ]
        learning.dreaming.agent.build_system_prompt = lambda: "system"
        try:
            report = learning.run_cli(
                {"learning": {"dir": str(daily), "min_chars": 10}},
                Path(tmp),
                memory,
                apply=True,
                verbose=False,
            )
        finally:
            (
                learning.dreaming.extract_items_from_text_result,
                learning.build_concepts,
                learning.dreaming.agent.build_system_prompt,
            ) = originals

        assert report["wiki_created"] == ["静かな朝の集中"]
        note = (memory / "wiki" / "静かな朝の集中.md").read_text(encoding="utf-8")
        assert "> source: daily/2026-11-18" in note
        assert (memory / "wiki" / "_index.md").is_file()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} learn-wiki tests PASSED!")


if __name__ == "__main__":
    run_tests()
