"""Longitudinal growth snapshots: the measurement half of issue #103.

The evaluation instruments existed (evals/longitudinal/: a snapshot script
and a compare script, both manual) but nothing ran them, so when a stage
promotion (New Moon -> Half Moon) was discussed there was no time series to
point at -- only impressions. This module wires the loop:

  * ``capture_snapshot`` / ``compare_snapshots`` are the single implementation
    of the snapshot format; the evals scripts import them from here, so a
    daemon-taken snapshot and a manual one stay comparable.
  * ``run_growth_snapshot`` is what the daemon calls weekly: it writes a
    dated JSON snapshot under ``[growth] snapshot_dir`` and refreshes
    ``digest.md`` next to it.
  * The digest is the Owner's evidence package: latest deltas against the
    previous snapshot, the snapshot history, the phase line recorded in
    meta.md, and the still-open reflection follow-ups (saku/followups.py).
    It presents evidence only -- promotion itself stays the Owner's call.
"""

import json
from datetime import datetime
from pathlib import Path

from saku import config as saku_config
from saku import core as agent
from saku import followups as saku_followups

_PHASE_TOKENS = ("🌑", "🌓", "🌕", "New Moon", "Half Moon", "Full Moon")


def _count_md(d: Path) -> int:
    return len(list(d.glob("*.md"))) if d.is_dir() else 0


def _section_items(content: str) -> dict[str, int]:
    """Count list items under each ## section in a Markdown file."""
    counts: dict[str, int] = {}
    current = None
    for line in content.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            counts[current] = 0
        elif current and line.strip().startswith("- "):
            counts[current] = counts.get(current, 0) + 1
    return counts


def capture_snapshot(memory_root: Path) -> dict:
    """Capture the growth state of ``memory_root`` (the evals JSON format)."""
    memory = memory_root / "MEMORY.md"
    meta = memory_root / "meta.md"
    mem_content = memory.read_text(encoding="utf-8") if memory.exists() else ""
    meta_content = meta.read_text(encoding="utf-8") if meta.exists() else ""

    wiki_notes = [p for p in (memory_root / "wiki").glob("*.md")] if (memory_root / "wiki").is_dir() else []

    return {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "memory": {
            "chars": len(mem_content),
            "items_by_section": _section_items(mem_content),
        },
        "meta": {
            "chars": len(meta_content),
            "items_by_section": _section_items(meta_content),
        },
        "files": {
            "principles": _count_md(memory_root / "principles"),
            "skills": _count_md(memory_root / "skills"),
            "wiki_notes": len(wiki_notes),
            "journal": _count_md(memory_root / "journal"),
            "monologue": _count_md(memory_root / "monologue"),
            "children": _count_md(memory_root / "children"),
            "tools": _count_md(memory_root / "tools"),
        },
    }


def compare_snapshots(before: dict, after: dict) -> list[str]:
    """Human-readable delta lines between two snapshots (oldest first)."""
    lines: list[str] = []
    b, a = before, after
    lines.append(f"captured: {b.get('captured_at')} -> {a.get('captured_at')}")
    lines.append("")

    for key, label in (("memory", "MEMORY.md"), ("meta", "meta.md")):
        db = b.get(key, {}).get("chars", 0)
        da = a.get(key, {}).get("chars", 0)
        lines.append(f"{label}: {db} -> {da} chars ({da - db:+d})")
        bs = b.get(key, {}).get("items_by_section", {})
        as_ = a.get(key, {}).get("items_by_section", {})
        for section in sorted(set(bs) | set(as_)):
            delta = as_.get(section, 0) - bs.get(section, 0)
            if delta:
                lines.append(f"  {section}: {bs.get(section, 0)} -> {as_.get(section, 0)} ({delta:+d})")
    lines.append("")

    bf, af = b.get("files", {}), a.get("files", {})
    for key in sorted(set(bf) | set(af)):
        db, da = bf.get(key, 0), af.get(key, 0)
        if da != db:
            lines.append(f"{key}: {db} -> {da} ({da - db:+d})")
    return lines


def find_phase(meta_content: str) -> str:
    """Best-effort phase line from meta.md (the phase is prose, not a field).

    Returns the first line naming a moon phase, trimmed; "" when meta.md
    records none. The digest labels it as a quote, never as a measurement.
    """
    for line in meta_content.splitlines():
        if any(token in line for token in _PHASE_TOKENS):
            return line.strip().lstrip("#-* ").strip()[:100]
    return ""


