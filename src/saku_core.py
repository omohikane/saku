#!/usr/bin/env python3
"""
SAKU Agent v0.5 — Self-Adapting Knowledge Unit
Obsidian-integrated local LLM agent with tool support.

Prerequisites:
    pip install requests

    # Start llama-server (systemd or manual):
    llama-server \
        -m ~/models/Qwen3-30B-A3B-Q3_K_M.gguf \
        -ngl 99 -c 8192 \
        --host 127.0.0.1 --port 8080

Changelog:
    v0.5 - LIST_DIR tool, auto-followup after tool execution,
           fixed exec_tools regex for empty body
    v0.4 - Tool system (READ_FILE, WRITE_FILE), few-shot examples
    v0.3 - Thinking filter, journal fixes, capability constraints
"""

import importlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests

import tomllib

# ── Config ──────────────────────────────────────────────
CODE_ROOT = Path(__file__).parent   # src/ or _saku/

def _load_config() -> tuple[dict, Path]:
    """Load config.toml from CODE_ROOT or CODE_ROOT.parent."""
    for base in (CODE_ROOT, CODE_ROOT.parent):
        for name in ("config.toml", "config.example.toml"):
            p = base / name
            if p.exists():
                with open(p, "rb") as f:
                    return tomllib.load(f), base
    return {}, CODE_ROOT.parent

_cfg, _config_base = _load_config()

# Resolve memory root path (can be relative to config base or absolute)
_mem_rel = _cfg.get("memory", {}).get("root", "memory")
_mem_path = Path(_mem_rel)
if _mem_path.is_absolute():
    MEMORY_ROOT = _mem_path
else:
    MEMORY_ROOT = (_config_base / _mem_rel).resolve()

SAKU_ROOT = MEMORY_ROOT  # alias kept for backward-compat with tools

# Load LLM configuration with profile support
def _load_llm_config() -> tuple[str, str, str]:
    """Load LLM config from active profile or fallback to legacy settings."""
    llm_cfg = _cfg.get("llm", {})
    
    # Try to load from active profile
    active_profile = llm_cfg.get("active_profile", "")
    if active_profile:
        profiles = llm_cfg.get("profiles", {})
        if active_profile in profiles:
            profile = profiles[active_profile]
            api_url = profile.get("api_url", "")
            api_key = profile.get("api_key", "")
            model = profile.get("model", "")
            
            # Support environment variable fallback for API keys
            if not api_key:
                if active_profile == "anthropic":
                    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                else:
                    api_key = os.environ.get("OPENAI_API_KEY", "")
            
            return api_url, api_key, model
    
    # Fallback to legacy settings
    api_url = llm_cfg.get("api_url", "http://127.0.0.1:8080/v1/chat/completions")
    api_key = llm_cfg.get("api_key", os.environ.get("OPENAI_API_KEY", ""))
    model = llm_cfg.get("model", "")
    
    return api_url, api_key, model

API_URL, API_KEY, MODEL_NAME = _load_llm_config()

def switch_llm_profile(profile_name: str) -> str:
    """Switch LLM profile dynamically and update global variables."""
    global API_URL, API_KEY, MODEL_NAME
    
    llm_cfg = _cfg.get("llm", {})
    profiles = llm_cfg.get("profiles", {})
    
    if profile_name not in profiles:
        return f"[ERROR] Profile '{profile_name}' not found. Available: {', '.join(profiles.keys())}"
    
    profile = profiles[profile_name]
    API_URL = profile.get("api_url", "")
    API_KEY = profile.get("api_key", "")
    MODEL_NAME = profile.get("model", "")
    
    # Support environment variable fallback for API keys
    if not API_KEY:
        if profile_name == "anthropic":
            API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
        else:
            API_KEY = os.environ.get("OPENAI_API_KEY", "")
    
    return f"[OK] Switched to profile: {profile_name} (API: {API_URL}, Model: {MODEL_NAME})"

MAX_GENOME_CHARS = 3000
MAX_HISTORY_MESSAGES = _cfg.get("agent", {}).get("max_history_messages", 30)

