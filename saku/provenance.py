"""Provenance for durable memory (#79): record where a memory came from.

As information starts arriving from other agents and the Web, not all of it
deserves the same weight: the Owner's own words, another agent's report, and
something picked up on the Web must not be treated as equals. That weighing
is only possible later if the origin is written down *now*, at the moment a
durable entry is created, and carried through every promotion.

Two storage shapes, one vocabulary:

- ``MEMORY.md`` entries carry the origin inside the existing HTML-comment
  metadata dreaming already writes (``s:``/``last:``), as an extra field:
  ``<!-- s:3 last:2026-08-30 src:journal/2026-08-30 -->``. Obsidian does not
  render comments, so the Owner-facing file stays clean, and entries written
  before provenance existed simply have no ``src`` (absence means "legacy /
  unknown", never an error).
- Free-form durable files (``principles/``, ``skills/``) written by the
  LLM-driven paths carry a blockquote header line, the same style wiki notes
  already use for ``> created:`` -- ``> source: inbox/report.md``.

A source reference ("ref") is ``<kind>/<detail>``: ``journal/2026-08-30``,
``monologue/2026-08-30``, ``inbox/report.md``, ``chat/2026-08-30``,
``agent/<name>``, ``web``, ``owner``, ``self``. An entry may list several
refs, comma-separated: that is a *candidate set* ("drawn from one of these
records"), not a claim that all of them contributed -- dreaming extracts
from the day's journal and monologue in one combined pass and cannot
attribute an item more precisely. Provenance is set when an entry is first
written and is never rewritten by reinforcement: a re-mention is not a new
origin. The Owner can amend a ref by hand-editing the Markdown; nothing
here is a database.

Refs are machine-generated from dates and file names, so they are sanitized
to a comment-safe alphabet: anything outside ``[A-Za-z0-9_./:+-]`` becomes
``_`` and ``--`` is collapsed (it would terminate the HTML comment).
"""

import re

# Source kinds (the part before the first "/").
KIND_JOURNAL = "journal"  # the day's journal (Owner chat + autonomous logs)
KIND_MONOLOGUE = "monologue"  # SAKU's own monologue for the day
KIND_INBOX = "inbox"  # an external document dropped into the inbox
KIND_CHAT = "chat"  # the Owner conversation (chat.md archive flow)
KIND_MAIL = "mail"  # a letter from another agent
KIND_AGENT = "agent"  # another agent's report (detail = agent name)
KIND_WEB = "web"  # picked up from the Web
KIND_OWNER = "owner"  # the Owner's own words, recorded directly
KIND_SELF = "self"  # SAKU's own inference / observation

KNOWN_KINDS = {
    KIND_JOURNAL,
    KIND_MONOLOGUE,
    KIND_INBOX,
    KIND_CHAT,
    KIND_MAIL,
    KIND_AGENT,
    KIND_WEB,
    KIND_OWNER,
    KIND_SELF,
}

_UNSAFE_REF_CHARS_RE = re.compile(r"[^A-Za-z0-9_./:+\-]")
_COMMENT_RE = re.compile(r"<!--(.*?)-->")
_SRC_FIELD_RE = re.compile(r"\bsrc:([^\s>]+)")
_SOURCE_LINE_RE = re.compile(r"^>\s*source:\s*(\S+)", re.MULTILINE)


def sanitize_ref(ref: str) -> str:
    """Make a source ref safe to embed in an HTML comment or a header line.

    Whitespace and comment-breaking sequences cannot survive: unsafe
    characters become ``_`` and ``--`` collapses to ``-``. A ref that
    sanitizes to nothing becomes ``"unknown"``.
    """
    cleaned = _UNSAFE_REF_CHARS_RE.sub("_", str(ref).strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("_") or "unknown"


def make_ref(kind: str, detail: str = "") -> str:
    """Build one source ref, e.g. ``make_ref("journal", "2026-08-30")``."""
    if detail:
        return sanitize_ref(f"{kind}/{detail}")
    return sanitize_ref(kind)


def split_refs(src: str | None) -> list[str]:
    """Split a stored ``src`` value into its refs (order preserved)."""
    if not src:
        return []
    return [r for r in (part.strip() for part in src.split(",")) if r]


def join_refs(refs: list[str]) -> str | None:
    """Join refs into a stored ``src`` value (deduped, order preserved).

    Returns None for an empty list, so callers can omit the field entirely
    and legacy-shaped output is produced instead.
    """
    seen: list[str] = []
    for ref in refs:
        clean = sanitize_ref(ref)
        if clean not in seen:
            seen.append(clean)
    return ",".join(seen) if seen else None


def union_srcs(*srcs: str | None) -> str | None:
    """Union several stored ``src`` values into one (deduped, first wins)."""
    refs: list[str] = []
    for src in srcs:
        refs.extend(split_refs(src))
    return join_refs(refs)


def extract_src(line: str) -> str | None:
    """Return the ``src`` value of a MEMORY.md entry line, or None.

    Only HTML comments are consulted, so a "src:" appearing in the entry
    text itself is never mistaken for provenance.
    """
    for m in _COMMENT_RE.finditer(line):
        field = _SRC_FIELD_RE.search(m.group(1))
        if field:
            return field.group(1)
    return None


def source_line(ref: str) -> str:
    """Render the provenance header line for a free-form durable file."""
    return f"> source: {sanitize_ref(ref)}"


def has_source_line(text: str) -> bool:
    """Whether a durable file's text records its provenance anywhere."""
    return _SOURCE_LINE_RE.search(text) is not None


def recorded_ref(text: str) -> str | None:
    """Return the ref recorded by a ``> source:`` header line, or None."""
    m = _SOURCE_LINE_RE.search(text)
    return m.group(1) if m else None
