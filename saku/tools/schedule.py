"""SCHEDULE tool: natural-language schedule via chat.

Usage:
- Add: [[SCHEDULE op="add" when="2026-08-29 10:00" task="レポートを作成する"]]
- List: [[SCHEDULE op="list"]]
- Remove: [[SCHEDULE op="remove" id="abc123"]]

Stores entries in state/schedule.json. The daemon polls this file and
executes due tasks. `when` can be a datetime (YYYY-MM-DD HH:MM) or a
natural phrase kept as-is for later parsing.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path


def _schedule_file(base: Path) -> Path:
    return base / "state" / "schedule.json"


def _load(base: Path) -> list[dict]:
    p = _schedule_file(base)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(base: Path, items: list[dict]) -> None:
    p = _schedule_file(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def _parse_when(s: str) -> str:
    s = s.strip()
    # Try to normalize YYYY-MM-DD HH:MM
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return s


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    op = kwargs.get("op", "").strip() or "add"
    if op == "list":
        items = _load(base)
        if not items:
            return "No scheduled tasks."
        lines = []
        for it in items:
            lines.append(f"- [{it.get('id','')}] {it.get('when','')} : {it.get('task','')} (status: {it.get('status','pending')})")
        return "\n".join(lines)

    if op == "remove":
        sid = kwargs.get("id", "").strip() or body.strip()
        if not sid:
            return "[ERROR] id required for remove"
        items = _load(base)
        before = len(items)
        items = [x for x in items if x.get("id") != sid]
        if len(items) == before:
            return f"[ERROR] not found: {sid}"
        _save(base, items)
        return f"[OK] removed {sid}"

    # add (default)
    when = kwargs.get("when", "").strip()
    task = kwargs.get("task", "").strip() or body.strip()
    if not task:
        return "[ERROR] task required (use task=\"...\" or body)"
    if not when:
        return "[ERROR] when required (e.g. when=\"2026-08-29 10:00\")"
    when_norm = _parse_when(when)
    sid = uuid.uuid4().hex[:8]
    entry = {
        "id": sid,
        "when": when_norm,
        "when_raw": when,
        "task": task,
        "status": "pending",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    items = _load(base)
    items.append(entry)
    _save(base, items)
    return f"[OK] scheduled {sid}: {when_norm} -> {task}"
