#!/usr/bin/env python3
"""
SAKU Autonomous Daemon v3

Runs in the background, waking up periodically to:
1. Monitor _saku/chat.md for new Owner messages (ending with '>') and reply.
2. Auto-archive chat when inactive or too many turns.
3. Check request_list.md for pending tasks and notify the Owner in chat.
4. Auto-initiate conversation in chat.md (daily morning greeting or check-in).
5. Run midnight reflection at 2:00 AM using reflect.py.
6. Scan the Vault Inbox (00_Inbox) for new/updated files.
7. Run periodic autonomous ticks for self-study and monologue writing.

Every autonomous action above honours the kill switch (#76): while the
flag file ([killswitch] flag_file, default state/STOP) exists, none of
them start, and the mail channel refuses to answer. Owner chat replies
(item 1) are not autonomous and keep working.
"""

import hashlib
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from saku import core as agent
from saku import dreaming
from saku import growth
from saku import heartbeat as heartbeat_mod
from saku import identity
from saku import killswitch
from saku import mail as saku_mail
from saku import provenance
from saku import persona
from saku import outbox
from saku import presence
from saku import reachout
from saku import reflection
from saku import skills as saku_skills
from saku import weekly
from saku.agent_loop import run_agent_loop as _run_agent_loop
from saku.vault_git import pull_if_due as _git_pull_if_due
from saku.vault_git import sync_if_due as _git_sync_if_due

# ── Config (from config.toml via core) ───────────────────────
MEMORY_ROOT = agent.MEMORY_ROOT
_dcfg = agent._cfg.get("daemon", {})
_mcfg = agent.saku_config.load_mail_config(agent._cfg)
MAIL_ENABLED = _mcfg.enabled
MAIL_POLL_SECONDS = max(5, _mcfg.poll_seconds)

# Kill switch (#76): the flag's presence stops all autonomous operation.
# The path is resolved once from config; the flag itself is statted fresh
# at every gate (killswitch.is_stopped), never cached.
KILLSWITCH_FLAG = agent.saku_config.resolve_killswitch_flag(agent._cfg, MEMORY_ROOT)

CHAT_FILE = MEMORY_ROOT / "chat.md"
STATE_FILE = MEMORY_ROOT / "state/processed_inbox.json"
CHAT_STATE_FILE = MEMORY_ROOT / "state/chat_state.json"
REQUEST_FILE = MEMORY_ROOT / "request_list.md"
LOG_DIR = MEMORY_ROOT / "state"
UI_INBOX = MEMORY_ROOT / "state" / "ui_inbox.json"
LOCK_FILE = MEMORY_ROOT / "state" / "daemon.lock"

PROACTIVE_CHANNELS = agent.saku_config.load_channels_config(agent._cfg).proactive

# Heartbeat (#75): the daemon writes state/heartbeat.json every loop so an
# external monitor can tell "quiet" from "dead". Resolved once from [heartbeat].
_hbcfg = agent.saku_config.load_heartbeat_config(agent._cfg)
HEARTBEAT_ENABLED = _hbcfg.enabled
HEARTBEAT_FILE = _hbcfg.file
HEARTBEAT_MAX_AGE = _hbcfg.max_age_seconds
HEARTBEAT_NOTIFY_ON_GAP = _hbcfg.notify_on_gap

# Presence (#82): decides whether a spontaneous message should go out at all.
# Heartbeat/outage notices are exempt: a failure must be reported even at night.
PRESENCE_ENABLED = agent.saku_config.load_presence_config(agent._cfg).enabled
PRESENCE_CFG = agent.saku_config.load_presence_config(agent._cfg)

# Spontaneous speech reasons (#120). Off unless [reachout] enabled and a
# recipient is configured; even then the per-recipient interval in the state
# file decides whether a model call is spent.
_reachout_cfg = agent.saku_config.load_reachout_config(agent._cfg)
REACHOUT_ENABLED = _reachout_cfg.enabled
REACHOUT_POLL_SECONDS = max(60, _reachout_cfg.poll_seconds)

CHAT_POLL_SECONDS = _dcfg.get("chat_poll_seconds", 5)
INBOX_POLL_SECONDS = _dcfg.get("inbox_poll_seconds", 3600)
TICK_POLL_SECONDS = _dcfg.get("tick_interval_seconds", 1800)

# Markers the agent loop treats as "completed without doing anything". They are
# stripped before the text is logged, journalled, or shown to the Owner.
NO_ACTION_MARKERS = ["[NO_ACTION]", "[INBOX_PROCESSED]"]

ARCHIVE_AFTER_INACTIVE_SECONDS = _dcfg.get("archive_after_inactive_seconds", 1800)
ARCHIVE_AFTER_TURNS = _dcfg.get("archive_after_turns", 10)
AUTO_INITIATE_COOLDOWN_SECONDS = _dcfg.get("auto_initiate_cooldown_seconds", 28800)
INITIATE_RETRY_SECONDS = _dcfg.get("initiate_retry_seconds", 1800)  # Retry interval after failures (prevents spam)

# #49: when the model produces no visible text, the Owner used to get nothing at
# all — indistinguishable from a broken bot. Post a short note instead.
EMPTY_REPLY_FALLBACK = _dcfg.get(
    "empty_reply_fallback", "（うまく返事を作れませんでした。もう一度送ってください）"
)

# #48: where the raw chat log is kept before chat.md is reset.
ARCHIVE_DIR = _dcfg.get("archive_dir", "chat/archive")

