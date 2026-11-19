#!/usr/bin/env python3
"""Tests for clipping classification (#72): saku.sort + saku.do + SORT_NOTES.

Everything runs in temporary directories with a stubbed LLM. The Owner's vault
is never touched: "vault" here is a temp directory shaped like
``<vault>/_saku/memory``.
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku import do as do_mod
from saku import sort as sort_mod


def _vault(tmp: Path) -> tuple[Path, Path]:
    """Make <tmp>/vault/{Clippings,02_Reference/<folders>,_saku/memory}."""
    vault = tmp / "vault"
    memory = vault / "_saku" / "memory"
    memory.mkdir(parents=True)
    (vault / "Clippings").mkdir()
    for folder in ("IT", "Life"):
        (vault / "02_Reference" / folder).mkdir(parents=True)
    return vault, memory


def _paths(tmp: Path):
    vault, memory = _vault(tmp)
    cfg = {}
    return cfg, memory, sort_mod.resolve_paths(cfg, tmp, memory)


def _fake_loop(raw: str, seen: dict | None = None):
    def _run(*_a, **_k):
        if seen is not None:
            seen.update(_k)
        return SimpleNamespace(last_raw=raw, visible="", thinking="")

    return _run


def test_resolve_paths_defaults_to_the_vault_that_owns_memory():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, memory, paths = _paths(Path(tmp))
        assert paths.source_dir == (Path(tmp) / "vault" / "Clippings").resolve()
        assert paths.target_dir == (Path(tmp) / "vault" / "02_Reference").resolve()


def test_resolve_paths_reads_config_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        cfg = {"sort": {"source_dir": "00_Inbox", "target_dir": "01_Notes",
                        "vault_root": str(vault)}}
        paths = sort_mod.resolve_paths(cfg, Path(tmp), memory)
        assert paths.source_dir == (vault / "00_Inbox").resolve()
        assert paths.target_dir == (vault / "01_Notes").resolve()


def test_existing_folders_skips_files_and_hidden_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, _memory, paths = _paths(Path(tmp))
        (paths.target_dir / ".hidden").mkdir()
        (paths.target_dir / "a-file.md").write_text("x", encoding="utf-8")
        assert sort_mod.existing_folders(paths.target_dir) == ["IT", "Life"]


def test_classify_parses_blocks_and_marks_unknown_folders_for_review():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, _memory, paths = _paths(Path(tmp))
        first = paths.source_dir / "a.md"
        second = paths.source_dir / "b.md"
        first.write_text("本文A", encoding="utf-8")
        second.write_text("本文B", encoding="utf-8")

        raw = """[CLASSIFY]
