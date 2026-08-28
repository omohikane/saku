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
"""

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
from saku import reflection
from saku.agent_loop import run_agent_loop as _run_agent_loop

# ── Config (from config.toml via core) ───────────────────────
MEMORY_ROOT = agent.MEMORY_ROOT
_dcfg = agent._cfg.get("daemon", {})

CHAT_FILE = MEMORY_ROOT / "chat.md"
STATE_FILE = MEMORY_ROOT / "state/processed_inbox.json"
CHAT_STATE_FILE = MEMORY_ROOT / "state/chat_state.json"
REQUEST_FILE = MEMORY_ROOT / "request_list.md"
LOG_DIR = MEMORY_ROOT / "state"
UI_INBOX = MEMORY_ROOT / "state" / "ui_inbox.json"
LOCK_FILE = MEMORY_ROOT / "state" / "daemon.lock"

PROACTIVE_CHANNELS = agent.saku_config.load_channels_config(agent._cfg).proactive

CHAT_POLL_SECONDS = _dcfg.get("chat_poll_seconds", 5)
INBOX_POLL_SECONDS = _dcfg.get("inbox_poll_seconds", 3600)
TICK_POLL_SECONDS = _dcfg.get("tick_interval_seconds", 1800)

ARCHIVE_AFTER_INACTIVE_SECONDS = _dcfg.get("archive_after_inactive_seconds", 1800)
ARCHIVE_AFTER_TURNS = _dcfg.get("archive_after_turns", 10)
AUTO_INITIATE_COOLDOWN_SECONDS = _dcfg.get("auto_initiate_cooldown_seconds", 28800)
INITIATE_RETRY_SECONDS = _dcfg.get("initiate_retry_seconds", 1800)  # Retry interval after failures (prevents spam)

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
    return history

def chat_ends_with_owner(content: str, last_content: str) -> tuple[bool, str]:
    """Return (True, owner_message) if chat.md has new content appended ending with '>'."""
    cleaned = content.rstrip()
    if not cleaned.endswith(">"):
        return False, ""
    
    if last_content and content.startswith(last_content):
        user_diff = content[len(last_content):].rstrip()
    else:
        reset_header_len = len(CHAT_RESET_HEADER)
        if len(content) >= reset_header_len:
            user_diff = content[reset_header_len:].rstrip()
        else:
            user_diff = content.rstrip()
            
    if user_diff.endswith(">"):
        user_diff = user_diff[:-1].strip()
        
    if not user_diff:
        return False, ""
        
    return True, user_diff

# ── Agent loop runner ────────────────────────────────────
def run_agent_loop(
    prompt: str, log_action_name: str, extra_history: list[dict] = None, light: bool = False, inbox_mode: bool = False
) -> tuple[bool, str]:
    """Run Saku on a specific prompt, execute tools, and log results. Returns (success, visible_output)."""
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

    result = _run_agent_loop(
        history,
        agent._current_llm,
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=5,
        on_tool_result=on_tool_result,
        no_action_markers=["[NO_ACTION]", "[INBOX_PROCESSED]"],
        log=lambda msg: logger.warning("[%s] %s", log_action_name, msg),
    )

    if result.action_taken and result.last_raw and not result.last_raw.startswith("[ERROR]"):
        agent.save_autonomous_log(log_action_name, result.visible, thinking=result.thinking)
        logger.info("[%s] reply: %s", log_action_name, result.visible[:500])
        return True, result.visible

    logger.info("[%s] no-action reply: %s", log_action_name, result.visible[:500])
    return False, result.visible

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
    has_new_msg, owner_msg = chat_ends_with_owner(content, last_content)

    if not has_new_msg:
        state["last_mtime"] = current_mtime
        save_chat_state(state)
        return

    print(f"[*] Trigger detected. Formatting new Owner message in chat.md...")
    now_str = datetime.now().strftime("%H:%M")
    
    if not content.startswith(last_content):
        cleaned_content = content.rstrip()
        if cleaned_content.endswith(">"):
            base_content = cleaned_content[:-1].rstrip()
        else:
            base_content = cleaned_content
    else:
        base_content = last_content.rstrip()

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

    prompt = f"""[system] chat.md上でOwnerからメッセージが届きました。