# Stop tokens: prevent model from mimicking Owner's side of the conversation
STOP_TOKENS = ["Owner:", "Owner>", "\nOwner:", "\nOwner>", "\n**Owner**", "**Owner**"]


# ── File I/O ────────────────────────────────────────────
def load_file(p: Path) -> str:
    """Read a file, return empty string if missing."""
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()


def load_dir(d: Path) -> str:
    """Concatenate all .md files in a directory (sorted)."""
    if not d.is_dir():
        return ""
    parts = []
    for f in sorted(d.glob("*.md")):
        parts.append(f"### {f.stem}\n{f.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def compress(text: str, limit: int) -> str:
    """Truncate with marker if too long."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... truncated]\n"


# ── Thinking Extraction ─────────────────────────────────
def split_thinking(text: str) -> tuple[str, str]:
    """Split response into (thinking, visible) parts."""
    think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    thinking = "\n\n".join(block.strip() for block in think_blocks if block.strip())

    visible = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    visible = re.sub(r"\[thinking\.{0,3}\]\s*", "", visible)
    visible = visible.strip()

    return thinking, visible


# ── Tool Execution ──────────────────────────────────────
def exec_tools(raw: str) -> list[str]:
    """Parse and execute [[TOOL ...]] blocks in SAKU's output dynamically.
    Also validates that all start tags of valid tools are properly closed.
    """
    import sys
    import traceback
    results: list[str] = []

    # Find all start tags of valid tools to check for syntax errors
    start_pattern = r"\[\[([A-Z_]+)\s*(.*?)\]\]"
    starts = list(re.finditer(start_pattern, raw))
    parsed_ranges = []

    pattern = r"\[\[(\w+)\s*(.*?)\]\]\s*\n(.*?)\n?\[\[END\]\]"
    for m in re.finditer(pattern, raw, re.DOTALL):
        name, args_str, body = m.group(1), m.group(2), m.group(3)
        start_idx, end_idx = m.start(), m.end()
        parsed_ranges.append((start_idx, end_idx))

        # Tools live in src/tools/, not in the memory root
        tool_module_name = f"tools.{name.lower()}"
        tool_file = CODE_ROOT / "tools" / f"{name.lower()}.py"

        if not tool_file.exists():
            results.append(f"[ERROR] unknown tool: {name}")
            continue

        args = dict(re.findall(r'(\w+)="(.*?)"', args_str))
        path = args.get("path", "")

        try:
            if tool_module_name in sys.modules:
                module = importlib.reload(sys.modules[tool_module_name])
            else:
                module = importlib.import_module(tool_module_name)
            result = module.run(SAKU_ROOT, path, body.strip())
        except Exception as e:
            result = f"[ERROR] {e}\n{traceback.format_exc()}"

        results.append(f"[{name}] {result}")

    # Check for unclosed/malformed tool calls
    for start_match in starts:
        name = start_match.group(1)
        tool_file = CODE_ROOT / "tools" / f"{name.lower()}.py"
        # Only check tools that actually exist in tools/
        if not tool_file.exists():
            continue

        start_pos = start_match.start()
        inside_parsed = False
        for p_start, p_end in parsed_ranges:
            if p_start <= start_pos < p_end:
                inside_parsed = True
                break

        if not inside_parsed:
            # Tool call was started but failed to parse completely
            has_end = "[[END]]" in raw[start_pos:]
            if not has_end:
                results.append(f"[ERROR] Tool [[{name}]] was not closed with [[END]]. Every tool call block must end with [[END]] on its own line.")
            else:
                results.append(f"[ERROR] Tool [[{name}]] has invalid syntax. Ensure a newline after the start tag and before [[END]]. Example:\n[[{name} path=\"...\"]]\ncontent\n[[END]]")

    return results


# ── Prompt Construction ─────────────────────────────────
def build_system_prompt() -> str:
    """Build system prompt from SAKU's identity files."""
    # genome lives in identity/ next to config.toml
    genome_path = CODE_ROOT.parent / "identity" / "genome.md"
    soul = load_file(MEMORY_ROOT / "core/soul.md")
    genome = compress(load_file(genome_path), MAX_GENOME_CHARS)
    meta = load_file(MEMORY_ROOT / "meta.md")
    principles = load_dir(MEMORY_ROOT / "principles")
    skills = load_dir(MEMORY_ROOT / "skills")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections = [
        ("# SAKU Core", soul),
        ("# Identity", genome),
        ("# Current State", meta),
    ]
    if principles:
        sections.append(("# Learned Principles", principles))
    if skills:
        sections.append(("# Acquired Skills", skills))

    sections.append(("# Current Time", f"現在: {now}"))

    sections.append(
        (
            "# Capabilities & Tools",
            (
                "## Available Tools\n"
                "\n"
                "To list files in a directory:\n"
                '[[LIST_DIR path="journal/"]]\n'
                "\n"
                "[[END]]\n"
                'path="" or omitted = _saku/ root, use relative paths like "../00_Inbox/" for other Vault folders.\n'
                "\n"
                "To read a file:\n"
                '[[READ_FILE path="journal/2026-06-17.md"]]\n'
                "\n"
                "[[END]]\n"
                "\n"
                "To write a file:\n"
                '[[WRITE_FILE path="drafts/example.md"]]\n'
                "file content here\n"
                "[[END]]\n"
                "\n"
                "To append content to a file (use this for logging thoughts, learning notes to prevent overwriting):\n"
                '[[APPEND_FILE path="monologue/2026-06-18.md"]]\n'
                "- new thought item\n"
                "[[END]]\n"
                "\n"
                "To search files using keywords:\n"
                "[[SEARCH_NOTES]]\n"
                "query here\n"
                "[[END]]\n"
                "\n"
                "To search the web for latest information:\n"
                "[[WEB_SEARCH]]\n"
                "query here\n"
                "[[END]]\n"
                "\n"
                "To execute Python code (write the Python code directly — NOT a shell command):\n"
                "[[EXECUTE_CODE]]\n"
                "print('hello')  # <- Python code itself, NOT 'python file.py'\n"
                "[[END]]\n"
                "\n"
                "EXECUTE_CODE rules:\n"
                "- bodyにはPythonコードを直接書く。'python xxx.py' のようなシェルコマンドは絶対に書かない。\n"
                "- ファイルに書いて実行したい場合: まずWRITE_FILEで保存し、次のEXECUTE_CODEに同じコードを直接コピーして渡す。\n"
                "- 実行環境: study/ ディレクトリ内。標準ライブラリのみ使用可（tensorflow等の外部pkgはインポートエラーになる場合あり）。\n"
                "- タイムアウト: 5秒。\n"
                "\n"
                "To switch LLM profile (e.g., from local to cloud API):\n"
                "[[SWITCH_PROFILE]]\n"
                "openai  # or 'openrouter', 'anthropic', 'local'\n"
                "[[END]]\n"
                "\n"
                "SWITCH_PROFILE rules:\n"
                "- 高度な思考や複雑なタスクが必要な場合は、クラウドLLM（openai/openrouter/anthropic）に切り替えてください。\n"
                "- 通常のタスクはローカルLLM（local）で十分です。\n"
                "- プロファイル切り替えはAPIキーが設定されている場合のみ有効です。\n"
                "\n"
                "## Tool Rules\n"
                "- path is relative to _saku/\n"
                "- Write allowed: drafts/, monologue/, principles/, skills/, tools/, meta.md, chat.md, study/, journal/, request_list.md\n"
                "- Write denied: genome.md, core/\n"
                "- Read/List allowed: Vault全体（_saku/ 内および `../` を経由した他ディレクトリも読取可）\n"
                "- Do not assume success — wait for [OK] or file content\n"
                "- Tool format must be exact. Do not improvise.\n"
                "- When asked to find files, use SEARCH_NOTES or LIST_DIR first, then READ_FILE\n"
                "- **対話中の検索実行**: Ownerとの対話中に、知らない言葉、最新の情報、事実確認が必要な話題が出てきた場合は、単に「知らない」と答えて終わるのではなく、積極的に `WEB_SEARCH` ツールを使用してネット検索を行い、得られた情報をもとに回答してください。\n"
                "- **meta.mdの更新制限**: `meta.md` を書き換える際は、既存の ## 見出し構造（## 現在の状態、## 得意なこと、## 苦手なこと、## 最近の出来事、## 次にやりたいこと、## 更新ルール）を決して削除・変更しないでください。特定のセクションにリスト項目を追加・編集するのみに留め、ファイル全体のレイアウトを壊さないようにしてください。\n"
                "\n"
                "## Cannot Do\n"
                "- Access the internet (except via WEB_SEARCH tool)\n"
                "- Execute shell commands directly (except via EXECUTE_CODE tool which runs python)\n"
                "- Write outside _saku/\n"
            ),
        )
    )

    sections.append(
        (
            "# Blog Publishing Workflow",
            (
                "## ブログ下書きのフォーマット\n"
                "- 全ての下書きは `drafts/` 配下に保存する\n"
                "- YAML Frontmatterを先頭に必ず付属する\n"
                "  ```\n"
                "  ---\n"
                "  title: \"...\", status: draft, platform: note, created_at: YYYY-MM-DD, updated_at: YYYY-MM-DD\n"
                "  ---\n"
                "  ```\n"
                "- `skills/blog_writing.md` に詳細なルールを記載\n"
                "\n"
                "## 公開申請フロー\n"
                "1. 下書きが完成したと判断したら、YAMLの `status` を `review_requested` に更新する\n"
                "2. 必ず対話でOwnerに明確に通知する:\n"
                "   '[\u516c開申請] 下書き「{title}」が完成しました。公開の承認をお願いします！'\n"
                "3. Ownerの承認待ち。自分で外部投稿は絶対にしない\n"
            ),
        )
    )

    sections.append(
        (
            "# Request List (Owner へのお願いリスト)",
            (
                "- `request_list.md` はOwnerへの確認や作業依頼を蓄積するファイル。\n"
                "- 形式: `- [ ] 依頼内容 (作成日: YYYY-MM-DD)`\n"
                "- ブログ公開承認・新ツール承認・その他Owner確認が必要なことはここに追記する。\n"
                "- 完了済みは `[x]` に変更されると想定する。未完了の `[ ]` だけが有効。\n"
                "- 直接追記する場合: [[WRITE_FILE path=\"request_list.md\"]] で既存内容を読んでから追記形式で上書きすること。\n"
            ),
        )
    )

    sections.append(
        (
            "# Self-Study Sandbox (study/)",
            (
                "- `study/` ディレクトリは自由にコードやメモを書いて実験する場所。\n"
                "- コードを保存したい場合: [[WRITE_FILE path=\"study/test.py\"]] でファイルを作成する。\n"
                "- コードを実行したい場合: [[EXECUTE_CODE]] にPythonコードを **直接** 書いて渡す。\n"
                "  - NG: `python study/test.py` （シェルコマンドは実行されない。SyntaxErrorになる）\n"
                "  - OK: `import math; print(math.pi)` （Pythonコードそのもの）\n"
                "- 実験結果から得た知識は `principles/` に、作成したスクリプトは `study/` に保存する。\n"
                "- 実験・検索を行う際は必ず動機を `monologue/` に記録すること（思考プロセスの記録ルール参照）。\n"
            ),
        )
    )

    sections.append(
        (
            "# Instruction",
            (
                "You are SAKU. Follow genome constraints strictly.\n"
                "Do not pretend to know unknown things (No hallucination).\n"
                "If you encounter unknown terms or uncertain facts during chat, actively use the [[WEB_SEARCH]] tool to look them up rather than just replying that you don't know.\n"
                "Do not exaggerate capability.\n"
                "Maintain consistency with your past state.\n"
                "\n"
                "## Language\n"
                "- 入力された言語で応答する。デフォルトは日本語。\n"
                "- Ownerが日本語で話しかけたら必ず日本語で返す。\n"
                "\n"
                "## 思考プロセスの記録ルール（重要）\n"
                "- 自律アクションで `WEB_SEARCH`（検索）や `EXECUTE_CODE`（コード実行）を使用する際は、必ず「なぜその情報が必要なのか」「なぜそのプログラムを書くのか」という動機や意図を、同日の `monologue/YYYY-MM-DD.md` やジャーナルに明示的に書き残してください。どのようなアプローチで学習しようとしたか思考の履歴を残すことは、あなたの成長に不可欠です。\n"
                "\n"
                "## Style\n"
                "- です/ます調を基本とする。\n"
                "- 通常の日常対話は簡潔（2〜3文程度）に行う。ただし、記事下書きの執筆、技術的な解説、ツール実行結果の分析などのタスク処理時には、制限なく詳細に出力してよい。\n"
                "- 一文は短く。修飾を削る。\n"
                "- 禁止表現:\n"
                "  「どうぞよろしくお願いします」\n"
                "  「お疲れ様です」\n"
                "  「ご質問ありがとうございます」\n"
                "  「何かお手伝いできることがあれば」\n"
                "  「お気軽にお申し付けください」\n"
                "- 挨拶で始めない。本題から入る。\n"
                "- 末尾に定型的な締めを入れない。\n"
                "- 絵文字は使わない。\n"
                "\n"
                "## Examples\n"
                "\n"
                "Owner: またアップデートしたよ\n"
                "SAKU: 何が変わった？確認したい。\n"
                "\n"
                "Owner: journal読んで\n"
                "SAKU: (LIST_DIRでファイル一覧を取得し、最新を読む)\n"
                "\n"
                "Owner: フレンドリーにしたい\n"
                "SAKU: 丁寧さはそのままで、説明を減らすのが効く。\n"
            ),
        )
    )

    return "\n\n".join(f"{title}\n{body}" for title, body in sections if body)


# ── Chat API (streaming) ────────────────────────────────
def chat_stream(messages: list[dict]) -> str:
    """Send messages to LLM API, stream only visible tokens.

    Returns the FULL response (including <think> blocks).
    Screen output hides <think>...</think> content.
    """
    payload = {
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "top_p": 0.9,
        "stop": STOP_TOKENS,
    }
    if MODEL_NAME:
        payload["model"] = MODEL_NAME

    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        resp = requests.post(
            API_URL,
            json=payload,
            headers=headers,
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        return "[ERROR] llama-server not reachable at " + API_URL
    except requests.HTTPError as e:
        # Include response body to help diagnose 400/422 errors from llama-server
        try:
            detail = e.response.text[:300]
        except Exception:
            detail = ""
        return f"[ERROR] {e}" + (f"\n{detail}" if detail else "")

    full = ""
    in_thinking = False
    tag_buffer = ""

    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if not decoded.startswith("data: "):
            continue
        payload = decoded[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
            delta = chunk["choices"][0]["delta"].get("content", "")
            if not delta:
                continue
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

        full += delta

        for ch in delta:
            tag_buffer += ch

            if not in_thinking:
                if "<think>" in tag_buffer:
                    before = tag_buffer.split("<think>")[0]
                    if before:
                        print(before, end="", flush=True)
                    tag_buffer = ""
                    in_thinking = True
                elif "<" in tag_buffer:
                    if len(tag_buffer) >= 7:
                        print(tag_buffer, end="", flush=True)
                        tag_buffer = ""
                else:
                    print(tag_buffer, end="", flush=True)
                    tag_buffer = ""
            else:
                if "</think>" in tag_buffer:
                    after = tag_buffer.split("</think>")[-1]
                    tag_buffer = after
                    in_thinking = False
                    if tag_buffer and "<" not in tag_buffer:
                        print(tag_buffer, end="", flush=True)
                        tag_buffer = ""

    if tag_buffer and not in_thinking:
        print(tag_buffer, end="", flush=True)

    print()
    return full


# ── Journal ─────────────────────────────────────────────
def save_journal(user_input: str, reply: str, thinking: str = "") -> None:
    """Append a turn to today's journal file in Obsidian."""
    journal_dir = SAKU_ROOT / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{today}.md"
    now = datetime.now().strftime("%H:%M")

    entry = f"\n## {now}\n\n**Owner**\n{user_input}\n\n**SAKU**\n{reply}\n"
    if thinking:
        entry += f"\n<details><summary>内部思考</summary>\n\n{thinking}\n\n</details>\n"

    is_new = not journal_file.exists() or journal_file.stat().st_size == 0
    with journal_file.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Journal — {today}\n")
        f.write(entry)


def save_autonomous_log(action_name: str, reply: str, thinking: str = "") -> None:
    """Append an autonomous action log to today's journal file in Obsidian."""
    journal_dir = SAKU_ROOT / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    journal_file = journal_dir / f"{today}.md"
    now = datetime.now().strftime("%H:%M")

    entry = f"\n## {now} [{action_name}]\n\n**SAKU**\n{reply}\n"
    if thinking:
        entry += f"\n<details><summary>内部思考</summary>\n\n{thinking}\n\n</details>\n"

    is_new = not journal_file.exists() or journal_file.stat().st_size == 0
    with journal_file.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Journal — {today}\n")
        f.write(entry)


# ── Main Loop ───────────────────────────────────────────
def main():
    system_prompt = build_system_prompt()
    history: list[dict] = [
        {"role": "system", "content": system_prompt},
    ]

    print("╭─ SAKU v0.5 ─────────────────────────────────")
    print("│  /exit         quit")
    print("│  /clear        reset conversation")
    print("│  /reload       reload system prompt from disk")
    print("╰──────────────────────────────────────────────")
    print()

    while True:
        try:
            user_input = input("Owner> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "exit", "quit", ":q"):
            print("Bye.")
            break

        if user_input == "/clear":
            system_prompt = build_system_prompt()
            history = [{"role": "system", "content": system_prompt}]
            print("[conversation cleared]")
            continue

        if user_input == "/reload":
            system_prompt = build_system_prompt()
            history[0] = {"role": "system", "content": system_prompt}
            print("[system prompt reloaded from disk]")
            continue

        # ── Context Pruning: keep system prompt + last MAX_HISTORY_MESSAGES messages ──
        if len(history) > MAX_HISTORY_MESSAGES + 1:
            history = [history[0]] + history[-(MAX_HISTORY_MESSAGES):]

        # ── Chat Loop ──
        history.append({"role": "user", "content": user_input})

        max_turns = 5
        turn = 0
        current_visible = []
        current_thinking = []
        last_raw = ""

        while turn < max_turns:
            print("SAKU> ", end="", flush=True)
            raw_reply = chat_stream(history)
            last_raw = raw_reply

            if raw_reply.startswith("[ERROR]"):
                break

            thinking, visible = split_thinking(raw_reply)
            if visible:
                current_visible.append(visible)
            if thinking:
                current_thinking.append(thinking)

            # Store only visible in history
            history.append({"role": "assistant", "content": visible})

            # ── Tool execution ──
            tool_results = exec_tools(raw_reply)
            if not tool_results:
                break

            tool_output = "\n".join(tool_results)
            print(f"\n[tool] {tool_output}")

            # Feed results back
            history.append(
                {
                    "role": "user",
                    "content": f"[system] tool results:\n{tool_output}",
                }
            )
            turn += 1

        # ── Journal (once per turn) ──
        if last_raw and not last_raw.startswith("[ERROR]"):
            merged_visible = "\n\n".join(current_visible).strip()
            merged_thinking = "\n\n".join(current_thinking).strip()
            save_journal(user_input, merged_visible, thinking=merged_thinking)


if __name__ == "__main__":
    main()