def _snapshot_files(snapshots_dir: Path) -> list[Path]:
    if not snapshots_dir.is_dir():
        return []
    return sorted(snapshots_dir.glob("*.json"))


def _load_snapshot(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def render_digest(
    date_str: str,
    snapshot: dict,
    previous: dict | None,
    previous_name: str,
    snapshot_names: list[str],
    phase: str,
    open_followups: list[dict],
) -> str:
    """Render digest.md: the Owner-readable evidence package for growth."""
    lines = [
        "# 成長ダイジェスト（自動生成）",
        "",
        f"最終スナップショット: {date_str}（{snapshot.get('captured_at', '')}）",
        "",
        "段階昇格の判断材料として、測定できた事実だけを並べます。昇格の判断は Owner が行います。",
        "",
    ]
    if phase:
        lines += [f"- 現在のフェーズ（meta.md の記述）: {phase}", ""]
    lines += ["## 前回スナップショットとの差分", ""]
    if previous is not None:
        lines += [f"（比較対象: {previous_name}）", ""]
        lines += [f"- {line}" if line else "" for line in compare_snapshots(previous, snapshot)]
    else:
        lines.append("比較対象の前回スナップショットはまだありません（今回が初回です）。")
    lines += ["", "## 未消化のフォローアップ（reflection の課題記録）", ""]
    if open_followups:
        for item in open_followups:
            lines.append(
                f"- #{item.get('id')}: {item.get('text', '')}"
                f"（{item.get('created', '')}〜、消化チェック{item.get('checks', 0)}回）"
            )
    else:
        lines.append("未消化のフォローアップはありません。")
    lines += ["", "## スナップショット履歴", ""]
    lines += [f"- {name}" for name in snapshot_names] or ["- （なし）"]
    lines.append("")
    return "\n".join(lines)


def run_growth_snapshot(now: datetime | None = None, memory_root: Path | None = None,
                        raw_cfg: dict | None = None) -> dict:
    """Take one dated snapshot and refresh the digest. Returns what happened.

    Pure measurement: no LLM is involved. A snapshot for today that already
    exists is overwritten (same-day reruns stay idempotent); the comparison
    baseline is the newest snapshot dated before today.
    """
    now = now or datetime.now()
    raw_cfg = agent._cfg if raw_cfg is None else raw_cfg
    memory_root = agent.MEMORY_ROOT if memory_root is None else memory_root
    date_str = now.strftime("%Y-%m-%d")

    snap_dir = saku_config.resolve_snapshot_dir(raw_cfg, memory_root)
    snapshots_dir = snap_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    previous_path = None
    previous = None
    for path in _snapshot_files(snapshots_dir):
        if path.stem < date_str:
            previous_path = path
            previous = _load_snapshot(path)

    snapshot = capture_snapshot(memory_root)
    out_path = snapshots_dir / f"{date_str}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] Growth snapshot saved to {out_path}")

    # The digest also carries the open follow-ups. A broken follow-ups
    # record must not block the snapshot, so failures degrade to "none".
    open_items: list[dict] = []
    try:
        store = saku_followups.load_store(saku_config.resolve_followups_file(raw_cfg, memory_root))
        open_items = saku_followups.open_items(store)
    except Exception as e:
        print(f"[!] Growth digest: follow-ups unreadable ({e}); continuing without them.")

    meta_path = memory_root / "meta.md"
    meta_content = meta_path.read_text(encoding="utf-8") if meta_path.exists() else ""

    names = [p.name for p in _snapshot_files(snapshots_dir)]
    digest = render_digest(
        date_str,
        snapshot,
        previous,
        previous_path.name if previous_path else "",
        names,
        find_phase(meta_content),
        open_items,
    )
    digest_path = snap_dir / "digest.md"
    digest_path.write_text(digest, encoding="utf-8")
    print(f"[*] Growth digest updated at {digest_path}")

    return {
        "date": date_str,
        "path": str(out_path),
        "written": True,
        "previous_date": previous_path.stem if previous_path else "",
        "digest_path": str(digest_path),
        "open_followups": len(open_items),
    }