file: a.md
folder: IT
confidence: 0.92
reason: セキュリティ記事
[/CLASSIFY]
[CLASSIFY]
file: b.md
folder: 新しいフォルダ
confidence: 0.8
reason: 不明
[/CLASSIFY]"""

        seen: dict = {}
        original_loop = sort_mod.run_agent_loop
        original_prompt = sort_mod.agent.build_system_prompt
        sort_mod.run_agent_loop = _fake_loop(raw, seen)
        sort_mod.agent.build_system_prompt = lambda: "system"
        try:
            items = sort_mod.classify(
                [first, second], ["IT", "Life"], llm_cfg=object()
            )
        finally:
            sort_mod.run_agent_loop = original_loop
            sort_mod.agent.build_system_prompt = original_prompt

        # The classification is read, not watched: raw [CLASSIFY] blocks must not
        # be streamed to stdout before the CLI's own table.
        assert callable(seen.get("on_visible"))

        by_name = {i["file"]: i for i in items}
        assert by_name["a.md"]["folder"] == "IT"
        assert by_name["a.md"]["confidence"] == 0.92
        # A folder that does not exist is never invented.
        assert by_name["b.md"]["folder"] == sort_mod.REVIEW_FOLDER


def test_classify_marks_unanswered_files_for_review():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, _memory, paths = _paths(Path(tmp))
        note = paths.source_dir / "only.md"
        note.write_text("x", encoding="utf-8")

        original_loop = sort_mod.run_agent_loop
        original_prompt = sort_mod.agent.build_system_prompt
        sort_mod.run_agent_loop = _fake_loop("[NO_CLASSIFY]")
        sort_mod.agent.build_system_prompt = lambda: "system"
        try:
            items = sort_mod.classify([note], ["IT"], llm_cfg=object())
        finally:
            sort_mod.run_agent_loop = original_loop
            sort_mod.agent.build_system_prompt = original_prompt

        assert items == [
            {"file": "only.md", "folder": sort_mod.REVIEW_FOLDER,
             "confidence": 0.0, "reason": "no answer"}
        ]


def test_apply_moves_only_confident_entries_and_backs_them_up():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, memory, paths = _paths(Path(tmp))
        confident = paths.source_dir / "keep-me.md"
        unsure = paths.source_dir / "maybe.md"
        confident.write_text("ok", encoding="utf-8")
        unsure.write_text("hmm", encoding="utf-8")

        proposal = sort_mod.Proposal(
            stamp="20260101-000000",
            review_below=0.7,
            source_dir=str(paths.source_dir),
            target_dir=str(paths.target_dir),
            items=[
                {"file": "keep-me.md", "folder": "IT", "confidence": 0.9},
                {"file": "maybe.md", "folder": "IT", "confidence": 0.4},
            ],
        )
        report = sort_mod.apply_proposal(memory, proposal)

        assert report["moved"] == [("keep-me.md", "IT/keep-me.md")]
        assert report["skipped"] == [("maybe.md", "needs review")]
        assert (paths.target_dir / "IT" / "keep-me.md").is_file()
        assert not confident.exists()
        # The unsure file stays put: a proposal is a suggestion, not a command.
        assert unsure.is_file()
        assert (memory / "state" / "backup" / "20260101-000000" / "sort" / "keep-me.md").is_file()


def test_apply_never_overwrites_and_never_creates_folders():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, memory, paths = _paths(Path(tmp))
        existing = paths.target_dir / "IT" / "article.md"
        existing.write_text("original", encoding="utf-8")
        (paths.source_dir / "article.md").write_text("new", encoding="utf-8")

        proposal = sort_mod.Proposal(
            stamp="20260101-000001",
            source_dir=str(paths.source_dir),
            target_dir=str(paths.target_dir),
            items=[
                {"file": "article.md", "folder": "IT", "confidence": 0.95},
                {"file": "article.md", "folder": "Nowhere", "confidence": 0.95},
            ],
        )
        report = sort_mod.apply_proposal(memory, proposal)

        assert report["moved"] == [("article.md", "IT/article-2.md")]
        assert existing.read_text(encoding="utf-8") == "original"
        assert not (paths.target_dir / "Nowhere").exists()
        assert any("folder not found" in why for _name, why in report["skipped"])


def test_apply_twice_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, memory, paths = _paths(Path(tmp))
        (paths.source_dir / "x.md").write_text("x", encoding="utf-8")
        proposal = sort_mod.Proposal(
            stamp="20260101-000002",
            source_dir=str(paths.source_dir),
            target_dir=str(paths.target_dir),
            items=[{"file": "x.md", "folder": "IT", "confidence": 0.9}],
        )
        saved = sort_mod.save_proposal(memory, proposal)
        sort_mod.apply_proposal(memory, proposal)
        sort_mod.mark_applied(memory, proposal)
        again = sort_mod.load_proposal(saved)
        report = sort_mod.apply_proposal(memory, again)
        assert report["moved"] == []
        assert report["skipped"] == [("*", "proposal already applied")]


def test_proposal_roundtrip_preserves_items():
    with tempfile.TemporaryDirectory() as tmp:
        _cfg, memory, paths = _paths(Path(tmp))
        proposal = sort_mod.Proposal(
            stamp="20260101-000003",
            created_at="2026-01-01 00:00:03",
            review_below=0.6,
            source_dir=str(paths.source_dir),
            target_dir=str(paths.target_dir),
            items=[{"file": "a.md", "folder": "IT", "confidence": 0.75}],
        )
        path = sort_mod.save_proposal(memory, proposal)
        loaded = sort_mod.load_proposal(path)
        assert loaded.to_dict() == proposal.to_dict()
        assert sort_mod.latest_proposal(memory) == path


def test_sort_notes_tool_saves_a_proposal_but_moves_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        note = vault / "Clippings" / "c.md"
        note.write_text("content", encoding="utf-8")

        original_loop = sort_mod.run_agent_loop
        original_prompt = sort_mod.agent.build_system_prompt
        original_cfg = cfg_mod.load_config
        sort_mod.agent.build_system_prompt = lambda: "system"
        sort_mod.run_agent_loop = _fake_loop(
            "[CLASSIFY]\nfile: c.md\nfolder: Life\nconfidence: 0.9\nreason: 生活\n[/CLASSIFY]"
        )
        from saku.tools import sort_notes

        try:
            # load_config and the tool both resolve [sort] through this temp tombstone.
            cfg = {"sort": {"vault_root": str(vault)}}
            cfg_mod.load_config = lambda: (cfg, Path(tmp))
            out = sort_notes.run(memory)
        finally:
            sort_mod.run_agent_loop = original_loop
            sort_mod.agent.build_system_prompt = original_prompt

        assert "proposal saved" in out
        assert "nothing moved" in out
        assert note.is_file()
        assert list((memory / "state" / "sort").glob("*.json"))


def test_sort_notes_apply_is_refused_unless_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        vault, memory = _vault(Path(tmp))
        cfg = {"sort": {"vault_root": str(vault), "allow_agent_apply": False}}
        original = cfg_mod.load_config
        cfg_mod.load_config = lambda: (cfg, Path(tmp))
        from saku.tools import sort_notes

        try:
            out = sort_notes.run(memory, apply="true")
        finally:
            cfg_mod.load_config = original
        assert out.startswith("[ERROR]") and "allow_agent_apply" in out


def test_do_resolves_known_requests_only():
    assert do_mod.resolve("クリッピングを分類して") == "sort"
    assert do_mod.resolve("bunrui shite") == "sort"
    assert do_mod.resolve("sort the clippings") == "sort"
    assert do_mod.resolve("メモリを掃除して") == "tidy"
    assert do_mod.resolve("成長の状態を見せて") == "status"
    assert do_mod.resolve("今日の天気は？") is None
    assert do_mod.resolve("") is None


def test_do_forwards_apply_only_when_asked():
    calls = []
    from saku import cli

    original_sort = sort_mod.main
    sort_mod.main = lambda argv=None: calls.append(list(argv or [])) or 0
    try:
        assert do_mod.main(["クリッピングを分類"]) == 0
        assert do_mod.main(["クリッピングを分類", "--apply"]) == 0
    finally:
        sort_mod.main = original_sort

    assert calls == [[], ["--apply"]]
    # The CLI routes the same verb.
    assert "sort" in cli.__doc__


def test_do_returns_nonzero_for_unknown_requests():
    assert do_mod.main(["今日の天気は？"]) == 2


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} sort tests PASSED!")


if __name__ == "__main__":
    run_tests()
