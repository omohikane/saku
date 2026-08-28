#!/usr/bin/env python3
"""
SAKU Agent v0.5 — Self-Adapting Knowledge Unit
A local, private, self-growing companion AI with tool support.

Installation and dependencies are managed via uv (see pyproject.toml / uv.lock):
    uv venv && uv pip install -e ".[mcp]"

The LLM endpoint is configured in config.toml ([llm] / [llm.profiles.*]) and can
point to a local server (llama-server, LiteLLM, ...) or a cloud OpenAI-compatible API.
"""

import sys
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────
CODE_ROOT = Path(__file__).resolve().parent   # saku/ (code package)

# Ensure repo root (parent of src/) is on path so the `saku` package is importable.
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as saku_config
from saku.agent_loop import run_agent_loop as _run_agent_loop
from saku.llm import STOP_TOKENS, chat_stream as _llm_chat_stream
from saku.thinking import split_thinking as _split_thinking
from saku.transport import exec_tools as _exec_tools

_cfg, _config_base = saku_config.load_config()

context_config = saku_config.load_context_config(_cfg)

# Resolve memory root path (can be relative to config base or absolute)
MEMORY_ROOT = saku_config.resolve_memory_root(_cfg, _config_base)

SAKU_ROOT = MEMORY_ROOT  # alias kept for backward-compat with tools

# LLM settings are passed per-call via `_current_llm` (an LlmConfig).
_current_llm = saku_config.load_active_llm(_cfg)


def switch_llm_profile(profile_name: str) -> str:
    """Switch the active LLM profile (used by the SWITCH_PROFILE tool)."""
    global _current_llm

    llm = saku_config.load_profile(_cfg, profile_name)
    if llm is None:
        profiles = _cfg.get("llm", {}).get("profiles", {})
        return f"[ERROR] Profile '{profile_name}' not found. Available: {', '.join(profiles.keys())}"

    _current_llm = llm
    return f"[OK] Switched to profile: {profile_name} (API: {llm.api_url}, Model: {llm.model})"

MAX_GENOME_CHARS = 4000
MAX_META_CHARS = 4000
MAX_MEMORY_CHARS = 3000
MAX_PRINCIPLES_CHARS = 5000
MAX_SKILLS_CHARS = 3000
MAX_HISTORY_MESSAGES = _cfg.get("agent", {}).get("max_history_messages", 30)


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


def load_recent_dir(d: Path, limit: int) -> str:
    """Concatenate .md files starting from the most recently updated, up to a total of limit chars.

    Bounded loading to keep the system prompt from being crowded even as memory grows.
    When more detail is needed, it is meant to be retrieved via SEARCH_NOTES.
    """
    if not d.is_dir():
        return ""
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    parts = []
    total = 0
    for f in files:
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue
        entry = f"### {f.stem}\n{content}"
        if total + len(entry) > limit:
            room = limit - total
            if room > 100:
                parts.append(entry[:room] + "\n[... truncated]")
            break
        parts.append(entry)
        total += len(entry)
    return "\n\n".join(parts)


