#!/usr/bin/env python3
"""
Unit tests for the evaluation wiring of issue #103:

- saku.growth: longitudinal snapshot capture/compare, the weekly snapshot
  runner, and the Owner's evidence digest.
- saku.followups: the structured reflection follow-up record and the
  [FOLLOWUPS] block parser.
- saku.reflection: open follow-ups reach the prompt; the reply's block is
  applied to the record (LLM mocked).
- saku.daemon: the weekly growth-snapshot gate (runner mocked).

No LLM is involved and every write lands in a temporary directory.
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as saku_config
from saku import core as agent
from saku import daemon as daemon_mod
from saku import followups
from saku import growth
from saku import reflection
from _isolate_logging import isolate_daemon_logging

# #42: daemon sets up file logging at import time, against the Owner's real
# state/saku.log. Redirect it before any test exercises the logger.
isolate_daemon_logging(daemon_mod.logger)

_PASS = 0
_FAIL = 0


def _patch(module, name, value):
    original = getattr(module, name)
    setattr(module, name, value)
    return original


def _restore(module, name, original):
    setattr(module, name, original)


def _loop_result(text: str):
    from saku.agent_loop import LoopResult

    return LoopResult(
        visible=text, thinking="", last_raw=text, history=[], action_taken=True, turns=1
    )


def _make_memory(root: Path) -> None:
    (root / "principles").mkdir(parents=True)
    (root / "principles" / "a.md").write_text("# a", encoding="utf-8")
    (root / "principles" / "b.md").write_text("# b", encoding="utf-8")
    (root / "journal").mkdir()
    (root / "journal" / "2026-11-19.md").write_text("記録", encoding="utf-8")
    (root / "MEMORY.md").write_text(
        "# MEMORY\n\n## 重要な学び\n- one\n- two\n\n## 好み・傾向\n- three\n",
        encoding="utf-8",
    )
    (root / "meta.md").write_text(
        "## 現在の状態\n- 🌑 New Moon。Owner 主導で学習中。\n\n## 最近の出来事\n- x\n",
        encoding="utf-8",
    )


# ── capture / compare ───────────────────────────────────

def test_capture_snapshot_counts_files_and_sections():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_memory(root)
        snap = growth.capture_snapshot(root)
    assert snap["files"]["principles"] == 2, snap["files"]
    assert snap["files"]["journal"] == 1
    assert snap["files"]["skills"] == 0  # missing dir reads as 0, not an error
    assert snap["memory"]["items_by_section"]["重要な学び"] == 2
    assert snap["memory"]["items_by_section"]["好み・傾向"] == 1
    assert snap["meta"]["chars"] > 0


def test_compare_snapshots_reports_deltas_only():
    before = {
        "captured_at": "2026-11-15 03:00",
        "memory": {"chars": 100, "items_by_section": {"重要な学び": 2}},
        "meta": {"chars": 50, "items_by_section": {}},
        "files": {"principles": 2, "journal": 5},
    }
    after = {
        "captured_at": "2026-11-22 03:00",
        "memory": {"chars": 160, "items_by_section": {"重要な学び": 4}},
        "meta": {"chars": 50, "items_by_section": {}},
        "files": {"principles": 3, "journal": 5},
    }
    text = "\n".join(growth.compare_snapshots(before, after))
    assert "MEMORY.md: 100 -> 160 chars (+60)" in text
    assert "重要な学び: 2 -> 4 (+2)" in text
    assert "principles: 2 -> 3 (+1)" in text
    assert "journal" not in text, "unchanged counts must not be reported"


def test_find_phase_reads_the_meta_prose():
    assert "New Moon" in growth.find_phase("## 現在の状態\n- 🌑 New Moon で学習中\n")
    assert growth.find_phase("## 現在の状態\n- 特に記載なし\n") == ""


# ── snapshot runner + digest ────────────────────────────

def test_run_growth_snapshot_first_run_has_no_baseline():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_memory(root)
        result = growth.run_growth_snapshot(datetime(2026, 11, 22, 3, 30), root, {})
        snap_path = root / "growth" / "snapshots" / "2026-11-22.json"
        digest_path = root / "growth" / "digest.md"
        assert result["written"] and snap_path.exists() and digest_path.exists()
        assert result["previous_date"] == ""
        digest = digest_path.read_text(encoding="utf-8")
        assert "今回が初回" in digest
        assert "New Moon" in digest  # the phase line is quoted from meta.md
        json.loads(snap_path.read_text(encoding="utf-8"))  # valid JSON


def test_run_growth_snapshot_compares_and_lists_open_followups():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_memory(root)
        # baseline: last week's snapshot, with fewer principles
        snap_dir = root / "growth" / "snapshots"
        snap_dir.mkdir(parents=True)
        baseline = growth.capture_snapshot(root)
        baseline["files"]["principles"] = 1
        (snap_dir / "2026-11-15.json").write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
        )
        # one open follow-up in the record
        store = followups.empty_store()
        followups.add_items(store, ["検索前に索引を確認する"], "2026-11-16")
        followups.save_store(root / "state" / "followups.json", store)

        result = growth.run_growth_snapshot(datetime(2026, 11, 22, 3, 30), root, {})
        assert result["previous_date"] == "2026-11-15"
        assert result["open_followups"] == 1
        digest = (root / "growth" / "digest.md").read_text(encoding="utf-8")
        assert "principles: 1 -> 2 (+1)" in digest
        assert "検索前に索引を確認する" in digest
        assert "2026-11-15.json" in digest and "2026-11-22.json" in digest


# ── follow-ups record ───────────────────────────────────

def test_followups_roundtrip_and_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state" / "followups.json"
        store = followups.load_store(path)  # missing -> empty, no error
        assert store["items"] == []
        added = followups.add_items(store, ["弱点A", "弱点B"], "2026-11-19")
        assert [it["id"] for it in added] == [1, 2]
        followups.save_store(path, store)
        loaded = followups.load_store(path)
        assert [it["text"] for it in followups.open_items(loaded)] == ["弱点A", "弱点B"]


def test_followups_add_dedupes_open_but_not_closed():
    store = followups.empty_store()
    followups.add_items(store, ["弱点A"], "2026-11-19")
    assert followups.add_items(store, ["弱点A"], "2026-11-20") == [], "open duplicate"
    followups.record_check(store, [1], "2026-11-20")  # close it
    re_added = followups.add_items(store, ["弱点A"], "2026-11-21")
    assert len(re_added) == 1 and re_added[0]["id"] == 2, "a recurrence is a new entry"


def test_followups_record_check_counts_and_closes():
    store = followups.empty_store()
    followups.add_items(store, ["弱点A", "弱点B"], "2026-11-19")
    followups.record_check(store, [1], "2026-11-20")
    a, b = store["items"]
    assert a["status"] == "done" and a["closed"] == "2026-11-20"
    assert b["status"] == "open" and b["checks"] == 1 and b["last_checked"] == "2026-11-20"
    followups.record_check(store, [], "2026-11-21")
    assert b["checks"] == 2, "still-open entries keep accumulating checks"


def test_followups_corrupt_file_reads_as_empty():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "followups.json"
        path.write_text("{not json", encoding="utf-8")
        assert followups.load_store(path)["items"] == []


def test_parse_followup_block_variants():
    text = "本文\n\n[FOLLOWUPS]\ndone: 3\ndone: #4\nstill-open: 5\nnew: 課題X\nnew: 課題Y\n関係ない行\n[/FOLLOWUPS]\n[REFLECTION_DONE]"
    report = followups.parse_followup_block(text)
    assert report["done"] == [3, 4], report
    assert report["new"] == ["課題X", "課題Y"], report
    # missing closing marker: the block runs to the end and still parses
    report2 = followups.parse_followup_block("本文\n[FOLLOWUPS]\nnew: 課題Z")
    assert report2["new"] == ["課題Z"]
    # no block at all -> empty report, nothing invented
    assert followups.parse_followup_block("ただの本文") == {"done": [], "new": []}
    # the new-item cap is enforced even if the model floods the block
    flooded = "[FOLLOWUPS]\n" + "\n".join(f"new: 課題{i}" for i in range(9))
    assert len(followups.parse_followup_block(flooded)["new"]) == followups.MAX_NEW_PER_REFLECTION


def test_render_open_items_shows_id_age_and_checks():
    store = followups.empty_store()
    followups.add_items(store, ["弱点A"], "2026-11-10")
    followups.record_check(store, [], "2026-11-12")
    rendered = followups.render_open_items(store, "2026-11-19")
    assert "#1" in rendered and "弱点A" in rendered
    assert "9日経過" in rendered and "消化チェック1回" in rendered


# ── reflection wiring ───────────────────────────────────

def test_reflection_prompt_includes_followups_section_only_when_given():
    plain = reflection.build_reflection_prompt("2026-11-19", "j", "m")
    assert "[FOLLOWUPS]" not in plain
    section = reflection.build_followups_section("- #1: 弱点A（2026-11-10〜）")
    with_fu = reflection.build_reflection_prompt(
        "2026-11-19", "j", "m", followups_section=section
    )
    assert "未消化のフォローアップ" in with_fu and "弱点A" in with_fu
    assert "[FOLLOWUPS]" in with_fu


def _run_reflection_with_fake_loop(root: Path, reply: str, raw_cfg: dict):
    """Run reflection.run_reflection against a temp memory root, LLM mocked."""
    (root / "journal").mkdir(parents=True, exist_ok=True)
    (root / "journal" / "2026-11-19.md").write_text("今日の記録", encoding="utf-8")
    seen = {}

    def fake_loop(history, *args, **kwargs):
        seen["prompt"] = history[-1]["content"]
        return _loop_result(reply)

    patches = [
        (reflection, "SAKU_ROOT", _patch(reflection, "SAKU_ROOT", root)),
        (reflection, "_run_agent_loop", _patch(reflection, "_run_agent_loop", fake_loop)),
        (agent, "MEMORY_ROOT", _patch(agent, "MEMORY_ROOT", root)),
        (agent, "SAKU_ROOT", _patch(agent, "SAKU_ROOT", root)),
        (agent, "_cfg", _patch(agent, "_cfg", raw_cfg)),
    ]
    try:
        reflection.run_reflection("2026-11-19")
    finally:
        for module, name, original in patches:
            _restore(module, name, original)
    return seen


def test_reflection_applies_followup_block_to_the_record():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # pre-seed one open follow-up the reply will close
        store = followups.empty_store()
        followups.add_items(store, ["既存の弱点"], "2026-11-15")
        followups.save_store(root / "state" / "followups.json", store)

        reply = (
            "振り返り本文\n\n[FOLLOWUPS]\ndone: 1\nnew: 検索前に索引を確認する\n[/FOLLOWUPS]"
        )
        seen = _run_reflection_with_fake_loop(root, reply, {})

        # the open item was shown to the reflection
        assert "既存の弱点" in seen["prompt"] and "#1" in seen["prompt"]
        store = followups.load_store(root / "state" / "followups.json")
        by_id = {it["id"]: it for it in store["items"]}
        assert by_id[1]["status"] == "done" and by_id[1]["closed"] == "2026-11-19"
        assert by_id[2]["status"] == "open" and by_id[2]["text"] == "検索前に索引を確認する"
        assert by_id[2]["created"] == "2026-11-19"


def test_reflection_without_block_leaves_record_but_counts_the_check():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = followups.empty_store()
        followups.add_items(store, ["既存の弱点"], "2026-11-15")
        followups.save_store(root / "state" / "followups.json", store)

        _run_reflection_with_fake_loop(root, "ブロック無しの返信", {})
        store = followups.load_store(root / "state" / "followups.json")
        item = store["items"][0]
        assert item["status"] == "open" and item["checks"] == 1


def test_reflection_followups_can_be_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reply = "[FOLLOWUPS]\nnew: 登録されないはずの課題\n[/FOLLOWUPS]"
        seen = _run_reflection_with_fake_loop(
            root, reply, {"growth": {"followups_enabled": False}}
        )
        assert "[FOLLOWUPS]" not in seen["prompt"]
        assert not (root / "state" / "followups.json").exists()


# ── daemon gate ─────────────────────────────────────────

def _run_snapshot_gate(now, state_in=None, gcfg=None, stopped=False):
    calls = []
    saved = []
    notices = []

    class _Growth:
        @staticmethod
        def run_growth_snapshot(n):
            calls.append(n)
            return {
                "date": n.strftime("%Y-%m-%d"),
                "path": "growth/snapshots/x.json",
                "written": True,
                "previous_date": "",
                "digest_path": "growth/digest.md",
                "open_followups": 0,
            }

    state = dict(state_in or {})
    originals = [
        (daemon_mod, "growth", _patch(daemon_mod, "growth", _Growth)),
        (daemon_mod, "load_chat_state", _patch(daemon_mod, "load_chat_state", lambda: state)),
        (daemon_mod, "save_chat_state", _patch(daemon_mod, "save_chat_state", lambda s: saved.append(dict(s)))),
        (daemon_mod, "killswitch_stopped", _patch(daemon_mod, "killswitch_stopped", lambda: stopped)),
        (daemon_mod, "notify_webui", _patch(daemon_mod, "notify_webui", lambda m: notices.append(m))),
        (saku_config, "load_growth_config",
         _patch(saku_config, "load_growth_config", lambda c: gcfg or saku_config.GrowthConfig())),
    ]
    try:
        daemon_mod.check_and_run_growth_snapshot(now)
    finally:
        for module, name, original in originals:
            _restore(module, name, original)
    return calls, saved, notices, state


def test_snapshot_gate_fires_on_sunday_after_hour():
    # 2026-11-22 is a Sunday.
    calls, saved, notices, state = _run_snapshot_gate(datetime(2026, 11, 22, 10, 0))
    assert len(calls) == 1
    assert state.get("last_growth_snapshot_date") == "2026-11-22"
    assert saved and len(notices) == 1


def test_snapshot_gate_respects_weekday_hour_and_once_per_day():
    calls, *_ = _run_snapshot_gate(datetime(2026, 11, 23, 10, 0))  # Monday
    assert calls == []
    calls, *_ = _run_snapshot_gate(datetime(2026, 11, 22, 2, 0))  # Sunday, too early
    assert calls == []
    calls, *_ = _run_snapshot_gate(
        datetime(2026, 11, 22, 10, 0), state_in={"last_growth_snapshot_date": "2026-11-22"}
    )
    assert calls == []


def test_snapshot_gate_disabled_and_killswitch():
    calls, *_ = _run_snapshot_gate(
        datetime(2026, 11, 22, 10, 0), gcfg=saku_config.GrowthConfig(snapshot_enabled=False)
    )
    assert calls == []
    calls, saved, _, state = _run_snapshot_gate(datetime(2026, 11, 22, 10, 0), stopped=True)
    assert calls == [] and saved == [], "a stopped day must not be recorded as run"
    assert "last_growth_snapshot_date" not in state


def run_tests():
    global _PASS, _FAIL
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
            print(f"    -> PASS: {fn.__name__}")
            _PASS += 1
        except Exception as e:
            print(f"    -> FAIL: {fn.__name__}: {e}")
            _FAIL += 1
    print(f"[*] All {_PASS + _FAIL} growth wiring tests {'PASSED!' if _FAIL == 0 else f'FAILED ({_FAIL})'}")
    return _FAIL == 0


if __name__ == "__main__":
    run_tests()
