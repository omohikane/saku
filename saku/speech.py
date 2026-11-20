"""Delivery of SAKU's own words to an audience (#120 stage 2).

Stage 1 put spontaneous speech in the Owner's outbox. This module generalises
the *recipient* while keeping one core: a reason produces text, the text is
delivered, and the delivery is recorded. The permission that decides whether a
recipient may be addressed is #73's: a configured agent with the ``mail``
scope. A name alone grants nothing, and an unknown recipient is refused.

Channels:

``owner``  the Owner's durable outbox (Web UI / ``saku outbox``).
``<agent>`` a letter in ``<memory>/mail/outbox/`` — the same file format the
           mail channel already writes for replies, so the other agent's
           reader needs no new code. The letter carries ``from: 朔`` and
           ``to: <agent>`` and no ``in_reply_to``: it is a new thread, not an
           answer.

Policy: a stopped SAKU (kill switch) does not speak, text is capped by
``[speech] max_chars``, and each recipient has an hourly cap so a loop cannot
spam an agent. Every send is audited through #73's log, allowed or denied.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from saku import config as saku_config
from saku import identity, killswitch, outbox

FROM_NAME = "朔"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def _state_path(memory_root: Path, cfg: dict) -> Path:
    rel = str(cfg.get("speech", {}).get("state_file", "state/speech.json"))
    path = Path(rel).expanduser()
    return path if path.is_absolute() else Path(memory_root) / path


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": {}}
    if not isinstance(data, dict):
        return {"events": {}}
    data.setdefault("events", {})
    return data


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _rate_ok(state: dict, recipient: str, limit: int, now: float) -> bool:
    recent = [t for t in state["events"].get(recipient, []) if now - t < 3600]
    state["events"][recipient] = recent
    return len(recent) < limit


def _write_letter(
    mail_dir: Path,
    to: str,
    body: str,
    *,
    now: float,
    letter_id: str | None = None,
) -> Path:
    """Write a new-thread letter into the outbox, never overwriting."""
    letter_id = letter_id or f"{datetime.fromtimestamp(now).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    out_dir = mail_dir / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{letter_id}.md"
    content = (
        f"---\nfrom: {FROM_NAME}\nto: {to}\nid: {letter_id}\n"
        f"sent: {_iso(now)}\n---\n{body.strip()}\n"
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def deliver(
    text: str,
    *,
    to: str = "owner",
    memory_root: Path,
    cfg: dict,
    now: float | None = None,
) -> dict:
    """Send SAKU's words to the Owner or to a configured agent.

    Returns ``{ok, channel, recipient, detail, path?}``. Refusals are audited
    with ``allowed=False`` and never raise: speaking is best-effort.
    """
    now = time.time() if now is None else now
    text = (text or "").strip()
    scfg = cfg.get("speech", {})
    max_chars = int(scfg.get("max_chars", 1500))
    max_per_hour = int(scfg.get("max_per_hour", 6))
    recipient = (to or "owner").strip()
    principal = identity.SYSTEM

    def _deny(reason: str) -> dict:
        identity.audit(
            memory_root, principal, "speech.send", channel="speech",
            allowed=False, detail=f"to={recipient}: {reason}", cfg=cfg, now=now,
        )
        return {"ok": False, "channel": "speech", "recipient": recipient, "detail": reason}

    if not text:
        return _deny("empty message")
    if len(text) > max_chars:
        return _deny(f"message exceeds {max_chars} characters")
    if killswitch.is_stopped(saku_config.resolve_killswitch_flag(cfg, memory_root)):
        return _deny("kill switch engaged")

    state_path = _state_path(memory_root, cfg)
    state = _load_state(state_path)
    if not _rate_ok(state, recipient, max_per_hour, now):
        return _deny(f"hourly limit reached for {recipient}")

    if recipient.lower() == "owner":
        entry = outbox.add(
            memory_root, text,
            kind=outbox.KIND_SPONTANEOUS,
            source="speech",
            cfg=saku_config.load_outbox_config(cfg),
            now=now,
        )
        state["events"].setdefault(recipient, []).append(now)
        _save_state(state_path, state)
        identity.audit(
            memory_root, principal, "speech.send", channel="webui",
            allowed=True, detail=f"to=owner id={entry.id if entry else '-'}", cfg=cfg, now=now,
        )
        return {
            "ok": True, "channel": "webui", "recipient": "owner",
            "detail": "delivered to the Owner outbox",
        }

    target = identity.principal_for_agent(cfg, recipient)
    if target is None:
        return _deny("unknown recipient")
    if not target.can(identity.SCOPE_MAIL):
        return _deny(f"recipient {recipient} has no mail scope")

    mail_dir = saku_config.resolve_mail_dir(cfg, memory_root)
    path = _write_letter(mail_dir, recipient, text, now=now)
    state["events"].setdefault(recipient, []).append(now)
    _save_state(state_path, state)
    identity.audit(
        memory_root, principal, "speech.send", channel="mail",
        allowed=True, detail=f"to={recipient} id={path.stem}", cfg=cfg, now=now,
    )
    return {
        "ok": True, "channel": "mail", "recipient": recipient,
        "detail": f"letter written: {path.name}", "path": str(path),
    }


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku say",
        description="Deliver a message from SAKU to the Owner or a configured agent",
    )
    parser.add_argument("text", nargs="+", help="what to say")
    parser.add_argument("--to", default="owner", help="owner (default) or an agent name")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    result = deliver(
        " ".join(args.text), to=args.to, memory_root=memory_root, cfg=cfg
    )
    if result["ok"]:
        print(f"[*] delivered to {result['recipient']} via {result['channel']}: {result['detail']}")
        return 0
    print(f"[!] not delivered: {result['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
