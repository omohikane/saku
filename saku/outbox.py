"""Owner-facing delivery queue (#120 stage 1).

The daemon already produced messages nobody saw: a Web UI notice lived in a
single ``state/ui_inbox.json`` slot that was consumed on the next poll, and a
spontaneous message only landed in ``chat.md``, which the Owner has to go and
read. This module is the shared "delivery" base the issue asked for: one
durable queue that both outage notices and spontaneous speech go through, so
"SAKU spoke" survives a closed browser and a daemon restart.

Entries are plain JSON under the memory root's ``state/``. Reading is the
Owner's act: ``delivered_at`` records that a client saw an entry, and
``read_at`` records that the Owner actually acknowledged it (by replying,
dismissing it, or running ``saku outbox read``).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from saku.config import OutboxConfig

KIND_SPONTANEOUS = "spontaneous"  # SAKU decided to speak (#120)
KIND_NOTICE = "notice"  # operational notice (heartbeat gap, kill switch, ...)
KINDS = (KIND_SPONTANEOUS, KIND_NOTICE)



@dataclass
class Entry:
    text: str
    kind: str = KIND_NOTICE
    source: str = ""
    id: str = ""
    created_at: float = 0.0
    delivered_at: float = 0.0
    read_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "created_at": self.created_at,
            "created_iso": _iso(self.created_at),
            "delivered_at": self.delivered_at,
            "read_at": self.read_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        return cls(
            text=str(data.get("text", "")),
            kind=str(data.get("kind", KIND_NOTICE)),
            source=str(data.get("source", "")),
            id=str(data.get("id", "")),
            created_at=float(data.get("created_at", 0) or 0),
            delivered_at=float(data.get("delivered_at", 0) or 0),
            read_at=float(data.get("read_at", 0) or 0),
        )


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def resolve_path(memory_root: Path, cfg: OutboxConfig | None = None) -> Path:
    rel = Path((cfg or OutboxConfig()).file or "state/outbox.json").expanduser()
    return rel if rel.is_absolute() else memory_root / rel


def _load(path: Path) -> list[Entry]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [Entry.from_dict(item) for item in raw if isinstance(item, dict)]


def _save(path: Path, entries: list[Entry], cfg: OutboxConfig,
          now: float | None = None) -> None:
    now = time.time() if now is None else now
    cutoff = now - cfg.retention_days * 86400 if cfg.retention_days > 0 else 0
    kept = [e for e in entries if not cutoff or e.created_at >= cutoff]
    kept = kept[-cfg.max_items :] if cfg.max_items > 0 else kept
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "updated_at": _iso(now),
               "items": [e.to_dict() for e in kept]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def add(
    memory_root: Path,
    text: str,
    *,
    kind: str = KIND_NOTICE,
    source: str = "",
    cfg: OutboxConfig | None = None,
    now: float | None = None,
) -> Entry | None:
    """Queue one Owner-facing message. Empty text is ignored."""
    text = (text or "").strip()
    if not text:
        return None
    cfg = cfg or OutboxConfig()
    now = time.time() if now is None else now
    entry = Entry(
        text=text,
        kind=kind if kind in KINDS else KIND_NOTICE,
        source=source,
        id=uuid.uuid4().hex[:12],
        created_at=now,
    )
    path = resolve_path(memory_root, cfg)
    entries = _load(path)
    entries.append(entry)
    _save(path, entries, cfg, now=now)
    return entry


def items(
    memory_root: Path,
    *,
    cfg: OutboxConfig | None = None,
    unread_only: bool = False,
) -> list[Entry]:
    entries = _load(resolve_path(memory_root, cfg))
    if unread_only:
        entries = [e for e in entries if not e.read_at]
    return entries


def mark_delivered(
    memory_root: Path,
    ids: list[str] | None = None,
    *,
    cfg: OutboxConfig | None = None,
    now: float | None = None,
) -> int:
    cfg = cfg or OutboxConfig()
    now = time.time() if now is None else now
    path = resolve_path(memory_root, cfg)
    entries = _load(path)
    changed = 0
    for entry in entries:
        if entry.delivered_at:
            continue
        if ids is None or entry.id in ids:
            entry.delivered_at = now
            changed += 1
    if changed:
        _save(path, entries, cfg, now=now)
    return changed


def mark_read(
    memory_root: Path,
    ids: list[str] | None = None,
    *,
    cfg: OutboxConfig | None = None,
    kind: str = "",
    now: float | None = None,
) -> int:
    """Acknowledge entries (all unread, those ids, or those of a kind)."""
    cfg = cfg or OutboxConfig()
    now = time.time() if now is None else now
    path = resolve_path(memory_root, cfg)
    entries = _load(path)
    changed = 0
    for entry in entries:
        if entry.read_at:
            continue
        if kind and entry.kind != kind:
            continue
        if ids is not None and entry.id not in ids:
            continue
        entry.read_at = now
        changed += 1
    if changed:
        _save(path, entries, cfg, now=now)
    return changed


def unread_count(memory_root: Path, cfg: OutboxConfig | None = None) -> int:
    return sum(1 for e in _load(resolve_path(memory_root, cfg)) if not e.read_at)


def format_entry(entry: Entry) -> str:
    tag = "自発" if entry.kind == KIND_SPONTANEOUS else "通知"
    return f"[{entry.id}] {_iso(entry.created_at)} ({tag}) {entry.text}"


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku outbox",
        description="List or acknowledge messages SAKU sent to the Owner",
    )
    parser.add_argument("op", nargs="?", default="list",
                        help="list / read <id...> / clear")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--all", action="store_true", help="include already-read entries")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    ocfg = cfg_mod.load_outbox_config(cfg)

    if args.op == "list":
        entries = items(memory_root, cfg=ocfg, unread_only=not args.all)
        if not entries:
            print("(outbox is empty)")
            return 0
        for entry in entries:
            print(format_entry(entry))
        print(f"  unread: {unread_count(memory_root, ocfg)}")
        return 0

    if args.op == "read":
        changed = mark_read(memory_root, args.ids or None, cfg=ocfg)
        print(f"[*] marked {changed} entry(ies) read")
        return 0

    if args.op == "clear":
        changed = mark_read(memory_root, None, cfg=ocfg)
        print(f"[*] cleared {changed} entry(ies)")
        return 0

    print(f"[!] unknown op: {args.op}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
