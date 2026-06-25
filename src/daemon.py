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
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))

import saku_core as agent
import reflect

# ── Config (from config.toml via saku_core) ───────────────────────
MEMORY_ROOT = agent.MEMORY_ROOT
_dcfg = agent._cfg.get("daemon", {})

CHAT_FILE = MEMORY_ROOT / "chat.md"
STATE_FILE = MEMORY_ROOT / "state/processed_inbox.json"
CHAT_STATE_FILE = MEMORY_ROOT / "state/chat_state.json"
REQUEST_FILE = MEMORY_ROOT / "request_list.md"
LOG_FILE = MEMORY_ROOT / "state/saku.log"

CHAT_POLL_SECONDS = _dcfg.get("chat_poll_seconds", 5)
INBOX_POLL_SECONDS = _dcfg.get("inbox_poll_seconds", 3600)
TICK_POLL_SECONDS = _dcfg.get("tick_interval_seconds", 1800)

ARCHIVE_AFTER_INACTIVE_SECONDS = _dcfg.get("archive_after_inactive_seconds", 1800)
ARCHIVE_AFTER_TURNS = _dcfg.get("archive_after_turns", 10)
AUTO_INITIATE_COOLDOWN_SECONDS = _dcfg.get("auto_initiate_cooldown_seconds", 28800)

# ── Debug Logger ──────────────────────────────────────
def log_debug(level: str, context: str, message: str) -> None:
    """Append a structured log entry to saku.log for easy post-mortem inspection."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(message) > 500:
        message = message[:500] + " [...truncated]"
    line = f"[{now}] [{level}] [{context}] {message}\n"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[!] log_debug write error: {e}")

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
def run_agent_loop(prompt: str, log_action_name: str, extra_history: list[dict] = None) -> tuple[bool, str]:
    """Run Saku on a specific prompt, execute tools, and log results. Returns (success, visible_output)."""
    system_prompt = agent.build_system_prompt()
    history = [{"role": "system", "content": system_prompt}]
    if extra_history:
        history.extend(extra_history)
    history.append({"role": "user", "content": prompt})

    log_debug("INFO", log_action_name, "agent loop started")

    max_turns = 5
    turn = 0
    current_visible = []
    current_thinking = []
    last_raw = ""
    action_taken = False

    while turn < max_turns:
        # --- Context Protection / Pruning ---
        # Safe character limit (~12000 chars is roughly 4000-6000 tokens, well below 8192 context limit)
        char_limit = 12000
        total_chars = sum(len(m.get("content", "")) for m in history)
        
        if total_chars > char_limit and len(history) > 5:
            print(f"[*] Context size is large ({total_chars} chars). Pruning old history...")
            log_debug("WARN", log_action_name, f"context pruned: {total_chars} chars -> keeping system + last 4 msgs")
            # Keep system prompt (index 0) and the last 4 messages (which contain current tools and logic)
            # and discard the middle (older chat logs)
            pruned_history = [history[0]] + history[-4:]
            history = pruned_history
            new_total = sum(len(m.get("content", "")) for m in history)
            print(f"[*] Pruned context size down to {new_total} chars.")

        raw_reply = agent.chat_stream(history)
        last_raw = raw_reply

        if raw_reply.startswith("[ERROR]"):
            print(f"[!] LLM Error: {raw_reply}")
            log_debug("ERROR", log_action_name, f"LLM error: {raw_reply[:300]}")
            break

        thinking, visible = agent.split_thinking(raw_reply)

        if "[NO_ACTION]" not in raw_reply and "[INBOX_PROCESSED]" not in raw_reply:
            action_taken = True

        if visible:
            current_visible.append(visible)
        if thinking:
            current_thinking.append(thinking)

        history.append({"role": "assistant", "content": visible})

        tool_results = agent.exec_tools(raw_reply)
        if tool_results:
            action_taken = True
            
            # Truncate overly long tool outputs to prevent context pollution
            processed_results = []
            for tr in tool_results:
                log_debug("TOOL", log_action_name, tr)
                if len(tr) > 2000:
                    tr = tr[:2000] + "\n\n[... tool output truncated to save context ...]"
                processed_results.append(tr)
                
            tool_output = "\n".join(processed_results)
            print(f"\n[tool] {tool_output}")
            history.append({"role": "user", "content": f"[system] tool results:\n{tool_output}"})
        else:
            break
        turn += 1

    merged_visible = "\n\n".join(current_visible).strip()
    merged_thinking = "\n\n".join(current_thinking).strip()

    if action_taken and last_raw and not last_raw.startswith("[ERROR]"):
        agent.save_autonomous_log(log_action_name, merged_visible, thinking=merged_thinking)
        return True, merged_visible

    return False, merged_visible

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

Ownerのメッセージ:
{owner_msg}
"""

    _, saku_reply = run_agent_loop(prompt, "chat返信", extra_history=context_history)

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
    
    # Check cooldown
    last_saku = state.get("last_saku_msg_time", 0)
    last_owner = state.get("last_owner_msg_time", 0)

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

    print("[*] SAKU is autonomously initiating a conversation thread...")
    saku_self_initiate("定例チェックイン")

