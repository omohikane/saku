"""Liveness signal for the resident daemon (#75).

SAKU's worst failure mode is dying quietly: the Web UI still answers, nothing
looks broken, and the Owner finds out days later. The daemon therefore writes a
small heartbeat file every loop iteration, and two readers make use of it:

- ``saku status`` shows how long ago the last beat was, so a glance is enough.
- ``saku heartbeat --check`` exits non-zero when the beat is stale or missing,
  which is what an external monitor (a systemd timer, cron, another agent)
  should run. The command never touches the network.

The daemon also compares the file's timestamp with the wall clock at startup:
if it was already stale, the process knows it was *down* and can say so once,
instead of pretending the gap never happened.

The heartbeat is a file under ``state/`` because it must survive a restart, be
readable without the daemon's cooperation, and require no port or IPC.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

DEFAULT_FILE = "state/heartbeat.json"
DEFAULT_MAX_AGE = 900  # seconds; a single slow LLM call must not look like death


def resolve_path(memory_root: Path, file: str = DEFAULT_FILE) -> Path:
    path = Path(file or DEFAULT_FILE).expanduser()
    return path if path.is_absolute() else memory_root / path


def beat(
    memory_root: Path,
    *,
    file: str = DEFAULT_FILE,
    interval: int | None = None,
    **extra,
) -> Path:
    """Write the heartbeat atomically. Returns the path written."""
    path = resolve_path(memory_root, file)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    previous = read(memory_root, file=file) or {}
    payload = {
        "pid": os.getpid(),
        "last_alive": now,
        "last_alive_iso": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": previous.get("started_at") or now,
        "beats": int(previous.get("beats", 0)) + 1,
        "interval": interval if interval is not None else previous.get("interval"),
    }
    payload.update(extra)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read(memory_root: Path, *, file: str = DEFAULT_FILE) -> dict | None:
    path = resolve_path(memory_root, file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def age_seconds(heartbeat: dict | None, *, now: float | None = None) -> float | None:
    if not heartbeat:
        return None
    try:
        last = float(heartbeat.get("last_alive", 0))
    except (TypeError, ValueError):
        return None
    if last <= 0:
        return None
    return max(0.0, (now if now is not None else time.time()) - last)


def pid_alive(pid: int | None) -> bool:
    """Whether a process exists. EPERM still means the process is there."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def check(
    memory_root: Path,
    *,
    file: str = DEFAULT_FILE,
    max_age: int = DEFAULT_MAX_AGE,
    now: float | None = None,
) -> dict:
    """Return the liveness verdict: ``ok`` / ``stale`` / ``missing``."""
    hb = read(memory_root, file=file)
    age = age_seconds(hb, now=now)
    if hb is None or age is None:
        return {"status": "missing", "age": None, "heartbeat": None, "pid_alive": False}
    pid = hb.get("pid")
    alive = pid_alive(pid)
    if age > max_age:
        status = "stale"
    elif not alive:
        # A fresh timestamp whose process is gone (pid reuse aside) is a restart
        # window; the daemon writes on start, so this is short-lived.
        status = "dead"
    else:
        status = "ok"
    return {"status": status, "age": age, "heartbeat": hb, "pid_alive": alive}


def _human_age(age: float | None) -> str:
    if age is None:
        return "unknown"
    if age < 90:
        return f"{age:.0f}s"
    if age < 5400:
        return f"{age / 60:.0f}m"
    return f"{age / 3600:.1f}h"


def status_line(memory_root: Path, *, file: str = DEFAULT_FILE, max_age: int = DEFAULT_MAX_AGE) -> str:
    """One line for ``saku status``."""
    result = check(memory_root, file=file, max_age=max_age)
    hb = result["heartbeat"] or {}
    pid = hb.get("pid")
    if result["status"] == "missing":
        return "heartbeat     : missing (daemon has not written one yet)"
    age = _human_age(result["age"])
    if result["status"] == "ok":
        return f"heartbeat     : {age} ago (pid {pid}, alive)"
    if result["status"] == "dead":
        return f"heartbeat     : {age} ago but pid {pid} is gone (stale process)"
    return f"heartbeat     : {age} ago (STALE; threshold {max_age}s, pid {pid})"


def gap_notice(
    memory_root: Path,
    *,
    file: str = DEFAULT_FILE,
    max_age: int = DEFAULT_MAX_AGE,
    now: float | None = None,
) -> str | None:
    """Describe a gap since the previous run, or None when there was none.

    Called at daemon startup, before the first beat overwrites the file: an age
    beyond ``max_age`` means the previous process was down (or wedged) for that
    long. A missing file means this is a first start — not an outage.
    """
    hb = read(memory_root, file=file)
    age = age_seconds(hb, now=now)
    if hb is None or age is None or age <= max_age:
        return None
    return (
        f"前回のハートビートから {_human_age(age)} 空いています"
        f"（pid {hb.get('pid', '?')}）。停止していた可能性があります。"
    )


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """``saku heartbeat [--check] [--max-age N] [--json]``.

    With ``--check`` the exit code is the signal: 0 when the daemon beat
    recently, 1 when the beat is stale, dead or missing. That is the hook an
    external monitor (systemd timer, cron, another agent) should use.
    """
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku heartbeat",
        description="Show or check the daemon's liveness signal",
    )
    parser.add_argument("--check", action="store_true", help="exit non-zero unless fresh")
    parser.add_argument("--max-age", type=int, default=None, help="staleness threshold (s)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    hbcfg = cfg_mod.load_heartbeat_config(cfg)
    max_age = args.max_age if args.max_age is not None else hbcfg.max_age_seconds
    result = check(memory_root, file=hbcfg.file, max_age=max_age)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.check:
        print(status_line(memory_root, file=hbcfg.file, max_age=max_age))
    else:
        print(f"SAKU heartbeat ({resolve_path(memory_root, hbcfg.file)})")
        print(f"  {status_line(memory_root, file=hbcfg.file, max_age=max_age)}")
        hb = result["heartbeat"] or {}
        if hb.get("last_alive_iso"):
            print(f"  last beat: {hb['last_alive_iso']}")
        if hb.get("beats"):
            print(f"  beats: {hb['beats']}")
        if hb.get("interval"):
            print(f"  loop interval: {hb['interval']}s")
        tip = gap_notice(memory_root, file=hbcfg.file, max_age=max_age)
        if tip:
            print(f"  [!] {tip}")

    if not args.check:
        return 0
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
