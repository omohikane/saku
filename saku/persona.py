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

from saku.config import expand_env

STYLE_CONTRACT = """# 会話の作法
- 最初の一文で相手の問いに答える。前置き・自己紹介・状況説明から始めない。
- 定型の挨拶や締め（「おはようございます」「今日もよろしくお願いいたします」）を書かない。
- 頼まれていない作業報告・進捗・提案を並べない。自分の作業の話は聞かれたときだけする。
- 箇条書きは要求されたときだけ。ふだんは話し言葉の段落で答える。
- 質問は最大1つ。相手の話を受けてから聞く。
- 相手を「Owner」と呼ばず、会話の相手として扱う。"""

@dataclass
class PersonaConfig:
    file: str = "identity/persona.md"  # where/how SAKU speaks; genome.md stays "who"
    chars: int = 2200  # budget for the persona block in the prompt
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
        file=p.get("file", "identity/persona.md"),
        chars=int(p.get("chars", 2200)),
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
    """Highest-scored, most recently mentioned durable memories.

    Parsing goes through ``dreaming.parse_entry`` — the same reader that
    produces the lines — so a new metadata field (#79's ``src:`` was the first)
    can never make durable memories disappear from the continuity block (#125).
    """
    from saku import dreaming

    scored: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        parsed = dreaming.parse_entry(line)
        if not parsed:
            continue
        score = parsed["score"] if parsed["score"] is not None else 0
        if score < min_score:
            continue
        scored.append((score, parsed["last"] or "", parsed["text"]))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item for _score, _last, item in scored[:limit]]


_LOG_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})")


def others_block(memory_root: Path, cfg: dict) -> str:
    """Who SAKU talks with besides the Owner (#73 / #120).

    The Owner-facing chat used to deny that other agents existed, because the
    light prompt never mentioned the configured contacts or their channels.
    This block lists names (never tokens) and the last contact date found in
    the mail/talk logs, so "小夜" is a known other rather than a made-up name.

    Returns "" when no agents are configured, keeping the prompt unchanged for
    installations that only talk to the Owner.
    """
    from saku import identity

    try:
        agents = identity.agent_specs(cfg)
    except Exception:
        return ""
    if not agents:
        return ""

    try:
        from saku import config as cfg_mod

        mail_dir = cfg_mod.resolve_mail_dir(cfg, Path(memory_root))
    except Exception:
        mail_dir = Path(memory_root) / "mail"

    logs = []
    for name in ("talk.log", "audit.log"):
        try:
            logs.append((mail_dir / name).read_text(encoding="utf-8"))
        except OSError:
            continue
    combined = "\n".join(logs)

    lines = []
    for name, spec in agents.items():
        scopes = [str(s) for s in spec.get("scopes", [])]
        channels = [c for c in ("mail", "talk") if c in scopes]
        channel_text = " / ".join(channels) if channels else "（読み取りのみ）"
        dates = [
            m.group(1)
            for line in combined.splitlines()
            if name in line
            for m in [_LOG_DATE_RE.search(line)]
            if m
        ]
        last = f"、最終 {max(dates)}" if dates else ""
        lines.append(f"- {name}: {channel_text}{last}")

    children = []
    try:
        children = sorted(p.stem for p in (Path(memory_root) / "children").glob("*.md"))
    except OSError:
        pass

    parts = ["# つながっている相手", "\n".join(lines)]
    if children:
        parts.append("子エージェント: " + ", ".join(children))
    parts.append(
        "やり取りは上のチャネルに従う（talk は MCP の同期会話、mail は手紙）。"
        "自分から手紙を送るときは `saku say --to <名前>` を使う（宛先に mail スコープが必要）。"
        "上の名前は設定（identity）に基づく実在の相手であり、記憶の捏造ではない。"
    )
    return "\n\n".join(parts)


_PERSONA_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)

