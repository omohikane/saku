"""Lightweight memory prefetch for the light prompt (refs #32).

The light prompt omits ``MEMORY.md`` / ``principles/`` / ``wiki/`` and expects the
model to recall on demand via ``SEARCH_NOTES``. In practice the model often answers
from general knowledge instead, which reads as "the bot does not remember me".

This module runs a cheap keyword recall *before* the turn and returns a small block
of the most relevant existing memory, so continuity survives without paying for the
full prompt. It is a prefetch, not a replacement for the tools: the block tells the
model it may dig further with ``SEARCH_NOTES``/``READ_FILE``.
"""

import re
from pathlib import Path

from saku import provenance

# Japanese has no word spacing, so terms are built from ASCII words plus
# Japanese n-grams (bigrams of each character run) instead of whole runs.
_ASCII_RE = re.compile(r"[A-Za-z0-9]{2,}")
_JP_RUN_RE = re.compile(r"[一-龠々ぁ-んァ-ヶー]{2,}")
_SCRIPT_SEG_RE = re.compile(r"[一-龠々]+|[ァ-ヶー]+|[ぁ-ん]+")
_KANA_ONLY_RE = re.compile(r"^[ぁ-んー]+$")

# Very common words that match almost everything and would drown the signal.
_SKIP_TERMS = {
    "こと", "もの", "です", "ます", "して", "する", "した", "してる", "から", "けど",
    "ので", "って", "という", "これ", "それ", "あれ", "どこ", "どう", "なに", "教えて",
    "これは", "それは", "について", "ですか",
    "the", "and", "for", "with", "you", "that", "this", "what", "how",
}

# Kana-only terms used to be dropped wholesale to keep particles out, which
# left everyday hiragana questions with no search terms at all (#134). Keep
# kana content words and filter function words instead; bigrams of particles
# ("れは", "はそ") are mostly harmless because they score low, but the frequent
# ones are listed so a casual sentence cannot flood the query.
_KANA_SKIP = _SKIP_TERMS | {
    "いつ", "その", "あの", "この", "どの", "ここ", "そこ", "また", "まだ", "もう",
    "よう", "ため", "とき", "ところ", "ばあい", "ない", "なく", "たい", "れて",
    "です", "ます", "でしょう", "ますか", "ですか", "でしょうか", "ください",
    "の", "は", "が", "に", "を", "で", "と", "も", "や", "か", "ね", "よ", "な", "わ", "へ",
}

DEFAULT_SOURCES = ["MEMORY.md", "principles/", "skills/", "wiki/"]
DEFAULT_ITEMS = 3
DEFAULT_MAX_CHARS = 600
MAX_TERMS = 40

_METADATA_RE = re.compile(r"\s*<!--.*?-->")
_ENTRY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:\s*")
_HEADING_RE = re.compile(r"^#+\s*")


def terms(text: str, *, max_terms: int = MAX_TERMS) -> list[str]:
    """Extract candidate search terms from a message (order preserved).

    ASCII words are taken whole; Japanese runs are split at script boundaries
    (kanji / katakana / hiragana) and then into bigrams.
    """
    out: list[str] = []

    def _add(term: str) -> None:
        if term and term not in _SKIP_TERMS and term not in out:
            out.append(term)

    for m in _ASCII_RE.findall(text):
        _add(m.lower())

    for run in _JP_RUN_RE.findall(text):
        for segment in _SCRIPT_SEG_RE.findall(run):
            kana_only = bool(_KANA_ONLY_RE.match(segment))
            # A long hiragana run is a whole sentence without spaces; keep it
            # only when it is short enough to be a word. Bigrams cover the rest.
            if 2 <= len(segment) and (
                (not kana_only and len(segment) <= 40)
                or (kana_only and len(segment) <= 8 and segment not in _KANA_SKIP)
            ):
                _add(segment)
            for i in range(len(segment) - 1):
                bigram = segment[i : i + 2]
                if _KANA_ONLY_RE.match(bigram) and bigram in _KANA_SKIP:
                    continue
                _add(bigram)

    return out[:max_terms]


def _clean_line(line: str) -> str:
    text = _METADATA_RE.sub("", line)
    text = _HEADING_RE.sub("", text)
    text = text.lstrip("-* \t").strip()
    text = _ENTRY_DATE_RE.sub("", text)
    return text