以下の指示と会話の文脈を踏まえて返信してください。
返信は必ず日本語で、最終的な回答のみを出力してください。「**Owner**」や「Owner>」は一切出力しないでください。
必要な記憶があれば [[SEARCH_NOTES]] や [[READ_FILE]] でオンデマンドに取得してください。
重い調査や時間のかかる作業を頼まれたら、必要に応じて [[SPAWN_CHILD name=\"...\"]] / [[DELEGATE child=\"...\"]] でサブエージェントに委譲してください。

Ownerのメッセージ:
{owner_msg}
"""

    _, saku_reply = run_agent_loop(prompt, "chat返信", extra_history=context_history, light=True)

    if not saku_reply:
        state["last_mtime"] = CHAT_FILE.stat().st_mtime
        state["last_content"] = updated_content
        save_chat_state(state)
        return

    # Check request list and append if any pending
    request_suffix = get_pending_requests()
    reply_block = f"\n**SAKU** ({now_str})\n{saku_reply}{request_suffix}\n"
    final_content = updated_content + reply_block

    with CHAT_FILE.open("a", encoding="utf-8") as f:
        f.write(reply_block)
    print(f"[*] SAKU replied in chat.md")

    state["last_mtime"] = CHAT_FILE.stat().st_mtime
    state["last_content"] = final_content
    state["last_owner_msg_time"] = time.time()
    state["last_saku_msg_time"] = time.time()
    state["turn_count"] = state.get("turn_count", 0) + 1
    save_chat_state(state)

    check_chat_archive_if_needed(state)

# ── Chat: SAKU Self-Initiated message ──────────────────────
def check_autonomous_initiation() -> None:
    """Self-initiate conversation if Owner is inactive for too long and SAKU has updates."""
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

    # Record the attempt time before running (retry after an interval even on failure)
    state["last_initiate_attempt"] = now
    save_chat_state(state)

    print("[*] SAKU is autonomously initiating a conversation thread...")
    saku_self_initiate("定例チェックイン")

def saku_self_initiate(reason: str) -> None:
    """Ask SAKU to autonomously write an opening message to Owner in chat.md."""
    state = load_chat_state()
    content = CHAT_FILE.read_text(encoding="utf-8")
    chat_history = parse_chat_history(content)

    if len(chat_history) > 20:
        chat_history = chat_history[-20:]

    now = datetime.now()
    now_str = now.strftime("%H:%M")
    hour = now.hour
    if 5 <= hour < 11:
        greeting_hint = "おはようございます"
    elif 11 <= hour < 18:
        greeting_hint = "こんにちは"
    else:
        greeting_hint = "こんばんは"
    
    prompt = f"""[system] あなたからOwnerへ自発的に話しかけるタイミングです。
現在時刻は {now_str} です。時刻に合った挨拶（{greeting_hint}）を使ってください。
用件: {reason}
これまでの会話履歴、日記、meta.mdの「次にやりたいこと」などを踏まえて、話しかけのメッセージを作成してください。
定型の「定例チェックインです」は使わず、今日の気づきやOwnerへの具体的な一言を含めてください。
※応答は必ず日本語で、最終回答のみ出力してください。「**SAKU**」や「**Owner**」は一切出力しないでください。
"""

    _, saku_msg = run_agent_loop(prompt, f"自発的発話: {reason}", extra_history=chat_history)
    if not saku_msg:
        return

    request_suffix = get_pending_requests()
    reply_block = f"\n**SAKU** ({now_str})\n{saku_msg}{request_suffix}\n"
    
    # Safely append to file
    with CHAT_FILE.open("a", encoding="utf-8") as f:
        f.write(reply_block)
        
    print(f"[*] SAKU initiated chat.md message: {reason}")

    # Also deliver to the Web UI (when "webui" is included in [channels] proactive)
    if "webui" in PROACTIVE_CHANNELS:
        notify_webui(saku_msg)
    
    # Save the updated content state so daemon doesn't loop
    state["last_mtime"] = CHAT_FILE.stat().st_mtime
    state["last_content"] = CHAT_FILE.read_text(encoding="utf-8")
    state["last_saku_msg_time"] = time.time()
    save_chat_state(state)


def notify_webui(message: str) -> None:
    """Deliver autonomous messages/alerts to the Web UI (proactive notification)."""
    UI_INBOX.parent.mkdir(parents=True, exist_ok=True)
    UI_INBOX.write_text(
        json.dumps({"message": message}, ensure_ascii=False),
        encoding="utf-8",
    )

# ── Midnight Reflection (2:00 AM) ──────────────────────────
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

    print(f"[*] Midnight reflection triggered at {now.strftime('%H:%M')}...")

    # reflection digests YESTERDAY's logs
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        # Run reflect logic
        reflection.run_reflection(yesterday)

        # Record run date
        state["last_reflection_date"] = today_str
        save_chat_state(state)

        # Post a report message to chat.md autonomously
        report_msg = f"昨日の活動の振り返りと、本日（{today_str}）の自己モデル（meta.md）の整理を完了しました。今日もよろしくお願いいたします。"
        saku_self_initiate(f"深夜振り返り報告 ({report_msg})")

    except Exception as e:
        print(f"[!] Midnight reflection failed: {e}")

# ── Dreaming: promote durable memories into MEMORY.md ─────────
def check_and_run_dreaming() -> None:
    """Run dreaming once per day (digests YESTERDAY's journal/monologue)."""
    state = load_chat_state()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if state.get("last_dream_date", "") == today_str:
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
            success, _ = run_agent_loop(task, f"scheduled:{it.get('id','')}")
            it["status"] = "done" if success else "pending"
            it["executed"] = now.strftime("%Y-%m-%d %H:%M")
            updated = True
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

    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M")

    meta_append_only = "meta.md" in agent.saku_config.get_path_policy().write_denied_exact
    meta_update_rule = (
        "※重要: meta.mdは WRITE_FILE での上書きが禁止されています。必ず [[APPEND_FILE path=\"meta.md\" heading=\"最近の出来事\"]] を使い、\n"
        "   既存の「## 最近の出来事」セクションの末尾に「- {today}: （概要）」の形式で1行だけ追記してください。\n"
        "   見出し構造（## で始まる行）は絶対に変更・削除しないでください。"
        if meta_append_only
        else "※meta.md は WRITE_FILE で編集可能です。既存の ## 見出し構造を尊重しつつ「## 最近の出来事」に概要を追記してください。"
    )

    prompt = f"""[system] chat.mdの会話アーカイブ処理を行います。
以下の会話履歴（{len(chat_history)}件のメッセージ）を分析し、以下のタスクを実行してください。

1. 新しい教訓や重要な気づきがあれば [[WRITE_FILE path="principles/{today}-chat-archive.md"]] に記録する。
2. 自己モデル（meta.md）の「最近の出来事」セクションに今日のchat概要を1行追記する。
   {meta_update_rule}
3. すべて完了したら「[ARCHIVE_DONE]」と出力してください。

（注: journal/ への書き込みは制限されています。principles/ や meta.md を使用してください）
"""
    run_agent_loop(prompt, f"チャットアーカイブ ({now_str})", extra_history=chat_history)
    reset_chat_file()

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

def check_inbox_and_process() -> None:
    # The inbox location is configurable via [memory] inbox_dir (the layout varies
    # per person). Falls back gracefully if not present.
    inbox_dir = agent.saku_config.resolve_inbox_dir(agent._cfg, MEMORY_ROOT, agent._config_base)

    if not inbox_dir.is_dir():
        return

    state = load_processed_state()
    new_state = dict(state)

    for p in inbox_dir.glob("*.md"):
        rel_inbox_path = str(p.relative_to(inbox_dir))
        mtime = p.stat().st_mtime

        if rel_inbox_path not in state or mtime > state[rel_inbox_path]:
            print(f"[*] Found new/updated inbox file: {rel_inbox_path}")
            file_content = agent.load_file(p)
            prompt = f"""[system] インボックスに新規/更新ファイルが配置されました。
ファイルパス: {rel_inbox_path}
内容:
---
{file_content[:3000]}
---

この内容を分析し、あなたの知識ベース（principles/ や skills/）に追加すべき情報があれば書き込んでください。
処理完了後は「[INBOX_PROCESSED]」と出力してください。
"""
            success, _ = run_agent_loop(prompt, f"インボックス処理: {p.name}", inbox_mode=True)
            if success:
                new_state[rel_inbox_path] = mtime
                save_processed_state(new_state)

# ── Autonomous tick (Self-Study / Monologue) ──────────────
def check_autonomous_tick() -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[*] Starting periodic autonomous tick at {now_str}...")

    meta_writable = "meta.md" not in agent.saku_config.get_path_policy().write_denied_exact
    meta_update_rule = (
        f'[[WRITE_FILE path="meta.md"]] で編集してください。'
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
    run_agent_loop(prompt, "定期自律アクション")

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

    state = load_chat_state()
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
    check_scheduled_tasks()
    last_schedule_check = now_ts

    while True:
        try:
            time.sleep(CHAT_POLL_SECONDS)
            now = time.time()

            # 1. Always check chat for new Owner messages
            check_chat_and_reply()

            # 2. Check inactivity-based archive
            check_chat_inactivity_archive()

            # 3. Check for midnight reflection (2:00 AM)
            check_and_run_midnight_reflection()

            # 3b. Dreaming: promote durable memories into MEMORY.md (once per day)
            check_and_run_dreaming()

            # 3c. Scheduled tasks (chat-driven, every 60s)
            if now - last_schedule_check >= 60:
                check_scheduled_tasks()
                last_schedule_check = now

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
