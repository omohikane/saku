"""Reflection follow-ups: findings that must not evaporate (issue #103).

The nightly reflection identifies weaknesses and intentions, but until now
they lived only in journal prose and in meta.md's free-form lists. Nothing
re-checked them, so the same weakness was re-discovered weeks later as if
it were new. This module owns a small structured record (JSON, written by
code -- never by the model's file tools) with one entry per follow-up:

    {"id": 3, "text": "...", "created": "2026-11-19", "status": "open",
     "last_checked": "2026-11-20", "checks": 2, "closed": ""}

The flow is a loop, not a log:

  1. reflection.py shows the still-open entries to the nightly reflection.
  2. The model answers with a [FOLLOWUPS] block (see parse_followup_block):
     which open entries are done, and which new findings to register.
  3. Code applies the block to the record: every open entry counts as
     checked that night, ``done`` entries close, new findings are added.

When issue #74 (machine-readable task queue) lands, this record is the
natural source to mirror open entries into that queue; until then it is
self-contained, and the growth digest (saku/growth.py) surfaces the open
entries to the Owner so they are visible outside the reflection loop too.
"""

import json
import re
from datetime import datetime
from pathlib import Path

BLOCK_START = "[FOLLOWUPS]"
BLOCK_END = "[/FOLLOWUPS]"

# A reflection that registers a pile of new items every night would flood
# the record faster than it can ever be digested; the prompt asks for at
# most 3, and the parser enforces a hard cap as a backstop.
MAX_NEW_PER_REFLECTION = 5

_LINE_RE = re.compile(r"^(done|still-open|new)\s*:\s*(.+)$", re.IGNORECASE)
_ID_RE = re.compile(r"#?(\d+)")


def empty_store() -> dict:
    return {"version": 1, "next_id": 1, "items": []}


def load_store(path: Path) -> dict:
    """Load the follow-ups record; a missing or corrupt file reads as empty.

    The record is bookkeeping, not memory: a corrupt file must never take
    the nightly reflection down with it. The next save rewrites it cleanly.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return empty_store()
    data.setdefault("version", 1)
    if not isinstance(data.get("next_id"), int):
        ids = [it.get("id", 0) for it in data["items"] if isinstance(it, dict)]
        data["next_id"] = (max(ids) + 1) if ids else 1
    return data


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def open_items(store: dict) -> list[dict]:
    return [it for it in store.get("items", []) if it.get("status") == "open"]


def add_items(store: dict, texts: list[str], date_str: str) -> list[dict]:
    """Register new follow-ups, skipping texts already open (exact match).

    The model re-derives the same weakness on consecutive nights; without
    dedupe the record would fill with copies of one finding and the checks
    counter would lose its meaning. Closed entries do not block re-adding:
    a weakness that came back after being closed is a genuine recurrence.
    """
    existing = {it.get("text", "").strip() for it in open_items(store)}
    added: list[dict] = []
    for text in texts:
        text = text.strip()
        if not text or text in existing:
            continue
        item = {
            "id": store["next_id"],
            "text": text,
            "created": date_str,
            "status": "open",
            "last_checked": "",
            "checks": 0,
            "closed": "",
        }
        store["next_id"] += 1
        store["items"].append(item)
        existing.add(text)
        added.append(item)
    return added


def record_check(store: dict, done_ids: list[int], date_str: str) -> None:
    """Mark one reflection's digestion check over the open entries.

    Every entry that was open gets ``last_checked``/``checks`` updated --
    being shown to the reflection and not reported done IS the check --
    and entries named in ``done_ids`` close.
    """
    done = set(done_ids)
    for item in store.get("items", []):
        if item.get("status") != "open":
            continue
        item["last_checked"] = date_str
        item["checks"] = item.get("checks", 0) + 1
        if item.get("id") in done:
            item["status"] = "done"
            item["closed"] = date_str


def parse_followup_block(text: str) -> dict:
    """Extract the model's follow-up report from a reflection reply.

    Expected shape (the closing marker is optional -- models drop it, and
    a missing marker must not lose the whole report):

        [FOLLOWUPS]
        done: 3
        still-open: 5
        new: <one finding per line>
        [/FOLLOWUPS]

    Parsing is deliberately conservative: lines that do not match are
    ignored, ``done`` ids that are not integers are dropped, and ``new``
    texts are capped at MAX_NEW_PER_REFLECTION. Returns
    ``{"done": [ids], "new": [texts]}``; no block means empty lists.
    """
    result: dict = {"done": [], "new": []}
    if not text or BLOCK_START not in text:
        return result
    block = text.split(BLOCK_START, 1)[1]
    if BLOCK_END in block:
        block = block.split(BLOCK_END, 1)[0]
    for line in block.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        kind, value = m.group(1).lower(), m.group(2).strip()
        if kind == "new":
            if value and len(result["new"]) < MAX_NEW_PER_REFLECTION:
                result["new"].append(value)
        elif kind == "done":
            idm = _ID_RE.search(value)
            if idm:
                result["done"].append(int(idm.group(1)))
        # "still-open" needs no action: record_check already counts every
        # open entry as checked; the line exists so the model can be
        # explicit without inventing its own format.
    return result


def render_open_items(store: dict, today_str: str) -> str:
    """Render the open follow-ups for the reflection prompt (oldest first)."""
    try:
        today = datetime.strptime(today_str, "%Y-%m-%d").date()
    except ValueError:
        today = None
    lines: list[str] = []
    for item in open_items(store):
        age = ""
        if today is not None:
            try:
                created = datetime.strptime(item.get("created", ""), "%Y-%m-%d").date()
                age = f", {(today - created).days}日経過"
            except ValueError:
                pass
        checks = item.get("checks", 0)
        check_note = f", 消化チェック{checks}回" if checks else ""
        lines.append(f"- #{item.get('id')}: {item.get('text', '')}（{item.get('created', '')}〜{age}{check_note}）")
    return "\n".join(lines)
