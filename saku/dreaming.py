"""Dreaming: promote durable memories from journal/monologue into MEMORY.md.

Runs periodically (e.g., daily). Reads short-term records (journal/monologue),
has the LLM extract important, reusable memories, and appends them to MEMORY.md
under the appropriate section (append-only, no duplication).

This is the "memory promotion" layer: raw experience (journal/monologue) -> durable
memory (MEMORY.md). Unlike the nightly reflection (which updates principles/ and
meta.md), dreaming distills facts and patterns that should persist long-term.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import core as agent
from saku.agent_loop import run_agent_loop

# category -> MEMORY.md ## section heading
MEMORY_SECTIONS = {
    "永続的な事実": "永続的な事実（Owner・環境）",
    "好み・傾向": "好み・傾向",
    "重要な学び": "重要な学び",
    "進行中プロジェクト": "進行中のプロジェクト",
    "方針・決定": "方針・決定",
}

MEMORY_HEADER = """# MEMORY — 長期記憶

dreaming が journal/monologue から重要な記憶を昇格させた場所。
追記のみ。既存内容は編集しない。

## 永続的な事実（Owner・環境）

## 好み・傾向

## 重要な学び

## 進行中のプロジェクト

## 方針・決定

"""

_MEMORY_ITEM_RE = re.compile(
    r"\[MEMORY\]\s*category:\s*(.+?)\s*content:\s*(.+?)\s*\[/MEMORY\]",
    re.DOTALL,
)


def extract_items(target_date: str, journal: str, monologue: str) -> list[tuple[str, str]]:
    """Ask the LLM to extract durable memory items as [[MEMORY]] blocks."""
    system_prompt = agent.build_system_prompt()
    user_prompt = f"""[system] 夢見（dreaming）の時間です。あなたの短期的な記録から、
長期的に残す価値のある記憶を抽出してください。

【対象日 ({target_date}) の日記】
{journal if journal else "(空)"}

【対象日 ({target_date}) の独り言】
{monologue if monologue else "(空)"}

抽出するもの:
1. Ownerや環境に関する永続的な事実（好み、環境、重要な出来事）
2. 繰り返し出てくる関心事・テーマ・方針
3. 重要な学び・決定（principles/ に未反映のもの）
4. 進行中のプロジェクトやタスクの現状

除外するもの:
- 一時的な思考・感情の揺れ・その場限りの感想
- 既に principles/ や meta.md に記録済みの内容

出力形式（ツールは使わない。この形式のみ出力。※必ず単括弧を使うこと）:
[MEMORY]
category: 永続的な事実|好み・傾向|重要な学び|進行中プロジェクト|方針・決定
content: 一行で簡潔に
[/MEMORY]

複数あるなら複数出力。無ければ [NO_MEMORY] とだけ出力。
"""

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    result = run_agent_loop(
        history,
        agent._current_llm,
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=1,
    )

    items = []
    for m in _MEMORY_ITEM_RE.finditer(result.last_raw):
        category = m.group(1).strip()
        content = m.group(2).strip()
        if category in MEMORY_SECTIONS and content:
            items.append((category, content))
    return items


def _insert_after_heading(content: str, heading: str, entry: str) -> str:
    """Insert `entry` as a list item right after the `## heading` section."""
    anchor = f"## {heading}"
    pos = content.find(anchor)
    if pos == -1:
        # append a new section at the end
        return content.rstrip() + f"\n\n{anchor}\n{entry}\n"
    # find the end of the heading line, then the first blank line after it
    line_end = content.find("\n", pos)
    after = line_end + 1 if line_end != -1 else len(content)
    return content[:after] + f"{entry}\n" + content[after:]


def append_items(memory_path: Path, items: list[tuple[str, str]], date: str) -> list[str]:
    """Append items to MEMORY.md under their section. Returns newly added contents."""
    if not memory_path.exists():
        memory_path.write_text(MEMORY_HEADER, encoding="utf-8")
    content = memory_path.read_text(encoding="utf-8")

    added = []
    for category, text in items:
        section = MEMORY_SECTIONS[category]
        if text.strip() in content:
            continue
        entry = f"- {date}: {text.strip()}"
        content = _insert_after_heading(content, section, entry)
        added.append(text.strip())

    if added:
        memory_path.write_text(content, encoding="utf-8")
    return added


def run_dreaming(target_date: str | None = None) -> list[str]:
    """Run one dreaming cycle for the given date (default: today). Returns added items."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    journal_path = agent.SAKU_ROOT / "journal" / f"{target_date}.md"
    monologue_path = agent.SAKU_ROOT / "monologue" / f"{target_date}.md"
    journal = agent.load_file(journal_path)
    monologue = agent.load_file(monologue_path)

    if not journal and not monologue:
        print(f"[*] Dreaming: no journal/monologue for {target_date}. Skipped.")
        return []

    print(f"[*] Dreaming for {target_date}...")
    items = extract_items(target_date, journal, monologue)
    if not items:
        print("[*] Dreaming: no durable memory found.")
        return []

    memory_path = agent.SAKU_ROOT / "MEMORY.md"
    added = append_items(memory_path, items, target_date)
    print(f"[*] Dreaming: promoted {len(added)} item(s) to MEMORY.md.")
    return added


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    arg_date = args[0] if args else None
    run_dreaming(arg_date)
    return 0


if __name__ == "__main__":
    main()
