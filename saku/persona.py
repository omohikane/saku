"""Persona continuity and conversational style (#71).

The light prompt carried identity (soul + genome) but no memory: `MEMORY.md`,
`meta.md` and `principles/` were omitted and only three recalled lines were
injected. Tool syntax was longer than the personality text, so the model answered
like a generic assistant and repeated formulaic greetings.

This module supplies the two missing pieces:

* **Continuity** — a small, budgeted block of what happened recently and what the
  agent is currently trying to do, rebuilt every turn and prepended to the system
  prompt (not injected as a mid-conversation ``system`` message, which some chat
  templates reject — see #69).
* **Style contract** — explicit conversational rules (answer in the first
  sentence, no stock greetings/closings, no unsolicited work reports).

Both are configurable via ``[persona]`` so the behaviour is not hardcoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STYLE_CONTRACT = """# 会話の作法
- 最初の一文で相手の問いに答える。前置き・自己紹介・状況説明から始めない。
- 定型の挨拶や締め（「おはようございます」「今日もよろしくお願いいたします」）を書かない。
- 頼まれていない作業報告・進捗・提案を並べない。自分の作業の話は聞かれたときだけする。
- 箇条書きは要求されたときだけ。ふだんは話し言葉の段落で答える。
- 質問は最大1つ。相手の話を受けてから聞く。
- 相手を「Owner」と呼ばず、会話の相手として扱う。"""

_MEMORY_ENTRY_RE = re.compile(
    r"^-\s*(?P<text>.*?)\s*<!--\s*s:(?P<score>\d+)(?:\s+last:(?P<last>[\d-]+))?\s*-->\s*$"
)


@dataclass
class PersonaConfig:
    continuity_enabled: bool = True
    continuity_chars: int = 1800  # total budget for the continuity block
    style_contract: bool = True
    reflection_chat_report: bool = False  # write nightly reports into chat.md (legacy behaviour)
    memory_min_score: int = 3  # only durable memories reach the continuity block
    recent_items: int = 6  # meta.md「最近の出来事」items
    next_items: int = 4  # meta.md「次にやりたいこと」items
    memory_items: int = 8  # MEMORY.md items


def load_persona_config(cfg: dict) -> PersonaConfig:
    p = cfg.get("persona", {})
    return PersonaConfig(
        continuity_enabled=p.get("continuity_enabled", True),
        continuity_chars=p.get("continuity_chars", 1800),
        style_contract=p.get("style_contract", True),
        reflection_chat_report=p.get("reflection_chat_report", False),
        memory_min_score=p.get("memory_min_score", 3),
        recent_items=p.get("recent_items", 6),
        next_items=p.get("next_items", 4),
        memory_items=p.get("memory_items", 8),
    )


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _section_items(text: str, heading: str, limit: int) -> list[str]:
    """Return the last ``limit`` bullet items under ``## heading`` (empty if absent)."""
    if limit <= 0:
        return []
    items: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
            continue
        if inside and line.strip().startswith("- "):
            item = line.strip()[2:].strip()
            if item:
                items.append(item)
    return items[-limit:]


def _memory_items(text: str, min_score: int, limit: int) -> list[str]:
    """Highest-scored, most recently mentioned durable memories."""
    scored: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        m = _MEMORY_ENTRY_RE.match(line.strip())
        if not m:
            continue
        score = int(m.group("score"))
        if score < min_score:
            continue
        scored.append((score, m.group("last") or "", m.group("text").strip()))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item for _score, _last, item in scored[:limit]]


def continuity_block(memory_root: Path, cfg: PersonaConfig | None = None) -> str:
    """Build the "what has been happening" block within the configured budget.

    Missing files are not an error: a fresh memory root simply yields no block.
    """
    cfg = cfg or PersonaConfig()
    if not cfg.continuity_enabled or cfg.continuity_chars <= 0:
        return ""

    try:
        meta = (Path(memory_root) / "meta.md").read_text(encoding="utf-8")
    except OSError:
        meta = ""
    try:
        memory = (Path(memory_root) / "MEMORY.md").read_text(encoding="utf-8")
    except OSError:
        memory = ""

    sections: list[str] = []

    recent = _section_items(meta, "最近の出来事", cfg.recent_items)
    if recent:
        sections.append("## 最近の出来事\n" + "\n".join(f"- {_clip(i, 220)}" for i in recent))

    upcoming = _section_items(meta, "次にやりたいこと", cfg.next_items)
    if upcoming:
        sections.append("## 次にやりたいこと\n" + "\n".join(f"- {_clip(i, 160)}" for i in upcoming))

    durable = _memory_items(memory, cfg.memory_min_score, cfg.memory_items)
    if durable:
        sections.append("## 長期記憶（重要）\n" + "\n".join(f"- {_clip(i, 200)}" for i in durable))

    if not sections:
        return ""

    header = "# 継続性（毎ターン更新）\n以下は自分が最近経験したことと、今の関心ごと。"
    body = header + "\n\n" + "\n\n".join(sections)

    budget = cfg.continuity_chars
    if len(body) > budget:
        # Trim the least important section first (long-term memory is the most
        # expensive and the easiest to compress; recent events matter most).
        body = header + "\n\n" + "\n\n".join(sections[:2]) if len(sections) > 2 else body
    if len(body) > budget:
        body = body[: max(0, budget - 1)].rstrip() + "…"
    return body