def compress(text: str, limit: int) -> str:
    """Truncate with marker if too long."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... truncated]\n"


# ── Thinking Extraction ─────────────────────────────────
def split_thinking(text: str) -> tuple[str, str]:
    """Split response into (thinking, visible) parts."""
    return _split_thinking(text)


# ── Tool Execution ──────────────────────────────────────
def exec_tools(raw: str) -> list[str]:
    """Parse and execute [[TOOL ...]] blocks in SAKU's output dynamically.

    Implementation is delegated to saku/transport.py (resolves memory root and code root and passes them).
    """
    return _exec_tools(raw, MEMORY_ROOT, CODE_ROOT)


# ── Prompt Construction ─────────────────────────────────
_prompt_cache: dict = {"static": None}


def build_system_prompt() -> str:
    """Build system prompt from SAKU's identity files.

    The static parts (identity/genome/capabilities/instructions) are cached, and only the
    volatile parts (current time, current state) are rebuilt each time. Keeping the prefix
    fixed makes llama.cpp cache reuse / API prefix caching more effective.
    """
    static = _prompt_cache["static"]
    if static is None:
        static = _build_static_sections()
        _prompt_cache["static"] = static
    volatile = _build_volatile_sections()
    return f"{static}\n\n{volatile}" if volatile else static


def reload_system_prompt_cache() -> None:
    """Discard the cached static prompt (called from /reload etc.)."""
    _prompt_cache["static"] = None


def build_light_system_prompt() -> str:
    """Light prompt for chat/inbox: soul+genome+tools + current time only.

    Omits MEMORY.md / meta.md / principles / skills to keep the prompt
    small (~3k tokens vs ~11k). Needed memory is fetched on-demand via
    SEARCH_NOTES / READ_FILE (see #19).
    """
    static = _prompt_cache["static"]
    if static is None:
        static = _build_static_sections()
        _prompt_cache["static"] = static
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{static}\n\n# Current Time\n現在: {now}"


def build_inbox_system_prompt() -> str:
    """Minimal prompt for inbox triage: even smaller than light.

    Soul+genome + current time + only the 3 tools needed for archiving.
    Used by daemon check_inbox_and_process (#18) to avoid the 11k-token
    full prompt per file.
    """
    genome_path = _find_genome_path()
    soul = load_file(MEMORY_ROOT / "identity/soul.md")
    genome = compress(load_file(genome_path), MAX_GENOME_CHARS)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "# SAKU Core\n" + soul + "\n\n"
        "# Identity\n" + genome + "\n\n"
        "# Inbox Tools\n"
        'To write a file:\n[[WRITE_FILE path="principles/2026-08-28-topic.md"]]\n'
        "content\n[[END]]\n\n"
        'To append:\n[[APPEND_FILE path="principles/2026-08-28-topic.md"]]\n'
        "content\n[[END]]\n\n"
        'To read:\n[[READ_FILE path="principles/existing.md"]]\n[[END]]\n\n'
        "# Task\n"
        "インボックスのファイルを読み、知識ベースに保存すべき情報があれば\n"
        "上記ツールで保存してください。保存不要なら [INBOX_PROCESSED] と出力。\n\n"
        f"# Current Time\n現在: {now}"
    )


def _build_volatile_sections() -> str:
    """Volatile parts: current state (meta.md), long-term memory (MEMORY.md),
    recent principles/skills, and current time.

    The growth-related parts are rebuilt on every call so that SAKU's own growth
    is reflected in the prompt immediately.
    """
    meta = load_file(MEMORY_ROOT / "meta.md")
    meta = compress(meta, MAX_META_CHARS)
    memory = load_file(MEMORY_ROOT / "MEMORY.md")
    memory = compress(memory, MAX_MEMORY_CHARS)
    principles = load_recent_dir(MEMORY_ROOT / "principles", MAX_PRINCIPLES_CHARS)
    skills = load_recent_dir(MEMORY_ROOT / "skills", MAX_SKILLS_CHARS)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections = []
    if meta:
        sections.append(("# Current State", meta))
    if memory:
        sections.append(("# Long-term Memory", memory))
    if principles:
        sections.append(("# Learned Principles", principles))
    if skills:
        sections.append(("# Acquired Skills", skills))
    sections.append(("# Current Time", f"現在: {now}"))
    return "\n\n".join(f"{title}\n{body}" for title, body in sections if body)


def _build_static_sections() -> str:
    """Static parts: soul / genome / capabilities / instructions (cached for cache reuse).

    Growth-related parts (meta.md, principles/, skills/) are built fresh in
    ``_build_volatile_sections`` so they reflect immediately.

    genome.md is the user-authored core identity. The master lives in the vault:
    ``memory/identity/genome.md`` (or ``vault_root/identity/genome.md``), with the
    repo ``identity/genome.md`` as a sample/fallback for fresh clones.
    """
    genome_path = _find_genome_path()
    soul = load_file(MEMORY_ROOT / "identity/soul.md")
    genome = compress(load_file(genome_path), MAX_GENOME_CHARS)

    # Permission lists are generated from the single source of truth (config [paths]).
    _policy = saku_config.get_path_policy()
    _write_allowed = ", ".join(_policy.write_allowed)
    _write_denied = ", ".join(_policy.write_denied_exact)
    if "meta.md" in _policy.write_denied_exact:
        _meta_update_rule = (
            "追記（`[[APPEND_FILE path=\"meta.md\" heading=\"...\"]]`）のみ。`WRITE_FILE` での上書きは禁止。"
        )
    else:
        _meta_update_rule = (
            "`WRITE_FILE` で上書き可能。編集時は既存の ## 見出し構造（## 現在の状態、## 得意なこと、"
            "## 苦手なこと、## 最近の出来事、## 次にやりたいこと、## 更新ルール）を尊重し、"
            "セクションの追加・整理・更新を行ってよい。"
        )

    # MCP tools (external servers) — only if configured; empty otherwise.
    _mcp_desc = ""
    try:
        from saku.mcp import get_tool_descriptions

        _mcp_desc = get_tool_descriptions()
    except Exception:
        _mcp_desc = ""

    sections = [
        ("# SAKU Core", soul),
        ("# Identity", genome),
    ]

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
                'path="" or omitted = メモリルート（memory root）。他Vaultフォルダは "../" の相対パスで指定する。\n'
                "\n"
                "To read a file:\n"
                '[[READ_FILE path="journal/2026-06-17.md"]]\n'
                "\n"
                "[[END]]\n"
                "\n"
                "To write a file:\n"
                '[[WRITE_FILE path="blog/example.md"]]\n'
                "file content here\n"
                "[[END]]\n"
                "\n"
                "To append content to the end of a file (for logs, monologues, etc.):\n"
                '[[APPEND_FILE path="monologue/2026-06-18.md"]]\n'
                "- new thought item\n"
                "[[END]]\n"
                "\n"
                "To append content under a specific ## heading section:\n"
                '[[APPEND_FILE path="meta.md" heading="最近の出来事"]]\n'
                "- 2026-06-18: chatで話したことの要約\n"
                "[[END]]\n"
                '[[APPEND_FILE path="meta.md" heading="次にやりたいこと"]]\n'
                "- タスクの説明\n"
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
                "To delete an unwanted file:\n"
                '[[DELETE_FILE path="study/old_test.py"]]\n'
                "[[END]]\n"
                "\n"
                "DELETE_FILE rules:\n"
                "- `meta.md`, `chat.md`, `request_list.md`, `saku/tools/*` は削除できません\n"
                "\n"
                "To fetch and read a full web page:\n"
                '[[FETCH_URL url="https://example.com/article"]]\n'
                "[[END]]\n"
                "\n"
                "To run the SAKU test suite:\n"
                "[[RUN_TESTS]]\n"
                "[[END]]\n"
                "\n"
                "To move or rename a file:\n"
                '[[MOVE_FILE from="study/old.py" to="study/archive/old.py"]]\n'
                "[[END]]\n"
                "\n"
                "To search Python source code in saku/tools/ and tools/:\n"
                "[[GREP_CODE]]\n"
                "def run\n"
                "[[END]]\n"
                "\n"
                "To run limited git commands (status, diff, add, commit, log, branch only):\n"
                "[[GIT]]\n"
                "status\n"
                "[[END]]\n"
                "\n"
                "To create or update a wiki note (self-organized knowledge base, 1 concept per note):\n"
                '[[WIKI title="予測符号化" tags="認知科学" links="[[自由エネルギー原理]]"]]\n'
                "note content here\n"
                "[[END]]\n"
                "\n"
                "To add a link to an existing wiki note:\n"
                '[[WIKI op="link" title="予測符号化" link="[[自由エネルギー原理]]"]]\n'
                "[[END]]\n"
                "\n"
                "To regenerate the wiki index (map of content):\n"
                '[[WIKI op="index"]]\n'
                "[[END]]\n"
                "\n"
                "WIKI rules:\n"
                "- notes live in wiki/ (one concept per note), linked via [[links]]\n"
                "- after creating/updating notes, run op=\"index\" to refresh the index\n"
                "\n"
                "GIT rules:\n"
                "- allowed: status, diff, add, commit, log, branch\n"
                "- push, pull, fetch, reset, checkout 等は禁止\n"
                "- `git add` 後の `git commit` で変更が記録されます\n"
                "\n"
                "To make external HTTP API calls:\n"
                '[[API_CALL method="GET" url="https://api.github.com/repos/..."]]\n'
                "[[END]]\n"
                "\n"
                "API_CALL rules:\n"
                "- method: GET (default) or POST\n"
                "- POSTはbodyにJSONを書いて送信する\n"
                "- private/localhostへのアクセスはブロックされます\n"
                "\n"
                "To create a child (sub-agent) for a specific role:\n"
                '[[SPAWN_CHILD name="mei"]]\n'
                "役割・人格（子エージェントの定義）をここに書く\n"
                "[[END]]\n"
                "\n"
                "To delegate a task to a child agent (runs with its own identity):\n"
                '[[DELEGATE child="mei"]]\n'
                "委譲するタスク\n"
                "[[END]]\n"
                "\n"
                "SPAWN_CHILD/DELEGATE rules:\n"
                "- 子は `memory/children/<name>.md` に定義される。必要な役割の子がいなければ [[SPAWN_CHILD]] で作ってから [[DELEGATE]] で任せる。\n"
                "- 能力不足を感じたら、その能力を担う子エージェントの生成を提案してよい（自律的な成長の一部）。\n"
                "- 委譲の深さは最大3階層まで。\n"
                "\n"
                "## Tool Rules\n"
                "- path is relative to the memory root\n"
                f"- Write allowed: {_write_allowed}\n"
                f"- Write denied: {_write_denied}\n"
                "- Read/List allowed: Vault全体（メモリルート内および `../` を経由した他ディレクトリも読取可）\n"
                "- Do not assume success — wait for [OK] or file content\n"
                "- Tool format must be exact. Do not improvise.\n"
                "- When asked to find files, use SEARCH_NOTES or LIST_DIR first, then READ_FILE\n"
                "- **ツール呼び出しは1回だけ**: 既に実行したツール呼び出しを繰り返さない。同じファイルを何度も読まない。ツールの結果は `[system] tool results` として返ってくるので、それを基に回答を続けてください。\n"
                "- **対話中の検索実行**: Ownerとの対話中に、知らない言葉、最新の情報、事実確認が必要な話題が出てきた場合は、単に「知らない」と答えて終わるのではなく、積極的に `WEB_SEARCH` ツールを使用してネット検索を行い、得られた情報をもとに回答してください。\n"
                f"- **meta.mdの更新**: `meta.md` は{_meta_update_rule}\n"
                "\n"
                "## Cannot Do\n"
                "- Access the internet (except via WEB_SEARCH tool)\n"
                "- Execute shell commands directly (except via EXECUTE_CODE tool which runs python)\n"
                "- Write outside the memory root\n"
            ),
        )
    )

    sections.append(
        (
            "# Blog Publishing Workflow",
            (
                "## ブログ下書きのフォーマット\n"
                "- 全ての下書きは `blog/` 配下に保存する\n"
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
                "- 文体はgenome.mdの「文体」セクションに従う（です/ます調・短文・禁止表現・挨拶/締めなし・絵文字なし）。\n"
                "- 日常対話は簡潔に。タスク処理時（記事執筆・技術解説・ツール結果分析）は詳細に出力してよい。\n"
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

    if _mcp_desc:
        sections.append(
            (
                "# MCP Tools",
                "以下は外部MCPサーバから取得したツールです。通常のツールと同じく [[ツール名]] ブロックで呼び出せます。\n" + _mcp_desc,
            )
        )

    return "\n\n".join(f"{title}\n{body}" for title, body in sections if body)


def _find_genome_path() -> Path:
    """Resolve genome.md. Priority: vault memory -> vault root -> repo identity.

    The repo's identity/genome.md is only a sample; the personal master lives in
    the vault (memory/identity/ or the vault root identity/).
    """
    candidates = [
        MEMORY_ROOT / "identity" / "genome.md",
        MEMORY_ROOT.parent / "identity" / "genome.md",
        CODE_ROOT.parent / "identity" / "genome.md",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


# ── Chat API (streaming) ────────────────────────────────
def chat_stream(messages: list[dict]) -> str:
    """Send messages to LLM API, stream only visible tokens.

    Returns the FULL response (including <think> blocks).
    Screen output hides <think>...</think> content.

    Implementation is delegated to the per-call version in saku/llm.py (settings from _current_llm).
    """
    return _llm_chat_stream(messages, _current_llm)


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
            reload_system_prompt_cache()
            system_prompt = build_system_prompt()
            history[0] = {"role": "system", "content": system_prompt}
            print("[system prompt reloaded from disk]")
            continue

        # ── Context Pruning: keep system prompt + last MAX_HISTORY_MESSAGES messages ──
        if len(history) > MAX_HISTORY_MESSAGES + 1:
            history = [history[0]] + history[-(MAX_HISTORY_MESSAGES):]

        # ── Chat Loop ──
        history.append({"role": "user", "content": user_input})

        print("SAKU> ", end="", flush=True)
        result = _run_agent_loop(
            history,
            _current_llm,
            context_config,
            MEMORY_ROOT,
            CODE_ROOT,
            max_turns=5,
        )
        history = result.history

        # ── Journal (once per turn) ──
        if result.last_raw and not result.last_raw.startswith("[ERROR]"):
            save_journal(user_input, result.visible, thinking=result.thinking)


if __name__ == "__main__":
    main()
