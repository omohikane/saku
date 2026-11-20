"""Where the Owner probably is, so SAKU can decide whether to speak (#82).

The daemon could only tell "no activity for N minutes" before this. That is not
enough to choose between speaking and staying quiet: a silent afternoon at work
and a silent evening at home look identical. Presence adds the cheap signals
that are actually available on this machine:

- **manual** — the Owner sets it (`saku presence away --for 120`). Highest
  priority, with an optional expiry so a forgotten override does not stick.
- **quiet hours** — a configurable sleep window.
- **work hours** — configurable weekdays and a time window.
- **activity** — the freshest of chat.md, today's journal and the UI's
  last-seen stamp decides "away" when nothing has happened for a while.

No calendar, no DBus, no location service: this is a hint, not surveillance.
`can_speak` is the one question the daemon asks.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path

from saku.config import PresenceConfig

STATES = ("present", "away", "working", "sleeping", "unknown")


@dataclass
class Presence:
    state: str
    source: str  # manual / quiet_hours / work_hours / activity / default
    since: str
    last_activity: str = ""
    can_speak: bool = True

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "source": self.source,
            "since": self.since,
            "last_activity": self.last_activity,
            "can_speak": self.can_speak,
        }


def _path(memory_root: Path, cfg: PresenceConfig) -> Path:
    p = Path(cfg.file or "state/presence.json").expanduser()
    return p if p.is_absolute() else memory_root / p


def _parse_hm(value: str, fallback: dtime) -> dtime:
    try:
        h, m = value.split(":", 1)
        return dtime(int(h), int(m))
    except (ValueError, AttributeError):
        return fallback


def _in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """Whether ``now`` is inside a possibly overnight window."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def read_manual(memory_root: Path, cfg: PresenceConfig, *, now: float | None = None) -> dict | None:
    """Return the manual override if it is still in effect."""
    path = _path(memory_root, cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    state = str(data.get("state", ""))
    if state not in STATES or state == "unknown":
        return None
    now = time.time() if now is None else now
    until = data.get("until")
    if until is not None:
        try:
            if now >= float(until):
                return None
        except (TypeError, ValueError):
            pass
    return data


def set_manual(
    memory_root: Path,
    state: str,
    *,
    minutes: int = 0,
    cfg: PresenceConfig | None = None,
    now: float | None = None,
) -> dict:
    """Set (or clear, with state="clear") the manual presence override."""
    cfg = cfg or PresenceConfig()
    path = _path(memory_root, cfg)
    if state == "clear":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return {"state": "cleared"}
    if state not in STATES or state == "unknown":
        raise ValueError(f"unknown presence state: {state}")
    now = time.time() if now is None else now
    payload = {
        "state": state,
        "since": _iso(now),
        "until": (now + minutes * 60) if minutes > 0 else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def last_activity(memory_root: Path) -> float:
    """Freshest Owner-visible activity on this machine."""
    candidates = [
        memory_root / "chat.md",
        memory_root / "journal" / f"{datetime.now().strftime('%Y-%m-%d')}.md",
        memory_root / "state" / "ui_last_seen.json",
    ]
    newest = 0.0
    for p in candidates:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def record_ui_activity(memory_root: Path, *, now: float | None = None) -> None:
    """Mark that the Owner is using the Web UI right now."""
    path = memory_root / "state" / "ui_last_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    path.write_text(json.dumps({"last_seen": now}), encoding="utf-8")


def _can_speak(state: str, cfg: PresenceConfig) -> bool:
    if state in ("sleeping",):
        return False
    if state == "working":
        return bool(cfg.speak_while_working)
    if state == "away":
        return bool(cfg.speak_while_away)
    return True


def load(
    memory_root: Path,
    cfg: PresenceConfig | None = None,
    *,
    now: float | None = None,
) -> Presence:
    """Best current guess, in priority order (manual > sleep > work > activity)."""
    cfg = cfg or PresenceConfig()
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(now)
    stamp = _iso(now)

    if not cfg.enabled:
        return Presence(state="present", source="disabled", since=stamp, can_speak=True)

    manual = read_manual(memory_root, cfg, now=now)
    if manual:
        state = str(manual["state"])
        return Presence(
            state=state,
            source="manual",
            since=str(manual.get("since", stamp)),
            last_activity=_iso(last_activity(memory_root)) if last_activity(memory_root) else "",
            can_speak=_can_speak(state, cfg),
        )

    sleep_start = _parse_hm(cfg.sleep_start, dtime(0, 30))
    sleep_end = _parse_hm(cfg.sleep_end, dtime(6, 30))
    if _in_window(dt.time(), sleep_start, sleep_end):
        return Presence(
            state="sleeping", source="quiet_hours", since=stamp, can_speak=False
        )

    work_start = _parse_hm(cfg.work_start, dtime(9, 0))
    work_end = _parse_hm(cfg.work_end, dtime(18, 0))
    if dt.weekday() in cfg.work_days and _in_window(dt.time(), work_start, work_end):
        return Presence(
            state="working",
            source="work_hours",
            since=stamp,
            can_speak=_can_speak("working", cfg),
        )

    activity = last_activity(memory_root)
    if activity and (now - activity) > cfg.away_after_minutes * 60:
        return Presence(
            state="away",
            source="activity",
            since=stamp,
            last_activity=_iso(activity),
            can_speak=_can_speak("away", cfg),
        )

    return Presence(
        state="present",
        source="activity" if activity else "default",
        since=stamp,
        last_activity=_iso(activity) if activity else "",
        can_speak=True,
    )


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku presence",
        description="Show or set the Owner's presence hint (used for deciding when to speak)",
    )
    parser.add_argument("state", nargs="?", default="show",
                        help="show / present / away / working / sleeping / clear")
    parser.add_argument("--for", dest="minutes", type=int, default=0,
                        help="minutes until the manual state expires (0 = no expiry)")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    pcfg = cfg_mod.load_presence_config(cfg)

    if args.state in ("show", ""):
        p = load(memory_root, pcfg)
        print("SAKU presence")
        for key, value in p.to_dict().items():
            print(f"  {key}: {value}")
        manual = read_manual(memory_root, pcfg)
        if manual:
            print(f"  manual: {manual}")
        return 0

    try:
        set_manual(memory_root, args.state, minutes=args.minutes, cfg=pcfg)
    except ValueError as e:
        print(f"[!] {e}")
        return 2
    if args.state == "clear":
        print("[*] manual presence cleared")
    else:
        suffix = f" for {args.minutes}m" if args.minutes else ""
        print(f"[*] presence set to {args.state}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