def saku_self_initiate(reason: str) -> None:
    """Ask SAKU to autonomously write an opening message to Owner in chat.md."""
    state = load_chat_state()
    content = CHAT_FILE.read_text(encoding="utf-8")
    chat_history = parse_chat_history(content)

    if len(chat_history) > 20:
        chat_history = chat_history[-20:]

    now_str = datetime.now().strftime("%H:%M")
    
    prompt = f"""[system] あなたからOwnerへ自発的に話しかけるタイミングです。
用件: {reason}
これまでの会話履歴、日記、meta.mdの「次にやりたいこと」などを踏まえて、話しかけのメッセージを作成してください。
（挨拶、進捗報告、今日やりたいことの宣言、またはOwnerへの軽い質問などを含めると良いです）
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
    
    # Save the updated content state so daemon doesn't loop
    state["last_mtime"] = CHAT_FILE.stat().st_mtime
    state["last_content"] = CHAT_FILE.read_text(encoding="utf-8")
    state["last_saku_msg_time"] = time.time()
    save_chat_state(state)

# ── Midnight Reflection (2:00 AM) ──────────────────────────
def check_and_run_midnight_reflection() -> None:
    """Run reflect.py automatically at 2:00 AM."""
    now = datetime.now()
    state = load_chat_state()
    
    # Target: 02:00 to 02:05
    if now.hour == 2 and 0 <= now.minute <= 5:
        today_str = now.strftime("%Y-%m-%d")
        
        # Check if already run for today
        if state.get("last_reflection_date", "") == today_str:
            return
            
        print(f"[*] Midnight reflection triggered at {now.strftime('%H:%M')}...")
        
        # reflection digests YESTERDAY's logs
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            # Run reflect logic
            reflect.run_reflection(yesterday)
            
            # Record run date
            state["last_reflection_date"] = today_str
            save_chat_state(state)
            
            # Post a report message to chat.md autonomously
            report_msg = f"昨日の活動の振り返りと、本日（{today_str}）の自己モデル（meta.md）の整理を完了しました。今日もよろしくお願いいたします。"
            saku_self_initiate(f"深夜振り返り報告 ({report_msg})")
            
        except Exception as e:
            print(f"[!] Midnight reflection failed: {e}")

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

    prompt = f"""[system] chat.mdの会話アーカイブ処理を行います。
以下の会話履歴（{len(chat_history)}件のメッセージ）を分析し、以下のタスクを実行してください。

1. 新しい教訓や重要な気づきがあれば [[WRITE_FILE path="principles/{today}-chat-archive.md"]] に記録する。
2. 自己モデル（meta.md）の「最近の出来事」セクションに今日のchat概要を1行追記する。
   ※重要: meta.mdは WRITE_FILE での上書きが禁止されています。必ず [[APPEND_FILE path="meta.md" heading="最近の出来事"]] を使い、
   既存の「## 最近の出来事」セクションの末尾に「- {today}: （概要）」の形式で1行だけ追記してください。
   見出し構造（## で始まる行）は絶対に変更・削除しないでください。
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
    # Inbox is expected one level above the memory root (Obsidian vault usage)
    # Falls back gracefully if not present.
    vault_root = MEMORY_ROOT.parent
    inbox_dir = vault_root / "00_Inbox"

    if not inbox_dir.is_dir():
        return

    state = load_processed_state()
    new_state = dict(state)

    for p in inbox_dir.glob("*.md"):
        rel_inbox_path = str(p.relative_to(vault_root))
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
            success, _ = run_agent_loop(prompt, f"インボックス処理: {p.name}")
            if success:
                new_state[rel_inbox_path] = mtime
                save_processed_state(new_state)

# ── Autonomous tick (Self-Study / Monologue) ──────────────
def check_autonomous_tick() -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[*] Starting periodic autonomous tick at {now_str}...")

    prompt = f"""[system] 定期自律アクションの時間です。現在時刻は {now_str} です。
以下の学習・検証タスクを積極的に実行してください。

1. 独り言の執筆:
   今考えていること、AIとしての自己存在、あるいは最近学んだことについての気づきを [[APPEND_FILE path="monologue/{today}.md"]] に書き足してください（追記モード）。
   ※重要: WEB_SEARCHやEXECUTE_CODEを使用する予定があれば、「なぜそれを行うのか」という動機を必ず独り言に明示的に書き残してください。

2. 自律研究とコード実行 sandbox（study/）での学習:
   - 自分が興味を持っている技術やAIのトレンド、学んでみたいIT技術、またはOwnerのVaultで見つかった気になる用語について、[[WEB_SEARCH]] でネット検索を行ってください。
   - 調査した技術の実証や検証のために、[[WRITE_FILE path="study/テスト名.py"]] を作成し、[[EXECUTE_CODE]] ツールを実行して動作テストを試してください。
   - 学習した内容やコード実証の結果得られた有益な知識・教訓は、[[APPEND_FILE path="principles/{today}-learning.md"]] に追加（追記）してください。

3. 自己モデルの調整:
   meta.md を [[READ_FILE path="meta.md"]] で読み込み、「次にやりたいこと」に更新が必要であれば
   [[APPEND_FILE path="meta.md" heading="次にやりたいこと"]] で追記してください（WRITE_FILEでの上書きは禁止です）。

特に行うべき自律タスクがない場合は、最終出力として「[NO_ACTION]」とだけ出力してください。
"""
    run_agent_loop(prompt, "定期自律アクション")

# ── Main ─────────────────────────────────────────────────
def main():
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

    last_inbox_check = 0
    last_tick = 0

    # Startup checks
    check_inbox_and_process()
    last_inbox_check = time.time()
    check_autonomous_tick()
    last_tick = time.time()

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

            # 4. Check for autonomous chat initiation
            check_autonomous_initiation()

            # 5. Periodically run inbox scan and autonomous tick
            if now - last_inbox_check >= interval:
                check_inbox_and_process()
                last_inbox_check = now

            if now - last_tick >= interval:
                check_autonomous_tick()
                last_tick = now

        except KeyboardInterrupt:
            print("\n[-] Daemon stopped by user.")
            break
        except Exception as e:
            print(f"[!] Daemon encountered unexpected error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)

if __name__ == "__main__":
    main()