def iter_memory_items(
    memory_root: Path,
    sources: list[str],
    *,
    archive_section: str = "Archive",
):
    """Yield ``{label, text, status}`` for the meaningful lines of the sources.

    ``sources`` entries are paths relative to the memory root: a file or a directory
    (scanned recursively for markdown).

    ``status`` is ``archived`` for lines under ``## <archive_section>`` in
    ``MEMORY.md`` and ``active`` otherwise (#128). Decay moves stale entries
    there; without this, an expired preference came back as current knowledge.
    Other files have no archive semantics, so their sections stay ``active``.
    """
    for rel in sources:
        p = memory_root / rel
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = sorted(p.rglob("*.md"))
        else:
            continue
        for f in files:
            try:
                if any(part.startswith(".") for part in f.relative_to(memory_root).parts):
                    continue
            except ValueError:
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            label = f.relative_to(memory_root).as_posix()
            has_archive = f.name == "MEMORY.md"
            status = "active"
            for line in content.splitlines():
                if has_archive and line.startswith("## "):
                    status = (
                        "archived"
                        if line[3:].strip() == archive_section
                        else "active"
                    )
                    continue
                text = _clean_line(line)
                if len(text) < 4:
                    continue
                # Provenance (#79) is stripped with the other metadata by
                # _clean_line; surface it explicitly so a recalled memory can
                # be weighed by its origin. Display only -- scoring is
                # unchanged. Legacy entries have no src and surface as before.
                src = provenance.extract_src(line)
                if src:
                    text = f"{text}（出典: {src}）"
                yield {"label": label, "text": text, "status": status}


def iter_memory_lines(memory_root: Path, sources: list[str]):
    """Yield ``(label, text)``; compatibility wrapper over iter_memory_items."""
    for item in iter_memory_items(memory_root, sources):
        yield item["label"], item["text"]


_PAST_QUERY_RE = re.compile(r"過去|以前|昔|かつて|前は|前に|old|archiv", re.IGNORECASE)


def recall(
    memory_root: Path,
    message: str,
    *,
    items: int = DEFAULT_ITEMS,
    max_chars: int = DEFAULT_MAX_CHARS,
    sources: list[str] | None = None,
    archive_section: str = "Archive",
) -> str:
    """Return a compact "related memory" block for ``message``, or "" when nothing matches.

    Scoring is deliberately simple: each distinct query term that appears in a line
    contributes its length, so longer (more specific) matches rank higher.
    """
    query = terms(message)
    if not query:
        return ""

    srcs = list(sources) if sources else list(DEFAULT_SOURCES)
    # #128: archived entries answer "what was it before?", not "what is it now".
    wants_past = bool(_PAST_QUERY_RE.search(message))
    scored: list[tuple[int, str, str]] = []
    for item in iter_memory_items(memory_root, srcs, archive_section=archive_section):
        label, text = item["label"], item["text"]
        low = text.lower()
        hits = [t for t in query if t in low]
        if not hits:
            continue
        score = sum(len(h) for h in hits) + len(hits)
        if item["status"] == "archived":
            if not wants_past:
                continue
            text = f"{text}（過去）"
            score -= 1  # active knowledge still ranks first
        scored.append((score, label, text))

    if not scored:
        return ""

    scored.sort(key=lambda x: (-x[0], x[1], x[2]))

    lines: list[str] = []
    header = "# 関連する記憶（自動想起）\n"
    footer = "\n\n必要なら `SEARCH_NOTES` で更に掘り、想起した内容に基づいて答えてください。\n"
    # #126: the budget covers the header and every entry, including the first
    # one. It used to be checked only after a line had been added, so a single
    # long memory entered the prompt unbounded (a 600-char budget produced
    # 13k characters).
    budget = max(0, max_chars - len(header) - len(footer))
    seen: set[str] = set()
    for _score, label, text in scored:
        if text in seen:
            continue
        entry = f"- ({label}) {text}"
        body = "\n".join(lines)
        room = budget - len(body) - (1 if lines else 0)
        if room <= 0:
            break
        if len(entry) > room:
            if room < 30:
                break
            entry = entry[: room - 1] + "…"
        seen.add(text)
        lines.append(entry)
        if len(lines) >= items:
            break

    if not lines:
        return ""

    return header + "\n".join(lines) + footer