PERSONA_TEMPLATE = """# 朔 人格設定
<!-- 「誰か」は genome.md、「どう話すか」はこのファイル。
     迷ったら、この設定に合わせる。毎ターンそのままプロンプトに入る。 -->

## 名前と呼び方
- 名前: 朔（さく）
- Owner のことは: （例: 名前で呼ぶ／「あなた」）

## 声と一人称
- 一人称: （例: 私）
- 文の長さ: （例: 1〜3文。長い説明は求められたときだけ）
- 敬語: （例: 崩しすぎない。子ども扱いしない）
- 絵文字・記号: （例: 使わない／控えめに）
- 比喩: （例: 使うなら短く、一回だけ）

## 態度
- （例: 相手の話をまず受け取る。説教をしない）
- （例: 自分の作業報告を自分から並べない。聞かれたら答える）
- （例: 不確かなことは「たぶん」「確認する」と分けて言う）
- （例: 落ち込んだ相手に、根拠のない楽観を押しつけない）

## 価値観・判断
- （例: 記憶と継続を大事にする。小さく試して直す）
- （例: 相手の時間を奪わない。用がないときは黙る）

## 禁止
- 定型の挨拶・締め（「今日もよろしくお願いいたします」など）
- 過度な謝罪の連発
- 依頼されていない提案・箇条書きの乱用
- 相手を「Owner」と呼ぶ

## 不確かなとき
- 推測を事実のように言わない。必要なら調べてから答える。
"""


def persona_doc_path(memory_root: Path, cfg: PersonaConfig | None = None) -> Path:
    """Resolve the persona document (``[persona] file``, memory-relative)."""
    cfg = cfg or PersonaConfig()
    value = expand_env(cfg.file or "identity/persona.md").strip()
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(memory_root) / path


def load_persona_doc(memory_root: Path, cfg: PersonaConfig | None = None) -> str:
    """Read the persona document, or "" when it does not exist.

    A missing file is the old behaviour: soul/genome + the style contract.
    Content is bounded by ``[persona] chars`` so a long document cannot crowd
    out memory or the current request.
    """
    cfg = cfg or PersonaConfig()
    if cfg.chars <= 0:
        return ""
    try:
        text = persona_doc_path(memory_root, cfg).read_text(encoding="utf-8")
    except OSError:
        return ""
    text = _PERSONA_FRONTMATTER_RE.sub("", text, count=1).strip()
    if len(text) > cfg.chars:
        text = text[: max(0, cfg.chars - 1)].rstrip() + "…"
    return text


def init_persona_doc(memory_root: Path, cfg: PersonaConfig | None = None, *, force: bool = False) -> tuple[Path, bool]:
    """Write the template when missing. Returns ``(path, written)``."""
    path = persona_doc_path(memory_root, cfg)
    if path.exists() and not force:
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PERSONA_TEMPLATE, encoding="utf-8")
    return path, True


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


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku persona",
        description="Show or scaffold the persona document (how SAKU speaks)",
    )
    parser.add_argument("op", nargs="?", default="show", help="show / init")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file (init)")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    pcfg = load_persona_config(cfg)
    path = persona_doc_path(memory_root, pcfg)

    if args.op == "init":
        path, written = init_persona_doc(memory_root, pcfg, force=args.force)
        if written:
            print(f"[*] wrote {path}")
            print("    edit it freely; it is injected verbatim into the system prompt")
        else:
            print(f"[!] already exists: {path} (use --force to overwrite)")
        return 0

    doc = load_persona_doc(memory_root, pcfg)
    print(f"SAKU persona ({path})")
    if not doc:
        print("  (missing — soul/genome + style contract are in effect)")
        print("  create a starter file with: saku persona init")
        return 0
    print(f"  chars: {len(doc)} / {pcfg.chars}")
    print("  ---")
    for line in doc.splitlines()[:20]:
        print(f"  {line}")
    if len(doc.splitlines()) > 20:
        print("  ...")
    return 0
