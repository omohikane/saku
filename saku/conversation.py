"""One conversation record across every channel (#120 follow-up).

SAKU's durable memory (MEMORY.md, journal, principles) was already shared, but
each *conversation* lived in its own box: the Web UI kept history in memory
keyed by a browser session, chat.md had its own file, talk had a per-caller
dict in the MCP process, and mail had letters. Changing the place you talk to
SAKU (another browser, chat.md, Discord later) therefore felt like meeting a
different person even though the memory was the same.

This module is the channel-independent thread: an append-only JSONL log of
``(time, channel, principal, role, text)`` under ``state/``. Channels write
every Owner/agent-visible turn, and a channel that starts fresh seeds itself
from the recent turns of the same principal. A new adapter (Discord, Slack, a
voice front end) only has to call :func:`append` and :func:`history_messages`.

Deliberate limits:

- tool results and system prompts are **not** stored — this is conversation,
  not the agent's scratchpad;
- entries are capped by count so the file cannot grow without bound;
- reading is bounded by ``limit`` and by ``max_chars`` so a long thread cannot
  blow up a prompt;
- the log is per memory root, like every other durable store.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

DEFAULT_MAX_ENTRIES = 2000
DEFAULT_HISTORY_LIMIT = 12
DEFAULT_MAX_CHARS = 4000

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


def resolve_path(memory_root: Path, cfg: dict | None = None) -> Path:
    # Tests set SAKU_CONVERSATION_FILE so a run never writes into real memory.
    override = os.environ.get("SAKU_CONVERSATION_FILE", "")
    if override:
        return Path(override)
    rel = str((cfg or {}).get("conversation", {}).get("file", "state/conversation.jsonl"))
    path = Path(rel).expanduser()
    return path if path.is_absolute() else Path(memory_root) / path


def _config(cfg: dict | None) -> dict:
    c = (cfg or {}).get("conversation", {})
    return {
        "max_entries": int(c.get("max_entries", DEFAULT_MAX_ENTRIES)),
        "history_limit": int(c.get("history_limit", DEFAULT_HISTORY_LIMIT)),
        "max_chars": int(c.get("max_chars", DEFAULT_MAX_CHARS)),
        "enabled": bool(c.get("enabled", True)),
    }


def append(
    memory_root: Path,
    *,
    channel: str,
    principal: str,
    role: str,
    text: str,
    session: str = "",
    cfg: dict | None = None,
    now: float | None = None,
) -> bool:
    """Record one visible turn. Tool output must not be passed here."""
    conf = _config(cfg)
    if not conf["enabled"]:
        return False
    text = (text or "").strip()
    if not text or role not in (ROLE_USER, ROLE_ASSISTANT):
        return False
    now = time.time() if now is None else now
    path = resolve_path(memory_root, cfg)
    record = {
        "ts": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": now,
        "channel": channel,
        "principal": principal,
        "role": role,
        "session": session,
        "text": text[:4000],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _prune(path, conf["max_entries"])
    except OSError:
        return False
    return True


def _prune(path: Path, max_entries: int) -> None:
    """Keep the log bounded by rewriting when it has grown past the cap.

    The cap is a line count, not a byte estimate: short messages used to slip
    under a size heuristic and the log never shrank. Reading a few thousand
    lines once per turn costs little and keeps the promise exact.
    """
    if max_entries <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= max_entries:
        return
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return


def recent(
    memory_root: Path,
    limit: int = DEFAULT_HISTORY_LIMIT,
    *,
    principal: str = "",
    channels: list[str] | None = None,
    cfg: dict | None = None,
) -> list[dict]:
    """Newest turns first filtered by principal/channel, returned oldest-first."""
    conf = _config(cfg)
    path = resolve_path(memory_root, cfg)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    wanted = set(channels or [])
    out: list[dict] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if principal and record.get("principal") != principal:
            continue
        if wanted and record.get("channel") not in wanted:
            continue
        out.append(record)
        if len(out) >= limit:
            break
    out.reverse()
    return out


def history_messages(
    memory_root: Path,
    *,
    principal: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
    channels: list[str] | None = None,
    cfg: dict | None = None,
) -> list[dict]:
    """Chat messages to seed a fresh channel from the shared thread."""
    conf = _config(cfg)
    entries = recent(
        memory_root,
        limit=limit,
        principal=principal,
        channels=channels,
        cfg=cfg,
    )
    messages: list[dict] = []
    used = 0
    for entry in entries:
        text = entry["text"]
        if used + len(text) > conf["max_chars"]:
            break
        messages.append({"role": entry["role"], "content": text})
        used += len(text)
    # A conversation should start with the user, not an orphan assistant reply.
    while messages and messages[0]["role"] != ROLE_USER:
        messages.pop(0)
    return messages
