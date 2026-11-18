"""Kill switch: the Owner's immediate stop for autonomous operation (#76).

At the Full Moon stage the Owner holds only a veto — and a veto is only
real if there is a working way to stop the machine. The switch is a flag
file (default ``state/STOP`` under the memory root, ``[killswitch]
flag_file``):

* **Engaged** = the file exists. Presence is the whole decision; the
  content is JSON metadata (when / by whom / why) for humans and for
  ``saku status``, never parsed on the stop path — an empty file, or one
  written by hand or by another tool, stops SAKU just the same.
* While engaged, the daemon starts no autonomous action (study ticks,
  inbox processing, self-initiation, scheduled tasks, reflection,
  dreaming, weekly review) and the mail / talk channels refuse to
  answer. Every gate stats the file at action time; nothing caches the
  state, so a flag dropped in by the vault's git pull takes effect at
  the next action, and release resumes normal operation the same way.
* **Released** = the file is gone. Only the Owner can do either: the
  default location is outside every write-allowed prefix of the
  ``[paths]`` policy (and on its write/delete deny lists), so SAKU's
  own file tools cannot create or remove it. Engage / release happen
  through ``saku stop`` / ``saku resume``, or by hand in the vault.

What the switch deliberately does NOT stop:

* Owner chat replies (chat.md / Web UI). Those are reactive — the
  Owner is present and directing — not autonomous operation.
* Git pull / sync of the memory repository. The pull is how a remotely
  engaged STOP arrives; sync is how the audit trail (mail / talk
  refusals, this module's log) reaches the Owner.
* An agent loop already in flight when the switch engages. Autonomous
  loops re-check between turns and halt at the next one, so the window
  is one bounded turn, never a whole run.

Honest limit: this is an operational stop, not a security boundary.
``EXECUTE_CODE`` runs unsandboxed Python (sandboxing is #78's scope),
so a deliberately adversarial model could remove the flag through code
execution; in normal operation no tool path reaches it.

Engage / release append one line each to ``killswitch.log`` beside the
flag file, and the daemon logs every transition it observes, so the
history of the switch is auditable from the synced memory alone.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import config as saku_config

LOG_FILE_NAME = "killswitch.log"


# ── State ───────────────────────────────────────────────
def is_stopped(flag: Path) -> bool:
    """True while the flag file exists. This is the ONLY stop check, and
    it stats the filesystem on every call — callers invoke it at action
    time, never on a cached value."""
    return Path(flag).exists()


def read_info(flag: Path) -> "dict | None":
    """Metadata of an engaged switch (``at`` / ``by`` / ``reason``).

    None when the switch is not engaged. An engaged file whose content
    is not the JSON metadata (hand-made flag, partial write) yields
    ``{"at": "", "by": "", "reason": ""}`` — engaged is engaged.
    """
    flag = Path(flag)
    if not flag.exists():
        return None
    try:
        data = json.loads(flag.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "at": str(data.get("at", "")),
                "by": str(data.get("by", "")),
                "reason": str(data.get("reason", "")),
            }
    except Exception:
        pass
    return {"at": "", "by": "", "reason": ""}


# ── Audit log ───────────────────────────────────────────
def log_path(flag: Path) -> Path:
    """The engage/release log lives beside the flag file."""
    return Path(flag).parent / LOG_FILE_NAME


def _append_log(flag: Path, event: str, info: dict) -> None:
    """Append one line to killswitch.log. Never raises: the switch must
    work even when its own bookkeeping cannot be written."""
    try:
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        line = (
            f"{ts} {event} by={info.get('by', '') or '-'} "
            f"reason={info.get('reason', '') or '-'}\n"
        )
        path = log_path(flag)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read_log(flag: Path) -> list[str]:
    """All recorded engage/release lines, oldest first ([] when none)."""
    try:
        return log_path(flag).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


# ── Engage / release (Owner-side operations) ────────────
def engage(flag: Path, by: str = "cli", reason: str = "") -> dict:
    """Engage the switch: write the flag file (atomically, via a temp
    file in the same directory + rename, so a concurrent check never
    sees a half-written flag — although presence alone would suffice).
    Returns the metadata written. Idempotent: engaging an engaged
    switch refreshes its metadata."""
    flag = Path(flag)
    info = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "by": by,
        "reason": reason,
    }
    flag.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(flag.parent), prefix=".STOP-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False) + "\n")
        os.replace(tmp, flag)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _append_log(flag, "engaged", info)
    return info


def release(flag: Path) -> "dict | None":
    """Release the switch: remove the flag file. Returns the metadata
    of the engagement that ended, or None when it was not engaged."""
    flag = Path(flag)
    info = read_info(flag)
    if info is None:
        return None
    try:
        flag.unlink()
    except FileNotFoundError:
        return None
    _append_log(flag, "released", {"by": "cli", "reason": ""})
    return info


# ── Misconfiguration guard ──────────────────────────────
def tool_writable_relpath(cfg: dict, memory_root: Path, flag: Path) -> "str | None":
    """The flag's memory-root-relative path if SAKU's file tools could
    write or delete it under the ``[paths]`` policy, else None.

    The default location cannot be reached by the tools; this exists so
    a custom ``[killswitch] flag_file`` that wanders into a write-allowed
    prefix is called out loudly instead of silently weakening the
    switch (the Owner could still choose it — the warning is the point).
    """
    try:
        rel = Path(flag).resolve().relative_to(Path(memory_root).resolve()).as_posix()
    except ValueError:
        return None  # outside the memory root: the tools cannot reach it at all
    policy = saku_config.load_path_policy(cfg)
    if rel in policy.write_denied_exact or rel in policy.delete_denied_exact:
        return None
    if policy.is_write_allowed(rel):
        return rel
    return None


# ── CLI: saku stop / saku resume / saku killswitch ──────
def _resolve_flag() -> "tuple[dict, Path, Path]":
    cfg, base = saku_config.load_config()
    memory_root = saku_config.resolve_memory_root(cfg, base)
    return cfg, memory_root, saku_config.resolve_killswitch_flag(cfg, memory_root)


def describe(flag: Path) -> str:
    """One-line human summary of the switch state, for status output."""
    info = read_info(flag)
    if info is None:
        return "released (autonomous operation running)"
    parts = ["ENGAGED — autonomous operation stopped"]
    if info.get("at"):
        parts.append(f"since {info['at']}")
    if info.get("by"):
        parts.append(f"by {info['by']}")
    if info.get("reason"):
        parts.append(f"reason: {info['reason']}")
    return ", ".join(parts)


def main(argv: "list[str] | None" = None) -> int:
    args = list(argv or [])
    action = args[0] if args else "status"
    rest = args[1:]
    reason = ""
    by = "cli"
    i = 0
    while i < len(rest):
        if rest[i] == "--reason" and i + 1 < len(rest):
            reason = rest[i + 1]
            i += 2
        elif rest[i] == "--by" and i + 1 < len(rest):
            by = rest[i + 1]
            i += 2
        else:
            i += 1

    cfg, memory_root, flag = _resolve_flag()

    if action in ("engage", "stop"):
        info = engage(flag, by=by, reason=reason)
        print(f"Kill switch ENGAGED ({flag})")
        print(f"  at: {info['at']}  by: {info['by']}" + (f"  reason: {reason}" if reason else ""))
        print("  Autonomous operation is stopped until `saku resume`.")
        warn_rel = tool_writable_relpath(cfg, memory_root, flag)
        if warn_rel:
            print(f"  [!] warning: {warn_rel} is inside a tool-writable path; "
                  "SAKU's file tools could remove this flag.")
        return 0

    if action in ("release", "resume"):
        info = release(flag)
        if info is None:
            print(f"Kill switch was not engaged ({flag}). Nothing to release.")
            return 0
        print(f"Kill switch RELEASED ({flag})")
        if info.get("at"):
            print(f"  was engaged at: {info['at']}  by: {info.get('by', '') or '-'}")
        print("  Autonomous operation resumes at the next daemon action.")
        return 0

    if action == "status":
        print(f"Kill switch: {describe(flag)}")
        print(f"  flag file: {flag}")
        lines = read_log(flag)
        if lines:
            print(f"  last event: {lines[-1]}")
        return 0

    print("Usage: saku killswitch [engage|release|status] [--reason TEXT] [--by NAME]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