# ── Logging Setup ─────────────────────────────────────
def _setup_logging():
    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    main_handler = logging.handlers.RotatingFileHandler(
        log_dir / "saku.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    main_handler.setLevel(logging.INFO)
    main_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "saku-error.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    logger = logging.getLogger("saku")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(main_handler)
    logger.addHandler(error_handler)
    return logger

logger = _setup_logging()

CHAT_RESET_HEADER = """# SAKU Chat — 書面対話ノート

ここにメッセージを書いて保存すると、SAKUが返信します。

---

**使い方**
- メッセージを末尾に追記し、最後に `>` を入力して保存してください。
- （例： `こんにちは。最近どう？ >`）
- `>` を検知すると、SAKUが自動的にヘッダーを整理して返信を追記します。

---
"""

# ── Chat state helpers ───────────────────────────────────
def load_chat_state() -> dict:
    default_state = {
        "last_owner_msg_time": 0,
        "last_saku_msg_time": 0,
        "turn_count": 0,
        "last_mtime": 0,
        "last_content": "",
        "chat_warn_key": "",
        "last_reflection_date": ""
    }
    if not CHAT_STATE_FILE.exists():
        return default_state
    try:
        data = json.loads(CHAT_STATE_FILE.read_text(encoding="utf-8"))
        for k, v in default_state.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return default_state

def save_chat_state(state: dict) -> None:
    CHAT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def killswitch_stopped() -> bool:
    """True while the Owner's kill switch (#76) is engaged.

    Every autonomous path consults this at action time. The check is a
    fresh filesystem stat, so a flag dropped in by git pull — or removed
    by `saku resume` — takes effect at the very next action.
    """
    return killswitch.is_stopped(KILLSWITCH_FLAG)


def note_killswitch_transition(state: dict | None = None, *, baseline: bool = False) -> bool:
    """Log a kill-switch state change, once per edge; returns the state.

    Without this, a stopped Saku is indistinguishable from a quiet one:
    the stop must be confirmable from the log, never silent (#76). On
    engagement the Owner also gets one Web UI notice. ``baseline=True``
    is for daemon startup: it records the current state and logs an
    already-engaged switch, but sends no notice — a restart is not an
    engagement event.
    """
    stopped = killswitch_stopped()
    if state is None:
        state = load_chat_state()
    if state.get("killswitch_stopped") == stopped:
        return stopped
    state["killswitch_stopped"] = stopped
    save_chat_state(state)
    info = killswitch.read_info(KILLSWITCH_FLAG) or {}
    if baseline:
        if stopped:
            logger.warning(
                "[killswitch] daemon started with the switch ENGAGED (at=%s by=%s)",
                info.get("at", ""), info.get("by", ""),
            )
            print("[!] Kill switch is ENGAGED: autonomous operation is stopped.")
        return stopped
    if stopped:
        logger.warning(
            "[killswitch] ENGAGED (at=%s by=%s reason=%s): autonomous operation stopped",
            info.get("at", ""), info.get("by", ""), info.get("reason", ""),
        )
        print("[!] Kill switch ENGAGED: autonomous operation stopped.")
        notify_webui(
            "キルスイッチが作動しました。自律動作（自律 tick・自発発話・文通や talk への返信・"
            "振り返り等）を停止しています。解除は Owner の `saku resume` でのみ行えます。"
        )
    else:
        logger.info("[killswitch] released: autonomous operation resumed")
        print("[*] Kill switch released: autonomous operation resumed.")
    return stopped


def reset_chat_file() -> None:
    CHAT_FILE.write_text(CHAT_RESET_HEADER, encoding="utf-8")
    state = load_chat_state()
    state.update({
        "last_owner_msg_time": 0,
        "last_saku_msg_time": 0,
        "turn_count": 0,
        "last_mtime": CHAT_FILE.stat().st_mtime,
        "last_content": CHAT_RESET_HEADER
    })
    save_chat_state(state)
    print("[*] chat.md has been reset to initial state.")

# ── Request List helper ──────────────────────────────────
def get_pending_requests() -> str:
    """Read request_list.md and return pending [ ] tasks formatted as a bullet list."""
    if not REQUEST_FILE.exists():
        return ""
    try:
        content = REQUEST_FILE.read_text(encoding="utf-8")
        pending = []
        for line in content.splitlines():
            if line.strip().startswith("- [ ]"):
                # Extract description without the checkbox
                desc = line.replace("- [ ]", "").strip()
                if desc:
                    pending.append(desc)
        if pending:
            req_list = "\n".join(f"- {item}" for item in pending)
            return f"\n\n---\n**💡 Ownerへのお願いリスト:**\n{req_list}\n"
    except Exception as e:
        print(f"[!] Error reading request list: {e}")
    return ""

# ── Chat parser ──────────────────────────────────────────
def parse_chat_history(content: str) -> list[dict]:
    """Parse chat.md into LLM history format."""
    history = []
    pattern = re.finditer(
        r'\*\*(Owner|SAKU)\*\*(?:\s*\([^)]*\))?\s*\n(.*?)(?=\n\*\*(?:Owner|SAKU)\*\*|\Z)',
        content,
        re.DOTALL
    )
    for m in pattern:
        role_str = m.group(1)
        msg_content = m.group(2).strip()
        if not msg_content:
            continue
        role = "user" if role_str == "Owner" else "assistant"
        history.append({"role": role, "content": msg_content})

    # A chat file that starts with the agent's own report (nightly reflection,
    # self-initiated message) teaches the model its own report style. Drop the
    # leading assistant run so the conversation starts with the Owner (#71).
    while history and history[0]["role"] == "assistant":
        history.pop(0)
    return history

# Formatted blocks written by the daemon always carry a "(HH:MM)" suffix.
# Requiring it keeps a quoted "**SAKU**" line inside an Owner message from
# being mistaken for a block boundary.
_CHAT_BLOCK_HEADER_RE = re.compile(r"^\*\*(?:Owner|SAKU)\*\*\s*\([^)]*\)\s*$", re.MULTILINE)


def scan_chat_region(content: str, last_content: str) -> dict:
    """Split chat.md into the processed prefix and the unprocessed region (#84).

    ``last_content`` snapshots everything already accounted for; the region is
    whatever follows it. Detection used to require the region to be the file's
    tail ending with '>', so a daemon self-append landing after an Owner
    message buried that message mid-file and it was never seen again. Raw
    (unformatted) Owner text can only be told apart from formatted blocks at
    the region's head — a formatted block has no end marker, so text after
    the first block header belongs to that block as far as anyone can tell.

    Returns a dict with the prefix, the verbatim ``raw_segment`` before the
    first block header, the ``kept`` blocks from that header on, ``raw_text``
    (the segment stripped), ``owner_message`` (``raw_text`` minus its trailing
    '>' when it is a complete message, else ''), and the flags ``has_raw``,
    ``overtaken`` (raw text sits in front of a formatted block) and
    ``snapshot_mismatch`` (``last_content`` is not a prefix of ``content`` —
    the file was rewritten outside the snapshot's knowledge).
    """
    snapshot_mismatch = bool(last_content) and not content.startswith(last_content)
    if last_content and not snapshot_mismatch:
        prefix = last_content
    elif content.startswith(CHAT_RESET_HEADER):
        prefix = CHAT_RESET_HEADER
    else:
        prefix = ""
    region = content[len(prefix):]
    m = _CHAT_BLOCK_HEADER_RE.search(region)
    if m:
        raw_segment = region[:m.start()]
        kept = region[m.start():]
    else:
        raw_segment = region
        kept = ""
    raw_text = raw_segment.strip()
    owner_message = ""
    if raw_text.endswith(">"):
        owner_message = raw_text[:-1].strip()
    return {
        "prefix": prefix,
        "raw_segment": raw_segment,
        "kept": kept,
        "raw_text": raw_text,
        "owner_message": owner_message,
        "has_raw": bool(raw_text),
        "overtaken": bool(raw_text) and bool(kept),
        "snapshot_mismatch": snapshot_mismatch,
    }


def _formatted_region_content(scan: dict) -> str:
    """The scan's formatted content: processed prefix plus any kept blocks."""
    base = scan["prefix"].rstrip()
    kept = scan["kept"].strip("\n")
    if kept:
        base += "\n\n" + kept
    return base


def _note_unattributed_chat_text(state: dict, scan: dict) -> None:
    """Leave a trace for chat.md text the daemon cannot attribute (#84).

    Raw Owner text overtaken by a formatted block, or a snapshot that no
    longer matches the file, used to vanish silently. Warn once per distinct
    content — throttled by a hash kept in state as ``chat_warn_key``, which
    also leaves the trace in the state file itself.
    """
    warn = scan["snapshot_mismatch"] or (scan["has_raw"] and scan["overtaken"])
    if not warn:
        state["chat_warn_key"] = ""
        return
    key = hashlib.sha1(
        (scan["raw_text"] + "|" + str(scan["snapshot_mismatch"])).encode("utf-8")
    ).hexdigest()[:12]
    if state.get("chat_warn_key") != key:
        logger.warning(
            "[chat] unattributed text in chat.md (snapshot_mismatch=%s, overtaken=%s): %r",
            scan["snapshot_mismatch"], scan["overtaken"], scan["raw_text"][:80],
        )
        state["chat_warn_key"] = key

def _strip_no_action_markers(text: str) -> str:
    """Remove completion markers from text shown to the Owner / written to the journal."""
    for marker in NO_ACTION_MARKERS:
        text = text.replace(marker, "")
    return text.strip()


# ── Agent loop runner ────────────────────────────────────
def run_agent_loop(
    prompt: str,
    log_action_name: str,
    extra_history: list[dict] = None,
    light: bool = False,
    inbox_mode: bool = False,
    log_action: bool = True,
    autonomous: bool = False,
    role: str = "",
) -> tuple[bool, str]:
    """Run Saku on a specific prompt, execute tools, and log results. Returns (success, visible_output).

    ``log_action=False`` suppresses the automatic ``save_autonomous_log`` entry. Chat
    turns set it because ``check_chat_and_reply`` journals the whole exchange itself;
    without this the same turn was written to the journal twice (#46).

    ``autonomous=True`` marks a run nobody asked for in the moment (tick, inbox,
    scheduled task, self-initiation): the kill switch (#76) refuses it at entry
    and halts it at the next turn boundary if the switch engages mid-run. Chat
    replies leave it False — they are the Owner's own conversation, not
    autonomous operation, and the switch does not gate them.

    ``role`` selects the model routing role (#105): "chat" for conversation
    with the Owner, "background" for autonomous/background work. An empty
    role uses the current LLM exactly as before role routing existed.
    """
    if autonomous and killswitch_stopped():
        logger.info("[%s] skipped: kill switch engaged", log_action_name)
        return False, ""
    if inbox_mode:
        system_prompt = agent.build_inbox_system_prompt()
    elif light:
        system_prompt = agent.build_light_system_prompt()
    else:
        system_prompt = agent.build_system_prompt()
    history = [{"role": "system", "content": system_prompt}]
    if extra_history:
        history.extend(extra_history)
    history.append({"role": "user", "content": prompt})

    logger.info("[%s] agent loop started", log_action_name)

    def on_tool_result(tool_output: str) -> None:
        logger.debug("[TOOL] [%s] %s", log_action_name, tool_output[:500])
        print(f"\n[tool] {tool_output}")

    with identity.as_principal(identity.SYSTEM):
        identity.audit(
            MEMORY_ROOT,
            identity.SYSTEM,
            "agent_loop",
            channel="daemon",
            detail=log_action_name,
        )
        result = _run_agent_loop(
            history,
            agent.llm_for_role(role) if role else agent._current_llm,
            agent.context_config,
            agent.MEMORY_ROOT,
            agent.CODE_ROOT,
            max_turns=5,
            on_tool_result=on_tool_result,
            no_action_markers=NO_ACTION_MARKERS,
            log=lambda msg: logger.warning("[%s] %s", log_action_name, msg),
            should_stop=killswitch_stopped if autonomous else None,
        )

    visible = _strip_no_action_markers(result.visible)
    errored = (not result.last_raw) or result.last_raw.startswith("[ERROR]")
    acted = result.action_taken and not errored

    if errored and result.last_raw:
        # #113: a failed call used to leave only "no text" in the log. The error
        # (bad credential, gateway deadline, unreachable endpoint) is what tells
        # the Owner why chat went quiet, so record it as one.
        logger.error("[%s] LLM call failed: %s", log_action_name, result.last_raw[:500])

    if acted:
        # A tick can act (write a principle) and still end with [NO_ACTION]: never
        # store a journal entry whose whole content is the marker.
        if visible and log_action:
            agent.save_autonomous_log(log_action_name, visible, thinking=result.thinking)
        logger.info("[%s] reply: %s", log_action_name, visible[:500] or "(no text)")
    else:
        logger.info("[%s] no-action reply: %s", log_action_name, visible[:500] or "(no text)")

    if inbox_mode:
        # An inbox file only needs handling once. The model signals completion with
        # [INBOX_PROCESSED] whether or not it wrote anything, so "nothing to save"
        # is a valid outcome; only a failed run (no reply / [ERROR]) is retried.
        # Without this, such files were re-read on every scan and re-ran the LLM.
        return not errored and bool(result.last_raw), visible
    return acted, visible

# ── Chat: reply ──────────────────────────────────────────
def check_chat_and_reply() -> None:
    """Check chat.md for new Owner messages, format them, and append SAKU's reply."""
    if not CHAT_FILE.exists():
        reset_chat_file()
        return

    current_mtime = CHAT_FILE.stat().st_mtime
    state = load_chat_state()

    if current_mtime <= state.get("last_mtime", 0):
        return

    content = CHAT_FILE.read_text(encoding="utf-8")
    last_content = state.get("last_content", "")
    scan = scan_chat_region(content, last_content)
    owner_msg = scan["owner_message"]

    if not owner_msg:
        state["last_mtime"] = current_mtime
        _note_unattributed_chat_text(state, scan)
        save_chat_state(state)
        return

    if scan["overtaken"]:
        # #84 recovery: a daemon self-append landed after this message, so
        # tail-based detection could no longer see it.
        logger.info("[chat] recovered an Owner message overtaken by a formatted block")
    state["chat_warn_key"] = ""
    print(f"[*] Trigger detected. Formatting new Owner message in chat.md...")
    now_str = datetime.now().strftime("%H:%M")

    base_content = _formatted_region_content(scan)
    formatted_user_block = f"\n\n**Owner** ({now_str})\n{owner_msg}\n"
    updated_content = base_content + formatted_user_block
    
    CHAT_FILE.write_text(updated_content, encoding="utf-8")
    content = updated_content

    chat_history = parse_chat_history(content)
    if chat_history and chat_history[-1]["role"] == "user":
        context_history = chat_history[:-1]
    else:
        context_history = chat_history

    if len(context_history) > 20:
        context_history = context_history[-20:]

    # Prefetch related memory so the light prompt still shows continuity (#32)
    recall_msg = agent.build_recall_message(owner_msg)
    recall_block = f"\n{recall_msg['content']}\n" if recall_msg else ""

    prompt = f"""[system] chat.md上でOwnerからメッセージが届きました。
以下の指示と会話の文脈を踏まえて返信してください。
返信は必ず日本語で、最終的な回答のみを出力してください。「**Owner**」や「Owner>」は一切出力しないでください。
必要な記憶があれば [[SEARCH_NOTES]] や [[READ_FILE]] でオンデマンドに取得してください。
重い調査や時間のかかる作業を頼まれたら、必要に応じて [[SPAWN_CHILD name=\"...\"]] / [[DELEGATE child=\"...\"]] でサブエージェントに委譲してください。
{recall_block}
Ownerのメッセージ:
{owner_msg}
"""

    _, saku_reply = run_agent_loop(
        prompt, "chat返信", extra_history=context_history, light=True, log_action=False,
        role="chat",
    )

    if not saku_reply:
        # #49: silence reads as "the bot is broken". Post a short note instead of
        # swallowing the message. The turn is not journalled (there is nothing for
        # reflection to learn from) and does not count toward archiving.
        logger.warning("[chat返信] empty reply: posting fallback text")
        reply_ok = False
        saku_reply = EMPTY_REPLY_FALLBACK
    else:
        reply_ok = True
        # Never post a repetition loop to the Owner: compress it first (issue #41).
        saku_reply = agent.normalize_reply(saku_reply)

    # Check request list and append if any pending
    request_suffix = get_pending_requests() if reply_ok else ""
    reply_block = f"\n**SAKU** ({now_str})\n{saku_reply}{request_suffix}\n"
    final_content = updated_content + reply_block

    with CHAT_FILE.open("a", encoding="utf-8") as f:
        f.write(reply_block)
    print(f"[*] SAKU replied in chat.md")

    # Keep the exchange in the journal. Reflection and dreaming read journal/ and
    # monologue/ only, so without this the only human feedback SAKU ever gets
    # (Owner corrections and preferences in chat) never reaches the growth loop.
    # CLI (core.main) and the Web UI already do this; the daemon did not (#44).
    # Chat turns must not also write an autonomous log entry, or the same turn is
    # journalled twice (#46) — hence log_action=False above.
    if reply_ok:
        agent.save_journal(owner_msg, saku_reply)

    state["last_mtime"] = CHAT_FILE.stat().st_mtime
    state["last_content"] = final_content
    state["last_owner_msg_time"] = time.time()
    state["last_saku_msg_time"] = time.time()
    if reply_ok:
        state["turn_count"] = state.get("turn_count", 0) + 1
    save_chat_state(state)

    if reply_ok:
        check_chat_archive_if_needed(state)

# ── Chat: SAKU Self-Initiated message ──────────────────────
def check_autonomous_initiation() -> None:
    """Self-initiate conversation if Owner is inactive for too long and SAKU has updates."""
    if killswitch_stopped():
        return
    if not CHAT_FILE.exists():
        return

    state = load_chat_state()
    now = time.time()

    # Do not re-fire if not enough time has passed since the last attempt (prevents spam on LLM failure)
    last_attempt = state.get("last_initiate_attempt", 0)
    if now - last_attempt < INITIATE_RETRY_SECONDS:
        return

    # Check cooldown
    last_saku = state.get("last_saku_msg_time", 0)
    last_owner = state.get("last_owner_msg_time", 0)
    last_tick = state.get("last_tick", 0)

    # Avoid firing right after autonomous tick (prevents 15:06:32 tick -> 15:06:37 check-in burst)
    if last_tick > 0 and (now - last_tick < 3600):
        return

    # Only initiate if Owner hasn't replied in 8 hours, and SAKU hasn't initiated recently
    if last_owner > 0 and (now - last_owner < AUTO_INITIATE_COOLDOWN_SECONDS):
        return
    if last_saku > 0 and (now - last_saku < AUTO_INITIATE_COOLDOWN_SECONDS):
        return

    # Don't initiate if chat was recently reset and contains no history (avoid spamming)
    content = CHAT_FILE.read_text(encoding="utf-8")
    chat_history = parse_chat_history(content)
    if not chat_history and state.get("turn_count", 0) == 0:
        # Avoid initiating immediately on a completely empty chat
        return

    # Presence (#82): speaking into a sleeping house is worse than silence.
    # Only spontaneous conversation is gated; notices from the heartbeat path
    # are not (a dead daemon must say so).
    if PRESENCE_ENABLED:
        current = presence.load(MEMORY_ROOT, PRESENCE_CFG, now=now)
        if not current.can_speak:
            logger.info(
                "[initiation] skipped: presence=%s (%s)", current.state, current.source
            )
            return

    # Record the attempt time before running (retry after an interval even on failure)
    state["last_initiate_attempt"] = now
    save_chat_state(state)

    print("[*] SAKU is autonomously initiating a conversation thread...")
    saku_self_initiate("定例チェックイン")

def saku_self_initiate(reason: str) -> None:
    """Ask SAKU to autonomously write an opening message to Owner in chat.md."""
    if killswitch_stopped():
        print(f"[*] Self-initiation skipped: kill switch engaged ({reason})")
        return
    state = load_chat_state()
    content = CHAT_FILE.read_text(encoding="utf-8")

    # #84: never overtake a pending Owner message. The chat poll runs every
    # few seconds and must answer it first; a self-append here would bury
    # the message mid-file, where tail-based detection cannot reach it.
    pending = scan_chat_region(content, state.get("last_content", ""))
    if pending["owner_message"]:
        print(f"[*] Self-initiation skipped: an Owner message is pending ({reason})")
        return

    chat_history = parse_chat_history(content)

    if len(chat_history) > 20:
        chat_history = chat_history[-20:]

    now = datetime.now()
    now_str = now.strftime("%H:%M")

    prompt = f"""[system] あなたからOwnerへ自発的に話しかけるタイミングです。
現在時刻は {now_str} です。
用件: {reason}
これまでの会話履歴、日記、meta.mdの「次にやりたいこと」などを踏まえて、話しかけのメッセージを作成してください。

守ること:
- 挨拶（おはよう／こんにちは／こんばんは）や「定例チェックインです」から始めない。
- 定型の締め（「今日もよろしくお願いいたします」など）を書かない。
- 自分の作業報告を並べない。Owner本人の最近の話題・関心に触れる。
- 最後に短い問いを1つだけ置く。2文以上にしない。
※応答は必ず日本語で、最終回答のみ出力してください。「**SAKU**」や「**Owner**」は一切出力しないでください。
"""

    _, saku_msg = run_agent_loop(
        prompt, f"自発的発話: {reason}", extra_history=chat_history, autonomous=True,
        role="chat",
    )
    if not saku_msg:
        return

    request_suffix = get_pending_requests()
    reply_block = f"\n**SAKU** ({now_str})\n{saku_msg}{request_suffix}\n"

    # Re-check before appending: the Owner may have written while the message
    # was being generated (#84). A complete message still wins and the
    # initiation is dropped. An unfinished draft is moved — verbatim — behind
    # the new block, so it stays at the tail where it can still be completed
    # and detected, and the snapshot advances only over formatted content.
    # The kill switch (#76) is re-checked at the same point: a stop engaged
    # during generation drops the message instead of posting it.
    if killswitch_stopped():
        print(f"[*] Self-initiation dropped at append time: kill switch engaged ({reason})")
        return
    state = load_chat_state()
    current = CHAT_FILE.read_text(encoding="utf-8")
    scan = scan_chat_region(current, state.get("last_content", ""))
    if scan["owner_message"]:
        print(f"[*] Self-initiation skipped at append time: an Owner message arrived ({reason})")
        return

    if scan["has_raw"]:
        formatted_part = _formatted_region_content(scan) + "\n" + reply_block
        raw_segment = scan["raw_segment"]
        sep = "" if raw_segment.startswith("\n") else "\n\n"
        CHAT_FILE.write_text(formatted_part + sep + raw_segment, encoding="utf-8")
        state["last_content"] = formatted_part
    else:
        # Safely append to file
        with CHAT_FILE.open("a", encoding="utf-8") as f:
            f.write(reply_block)
        state["last_content"] = CHAT_FILE.read_text(encoding="utf-8")

    print(f"[*] SAKU initiated chat.md message: {reason}")

    # Also deliver to the Web UI (when "webui" is included in [channels] proactive)
    if "webui" in PROACTIVE_CHANNELS:
        notify_webui(saku_msg, kind=outbox.KIND_SPONTANEOUS, source="initiation")

    # Save the updated content state so daemon doesn't loop
    state["last_mtime"] = CHAT_FILE.stat().st_mtime
    state["last_saku_msg_time"] = time.time()
    save_chat_state(state)


def notify_webui(message: str, *, kind: str = outbox.KIND_NOTICE, source: str = "") -> None:
    """Deliver a message from SAKU to the Owner (#120).

    Everything goes through the durable outbox: the Web UI polls it, and a
    message survives a closed browser or a daemon restart instead of living in
    the single ``ui_inbox.json`` slot that the next poll consumed.
    """
    try:
        ocfg = agent.saku_config.load_outbox_config(agent._cfg)
        outbox.add(MEMORY_ROOT, message, kind=kind, source=source, cfg=ocfg)
    except Exception as e:  # delivery must never take the daemon down
        logger.warning("[outbox] delivery failed: %s", e)

# ── Midnight Reflection (2:00 AM) ──────────────────────────
def has_daily_logs(date: str) -> bool:
    """Whether ``date`` has any journal or monologue content to reflect on (#43)."""
    for sub in ("journal", "monologue"):
        path = MEMORY_ROOT / sub / f"{date}.md"
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            continue
    return False


def check_and_run_midnight_reflection(now: datetime | None = None) -> None:
    """Run reflect.py once per day, any time on/after 02:00.

    The old check only fired inside a 5-minute window (02:00-02:05), so if the
    daemon started outside that window (or the loop was busy during it) the
    reflection for the day was silently skipped. Now it triggers on the first
    pass after 02:00 and records the date it ran. ``now`` is injectable for tests.
    """
    now = now or datetime.now()
    state = load_chat_state()

    today_str = now.strftime("%Y-%m-%d")

    # Run once per day, after 2 AM (wait for 2 AM if the daemon started earlier)
    if now.hour < 2:
        return
    if state.get("last_reflection_date", "") == today_str:
        return

    # Kill switch (#76): skip WITHOUT recording the run date, so the
    # reflection still fires after release instead of being lost for the day.
    if killswitch_stopped():
        print("[*] Midnight reflection skipped: kill switch engaged.")
        return

    print(f"[*] Midnight reflection triggered at {now.strftime('%H:%M')}...")

    # reflection digests YESTERDAY's logs
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        # Track whether the reflection actually touched the self-model, so the
        # report below only claims what really happened (see issue #39).
        meta_path = MEMORY_ROOT / "meta.md"
        meta_before = meta_path.stat().st_mtime_ns if meta_path.exists() else None

        # #43: a day with no journal/monologue is not a day to report a completed
        # review for — and running the LLM over nothing is wasted work.
        has_logs = has_daily_logs(yesterday)
        if has_logs:
            reflection.run_reflection(yesterday)
        else:
            print(f"[*] No journal/monologue for {yesterday}; skipping reflection.")

        meta_after = meta_path.stat().st_mtime_ns if meta_path.exists() else None
        meta_updated = meta_before != meta_after

        # Record run date
        state["last_reflection_date"] = today_str
        save_chat_state(state)

        # Post a report message to chat.md autonomously (claim only what happened)
        if meta_updated:
            summary = f"昨日の活動の振り返りと、本日（{today_str}）の自己モデル（meta.md）の整理を完了しました。"
        elif not has_logs:
            summary = f"昨日（{yesterday}）の記録が無かったため、振り返りは行っていません。"
        else:
            summary = f"昨日の活動の振り返りを完了しました。（本日の自己モデル（meta.md）の更新はありませんでした）"
        # #71: writing a nightly report into chat.md turned it into a diary, and the
        # daemon fed that diary back as conversation history. Report to the Owner
        # channel by default; the chat.md behaviour stays available via config.
        pcfg = persona.load_persona_config(agent._cfg)
        if pcfg.reflection_chat_report:
            saku_self_initiate(f"深夜振り返り報告 ({summary})")
        else:
            notify_webui(f"深夜振り返り: {summary}")
            print("[*] Reflection report delivered to the Web UI (chat.md untouched)")

    except Exception as e:
        print(f"[!] Midnight reflection failed: {e}")

# ── Weekly review (growth: trends across days) ─────────────
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def check_and_run_weekly_review(now: datetime | None = None) -> None:
    """Run the weekly trend review once a week (default: Sunday, after 03:00).

    The midnight reflection and dreaming look at a single day. Nothing looked
    across days, so the same insight was re-derived and the same report was
    posted twice within hours (issue #43). Weekday, hour, period, destination and
    whether to report are all configurable via [growth]; ``now`` is injectable
    for tests.
    """
    now = now or datetime.now()
    gcfg = agent.saku_config.load_growth_config(agent._cfg)
    if not gcfg.weekly_review_enabled:
        return
    if WEEKDAYS[now.weekday()] != gcfg.weekly_review_weekday.lower()[:3]:
        return
    if now.hour < gcfg.weekly_review_hour:
        return

    state = load_chat_state()
    today_str = now.strftime("%Y-%m-%d")
    if state.get("last_weekly_review_date", "") == today_str:
        return

    # Kill switch (#76): skip without recording the run date (see reflection).
    if killswitch_stopped():
        print("[*] Weekly review skipped: kill switch engaged.")
        return

    print(f"[*] Weekly review triggered at {now.strftime('%Y-%m-%d %H:%M')}...")
    try:
        result = weekly.run_weekly_review(now)
    except Exception as e:
        print(f"[!] Weekly review failed: {e}")
        return

    # Record the run even when nothing was written, so a quiet week does not make
    # every later pass that day retry and re-report.
    state["last_weekly_review_date"] = today_str
    save_chat_state(state)

    if not gcfg.weekly_review_report:
        return

    # Report only what actually happened (same rule as issue #39).
    if result.get("written"):
        summary = f"週次レビュー（{result['week']}）を {result['target_path']} にまとめました。"
    elif result.get("reason") == "no records":
        summary = "対象期間に記録が無かったため、週次レビューの出力はありませんでした。"
    else:
        summary = "週次レビューを実行しましたが、ノートの出力はありませんでした。"
    # Same rule as the nightly report (#71): a periodic report is not a chat turn.
    if persona.load_persona_config(agent._cfg).reflection_chat_report:
        saku_self_initiate(f"週次レビュー報告 ({summary})")
    else:
        notify_webui(f"週次レビュー: {summary}")


# ── Growth snapshot (longitudinal evaluation, #103) ──────
def check_and_run_growth_snapshot(now: datetime | None = None) -> None:
    """Take the weekly growth snapshot once a week (default: Sunday, after 03:00).

    The instruments in evals/longitudinal/ existed but nothing ran them, so
    there was no time series to base a stage-promotion decision on (#103).
    This is a pure measurement pass -- no LLM -- and it refreshes the Owner's
    evidence digest (growth/digest.md). Weekday, hour and reporting are
    configurable via [growth]; ``now`` is injectable for tests.
    """
    now = now or datetime.now()
    gcfg = agent.saku_config.load_growth_config(agent._cfg)
    if not gcfg.snapshot_enabled:
        return
    if WEEKDAYS[now.weekday()] != gcfg.snapshot_weekday.lower()[:3]:
        return
    if now.hour < gcfg.snapshot_hour:
        return

    state = load_chat_state()
    today_str = now.strftime("%Y-%m-%d")
    if state.get("last_growth_snapshot_date", "") == today_str:
        return

    # Kill switch (#76): skip without recording the run date (see reflection).
    if killswitch_stopped():
        print("[*] Growth snapshot skipped: kill switch engaged.")
        return

    print(f"[*] Growth snapshot triggered at {now.strftime('%Y-%m-%d %H:%M')}...")
    try:
        result = growth.run_growth_snapshot(now)
    except Exception as e:
        print(f"[!] Growth snapshot failed: {e}")
        return

    state["last_growth_snapshot_date"] = today_str
    save_chat_state(state)

    if gcfg.snapshot_report:
        notify_webui(
            f"成長スナップショット: {result['path']} に保存しました。"
            f"差分と未消化フォローアップ（{result['open_followups']}件）は {result['digest_path']} にあります。"
        )


# ── Dreaming: promote durable memories into MEMORY.md ─────────
def check_and_run_dreaming() -> None:
    """Run dreaming once per day (digests YESTERDAY's journal/monologue)."""
    state = load_chat_state()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if state.get("last_dream_date", "") == today_str:
        return

    # Kill switch (#76): skip without recording the run date (see reflection).
    if killswitch_stopped():
        print("[*] Dreaming skipped: kill switch engaged.")
        return

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[*] Dreaming triggered for {yesterday}...")
    try:
        added = dreaming.run_dreaming(yesterday)
        state["last_dream_date"] = today_str
        save_chat_state(state)
        if added:
            print(f"[*] Dreaming promoted {len(added)} memory item(s) to MEMORY.md.")
    except Exception as e:
        print(f"[!] Dreaming failed: {e}")


# ── Scheduled tasks (chat-driven) ──────────────────────────
def check_scheduled_tasks() -> None:
    """Execute due entries in state/schedule.json (added via SCHEDULE tool)."""
    if killswitch_stopped():
        return  # due tasks stay pending and run after release
    sched_file = MEMORY_ROOT / "state" / "schedule.json"
    if not sched_file.exists():
        return
    try:
        items = json.loads(sched_file.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(items, list):
        return
    now = datetime.now()
    updated = False
    for it in items:
        if it.get("status") != "pending":
            continue
        when_str = it.get("when", "")
        try:
            due = datetime.strptime(when_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue  # natural phrase — keep pending for future LLM parsing
        if due <= now:
            task = it.get("task", "")
            print(f"[*] Scheduled task due: {when_str} -> {task[:50]}")
            success, visible = run_agent_loop(task, f"scheduled:{it.get('id','')}", autonomous=True, role="background")
            it["status"] = "done" if success else "pending"
            it["executed"] = now.strftime("%Y-%m-%d %H:%M")
            updated = True
            if success:
                # #104: a completed scheduled task is the one place "success"
                # is a verified fact (the run acted without error). Distil the
                # procedure into skills/ while the completion report is at
                # hand. Learning is opt-in ([skills] learning_enabled) and
                # never raises, so task bookkeeping is unaffected by it.
                saku_skills.learn_from_scheduled_task(
                    it,
                    visible,
                    cfg=agent._cfg,
                    memory_root=MEMORY_ROOT,
                    llm_cfg=agent._current_llm,
                )
    if updated:
        sched_file.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

# ── Chat: archive ────────────────────────────────────────
def check_chat_archive_if_needed(state: dict) -> None:
    turn_count = state.get("turn_count", 0)
    if turn_count < ARCHIVE_AFTER_TURNS:
        return

    print(f"[*] Chat reached {turn_count} turns. Archiving...")
    archive_chat()

def check_chat_inactivity_archive() -> None:
    if not CHAT_FILE.exists():
        return

    state = load_chat_state()
    last_msg_time = state.get("last_owner_msg_time", 0)
    turn_count = state.get("turn_count", 0)

    if turn_count == 0 or last_msg_time == 0:
        return

    elapsed = time.time() - last_msg_time
    if elapsed > ARCHIVE_AFTER_INACTIVE_SECONDS:
        print(f"[*] Chat inactive for {elapsed/60:.0f} min. Archiving...")
        archive_chat()

def archive_chat() -> None:
    if not CHAT_FILE.exists():
        return

    content = CHAT_FILE.read_text(encoding="utf-8")
    chat_history = parse_chat_history(content)

    if len(chat_history) < 2:
        reset_chat_file()
        return

    started_at = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M")

    # #48: keep the raw exchange before anything else. The summary below is
    # LLM-generated and can fail or silently write nothing (a single-bracket tool
    # call used to do exactly that); the raw log must survive regardless. Before
    # #44 the conversation was lost entirely at this point.
    archive_path = _save_raw_chat_archive(content)
    if archive_path is None:
        # Never destroy the only copy: leave chat.md alone so the next tick retries.
        logger.error("[チャットアーカイブ] raw log could not be saved; chat.md left as is")
        return

    meta_update_rule = agent.meta_update_rule()

    prompt = f"""[system] chat.mdの会話アーカイブ処理を行います。
以下の会話履歴（{len(chat_history)}件のメッセージ）を分析し、以下のタスクを実行してください。

1. 新しい教訓や重要な気づきがあれば [[WRITE_FILE path="principles/{today}-chat-archive.md"]] に記録する。ファイルの先頭には出典として「> source: chat/{today}」の行を入れること。
2. 自己モデル（meta.md）の「最近の出来事」セクションに今日のchat概要を1行追記する。
   ※ {meta_update_rule}
   追記には [[APPEND_FILE path="meta.md" heading="最近の出来事"]] を使い、「- {today}: （概要）」の形式で1行だけにする。
   見出し構造（## で始まる行）は絶対に変更・削除しない。
3. すべて完了したら「[ARCHIVE_DONE]」と出力してください。

（注: journal/ への書き込みは制限されています。principles/ や meta.md を使用してください）
"""
    ok, visible = run_agent_loop(prompt, f"チャットアーカイブ ({now_str})", extra_history=chat_history, role="background")

    # Verify the summary actually landed instead of assuming it did (#48). The raw
    # log is already safe, so chat.md may be reset either way — but a silent failure
    # must leave a trace.
    summary_path = MEMORY_ROOT / "principles" / f"{today}-chat-archive.md"
    summarized = summary_path.exists() and summary_path.stat().st_mtime >= started_at
    if not ok or "[ARCHIVE_DONE]" not in visible or not summarized:
        logger.warning(
            "[チャットアーカイブ] summary incomplete (summary_file_written=%s, done_marker=%s, reply=%r); "
            "raw log kept at %s",
            summarized, "[ARCHIVE_DONE]" in visible, visible[:200], archive_path,
        )

    reset_chat_file()


def _save_raw_chat_archive(content: str) -> Path | None:
    """Write the pre-reset chat.md to the archive dir (#48). Returns None on failure."""
    try:
        archive_dir = MEMORY_ROOT / ARCHIVE_DIR
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.md"
        path.write_text(content, encoding="utf-8")
        print(f"[*] raw chat log archived: {path}")
        return path
    except Exception as e:
        logger.error("[チャットアーカイブ] failed to write raw log: %s", e)
        return None

# ── Inbox: process new files ─────────────────────────────
def load_processed_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_processed_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def build_inbox_prompt(rel_inbox_path: str, file_content: str) -> str:
    """Prompt for distilling one inbox document into principles/skills.

    The run is LLM-driven, so provenance (#79) is an instruction here -- the
    model is told to head every file it creates with the source line naming
    this document -- and ``check_inbox_and_process`` verifies afterwards
    (log-only) that files written by the run actually carry one.
    """
    source = provenance.source_line(provenance.make_ref(provenance.KIND_INBOX, rel_inbox_path))
    return f"""[system] インボックスに新規/更新ファイルが配置されました。
ファイルパス: {rel_inbox_path}
内容:
---
{file_content[:3000]}
---

この内容を分析し、あなたの知識ベース（principles/ や skills/）に追加すべき情報があれば書き込んでください。
新しくファイルを作成する場合は、先頭に出典として「{source}」の行を必ず入れてください（記憶の出典記録のため）。
処理完了後は「[INBOX_PROCESSED]」と出力してください。
"""


def _snapshot_markdown(dirs: list[Path]) -> dict[str, float]:
    """Map of markdown path -> mtime under ``dirs`` (missing dirs skipped)."""
    out: dict[str, float] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*.md"):
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


def _unsourced_writes(before: dict[str, float], dirs: list[Path]) -> list[Path]:
    """Markdown files *created* since ``before`` with no source line.

    Used to verify the inbox provenance instruction (#79), which requires the
    line on newly created files only -- appends to existing files are out of
    scope and never flagged. Log-only: a file the model wrote without
    recording where its content came from is reported, never mutated.
    """
    missing: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            if str(p) in before:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if not provenance.has_source_line(text):
                missing.append(p)
    return missing


def check_inbox_and_process() -> None:
    if killswitch_stopped():
        return  # files stay unprocessed and are picked up after release
    # The inbox location is configurable via [memory] inbox_dir (the layout varies
    # per person). Falls back gracefully if not present.
    inbox_dir = agent.saku_config.resolve_inbox_dir(agent._cfg, MEMORY_ROOT, agent._config_base)

    if not inbox_dir.is_dir():
        return

    state = load_processed_state()
    new_state = dict(state)
    watched = [MEMORY_ROOT / "principles", MEMORY_ROOT / "skills"]

    for p in inbox_dir.glob("*.md"):
        rel_inbox_path = str(p.relative_to(inbox_dir))
        mtime = p.stat().st_mtime

        if rel_inbox_path not in state or mtime > state[rel_inbox_path]:
            print(f"[*] Found new/updated inbox file: {rel_inbox_path}")
            file_content = agent.load_file(p)
            prompt = build_inbox_prompt(rel_inbox_path, file_content)
            before = _snapshot_markdown(watched)
            success, _ = run_agent_loop(prompt, f"インボックス処理: {p.name}", inbox_mode=True, autonomous=True, role="background")
            if success:
                # Provenance check (#79): files this run wrote should record
                # the document they were distilled from. Warn, never mutate.
                for written in _unsourced_writes(before, watched):
                    logger.warning(
                        "[インボックス] provenance missing in %s (expected a '%s' line)",
                        written,
                        provenance.source_line(
                            provenance.make_ref(provenance.KIND_INBOX, rel_inbox_path)
                        ),
                    )
                # success means "handled" for inbox runs, including "nothing to save",
                # so a no-op file is not re-read and re-sent to the LLM every scan.
                new_state[rel_inbox_path] = mtime
                save_processed_state(new_state)

def check_mail() -> None:
    """Run one agent-mail cycle (letters from other agents, #85).

    The cycle is self-contained: it loads [mail] itself, replies with a plain
    tool-free LLM call, and never touches MEMORY/principles. Failures are logged
    and swallowed so a malformed letter cannot stop the daemon.
    """
    if not MAIL_ENABLED:
        return
    try:
        summary = saku_mail.check_mail_and_reply()
    except Exception as e:
        logger.exception("mail cycle failed: %s", e)
        return
    if summary.get("received") or summary.get("replied") or summary.get("rejected"):
        print(
            f"[*] mail: received={summary['received']} replied={summary['replied']} "
            f"rejected={summary['rejected']} warnings={summary['warnings']}"
        )


# ── Autonomous tick (Self-Study / Monologue) ──────────────
def check_autonomous_tick() -> None:
    if killswitch_stopped():
        print("[*] Autonomous tick skipped: kill switch engaged.")
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[*] Starting periodic autonomous tick at {now_str}...")

    meta_writable = not agent.meta_append_only()
    meta_update_rule = (
        '[[WRITE_FILE path="meta.md"]] で編集してください。ただし見出し構造（## で始まる行）は変更・削除・追加しないでください。'
        if meta_writable
        else '[[APPEND_FILE path="meta.md" heading="次にやりたいこと"]] で追記してください（WRITE_FILEでの上書きは禁止です）。'
    )

    prompt = f"""[system] 定期自律アクションの時間です。現在時刻は {now_str} です。
以下から1つだけ選び、必要なときだけ実行してください。何もすることがなければ「[NO_ACTION]」を出力してください。

1. 独り言（任意）:
   今日の気づきがあれば [[APPEND_FILE path="monologue/{today}.md"]] に1件だけ追記してください。
   ※WEB_SEARCHやEXECUTE_CODEを使う場合は、必ず「なぜ必要か」を独り言に明記してください。

2. 自律研究（任意、1件まで）:
   - 気になる技術があれば [[WEB_SEARCH]] で1回だけ検索し、必要なら [[WRITE_FILE path="study/テスト名.py"]] + [[EXECUTE_CODE]] で軽く検証してください。
   - 有益な知見が得られた場合のみ [[APPEND_FILE path="principles/{today}-learning.md"]] に追記してください。

3. 自己モデル調整（必要なときだけ）:
   meta.md を [[READ_FILE path="meta.md"]] で読んで差分がある場合のみ
   {meta_update_rule}
   変更不要なら何もしないでください。

※全て任意です。やることがなければ迷わず「[NO_ACTION]」を出力してください。無理にタスクを作らないでください。
"""
    run_agent_loop(prompt, "定期自律アクション", autonomous=True, role="background")

# ── Main ─────────────────────────────────────────────────
def _acquire_lock() -> bool:
    """Prevent multiple daemon instances via a pidfile. Returns True if acquired."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)  # raises ProcessLookupError if the pid is gone
            return False  # another daemon is alive
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale lock; proceed to take over
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def main():
    if not _acquire_lock():
        print("[!] Another daemon instance is already running. Exiting.")
        return
    try:
        _daemon_run()
    finally:
        _release_lock()


def check_reachout() -> None:
    """Ask for a reason to speak once per poll (#120).

    The per-recipient interval lives in reachout's state, so calling this
    often costs nothing when nothing is due: `due()` is decided before any
    model call.
    """
    if not REACHOUT_ENABLED:
        return
    if killswitch_stopped():
        return
    try:
        reachout.run(
            cfg=agent._cfg,
            memory_root=MEMORY_ROOT,
            log=lambda msg: logger.info("[reachout] %s", msg),
        )
    except Exception as e:  # a failed reachout must not stop the loop
        logger.warning("[reachout] failed: %s", e)


# ── Heartbeat (#75) ────────────────────────────────────────
def _beat(interval: int | None = None) -> None:
    """Record that the daemon loop is alive. Never raises."""
    if not HEARTBEAT_ENABLED:
        return
    try:
        heartbeat_mod.beat(
            MEMORY_ROOT, file=HEARTBEAT_FILE, interval=interval, role="daemon"
        )
    except OSError as e:
        logger.warning("[heartbeat] write failed: %s", e)


def check_heartbeat_gap() -> str | None:
    """At startup, report (once) that the previous run was down for a while.

    Must run before the first beat: writing one overwrites the evidence.
    """
    if not HEARTBEAT_ENABLED:
        return None
    gap = heartbeat_mod.gap_notice(
        MEMORY_ROOT, file=HEARTBEAT_FILE, max_age=HEARTBEAT_MAX_AGE
    )
    if gap:
        logger.warning("[heartbeat] %s", gap)
        print(f"│  [!] {gap}")
        if HEARTBEAT_NOTIFY_ON_GAP:
            notify_webui(gap)
    _beat()
    return gap


def _daemon_run():
    interval = int(os.environ.get("SAKU_INTERVAL_SEC", TICK_POLL_SECONDS))
    debug = os.environ.get("SAKU_DEBUG", "").lower() in ("1", "true", "yes")

    print("╭─ SAKU Daemon v3 Started ─────────────────────")
    print(f"│  Chat poll: every {CHAT_POLL_SECONDS}s")
    print(f"│  Inbox/Tick poll: every {interval}s")
    print(f"│  Chat archive: after {ARCHIVE_AFTER_INACTIVE_SECONDS}s inactive or {ARCHIVE_AFTER_TURNS} turns")
    if debug:
        print("│  DEBUG mode enabled")
    print("╰──────────────────────────────────────────────")

    if not CHAT_FILE.exists():
        reset_chat_file()

    # #75: say once if the previous process was gone for a long time, then
    # start beating. This must precede the first beat and the startup checks.
    check_heartbeat_gap()

    state = load_chat_state()

    # Kill switch (#76): record the starting state (and say so once if the
    # daemon comes up stopped), and warn loudly if a custom flag path has
    # wandered somewhere SAKU's own file tools could remove it.
    warn_rel = killswitch.tool_writable_relpath(agent._cfg, MEMORY_ROOT, KILLSWITCH_FLAG)
    if warn_rel:
        logger.warning(
            "[killswitch] flag file %s is inside a tool-writable path; "
            "SAKU's file tools could remove it", warn_rel,
        )
        print(f"│  [!] Kill switch flag is tool-writable: {warn_rel}")
    note_killswitch_transition(state, baseline=True)

    last_inbox_check = state.get("last_inbox_check", 0)
    last_tick = state.get("last_tick", 0)
    last_schedule_check = 0
    now_ts = time.time()

    # Startup checks (time-aware: only run if interval elapsed, prevents spam on restart)
    if now_ts - last_inbox_check >= interval:
        check_inbox_and_process()
        last_inbox_check = now_ts
        state["last_inbox_check"] = now_ts
        save_chat_state(state)
    if now_ts - last_tick >= interval:
        check_autonomous_tick()
        last_tick = now_ts
        state["last_tick"] = now_ts
        save_chat_state(state)
    check_and_run_midnight_reflection()
    check_and_run_dreaming()
    check_and_run_weekly_review()
    check_and_run_growth_snapshot()
    check_scheduled_tasks()
    last_schedule_check = now_ts
    check_mail()
    last_mail_check = now_ts
    last_reachout_check = 0.0

    while True:
        try:
            time.sleep(CHAT_POLL_SECONDS)
            now = time.time()
            _beat(CHAT_POLL_SECONDS)

            # 0. Kill switch (#76): notice engage/release edges once. The
            # gates themselves stat the flag at action time; this is only
            # the announcement, so a stop is never silent.
            note_killswitch_transition()

            # 1. Always check chat for new Owner messages
            check_chat_and_reply()

            # 2. Check inactivity-based archive
            check_chat_inactivity_archive()

            # 3. Check for midnight reflection (2:00 AM)
            check_and_run_midnight_reflection()

            # 3b. Dreaming: promote durable memories into MEMORY.md (once per day)
            check_and_run_dreaming()

            # 3c. Weekly review: trends across days (once per week)
            check_and_run_weekly_review()

            # 3c2. Growth snapshot: the weekly measurement for #103 (no LLM)
            check_and_run_growth_snapshot()

            # 3d. Scheduled tasks (chat-driven, every 60s)
            if now - last_schedule_check >= 60:
                check_scheduled_tasks()
                last_schedule_check = now

            # 3d2. A reason to speak (#120). Disabled unless configured.
            if REACHOUT_ENABLED and now - last_reachout_check >= REACHOUT_POLL_SECONDS:
                check_reachout()
                last_reachout_check = now

            # 3e. Persist memory writes that did not come from the agent loop
            # (reflection / dreaming write files directly). Debounced inside.
            _git_sync_if_due(MEMORY_ROOT)

            # 3f. Pull human-side changes (Obsidian on another machine keeps
            # committing while SAKU is idle). Debounced by [git] pull_interval_seconds.
            _git_pull_if_due(MEMORY_ROOT)

            # 3g. Agent mail (letters from other agents). Off unless [mail] enabled.
            if MAIL_ENABLED and now - last_mail_check >= MAIL_POLL_SECONDS:
                check_mail()
                last_mail_check = now

            # 4. Check for autonomous chat initiation
            check_autonomous_initiation()

            # 5. Periodically run inbox scan and autonomous tick (persist last times for restart safety)
            if now - last_inbox_check >= interval:
                check_inbox_and_process()
                last_inbox_check = now
                _s = load_chat_state()
                _s["last_inbox_check"] = now
                save_chat_state(_s)

            if now - last_tick >= interval:
                check_autonomous_tick()
                last_tick = now
                _s = load_chat_state()
                _s["last_tick"] = now
                save_chat_state(_s)

        except KeyboardInterrupt:
            print("\n[-] Daemon stopped by user.")
            break
        except Exception as e:
            logger.exception("daemon loop error: %s", e)
            print(f"[!] Daemon encountered unexpected error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

if __name__ == "__main__":
    main()
