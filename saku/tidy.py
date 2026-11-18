"""Tidy: deterministic cleanup of MEMORY.md and the wiki (issue #70).

Over time the long-term memory accumulates mechanical garbage: metadata
comments that leaked into entry text, exact-duplicate entries, tool-call
syntax saved into wiki notes, duplicated lines, and concept notes whose
titles collide after normalization (表記ゆれ). ``saku tidy`` removes that
garbage with deterministic rules only -- no LLM judgment, and nothing is
ever deleted: duplicates are merged, retired files are moved to the wiki's
``.archive/`` directory, and an ``--apply`` run first backs up every file
it is about to change under ``state/backup/<timestamp>/``.

The default is a dry run: the command reports what it *would* do and
writes nothing. ``--apply`` commits the changes. ``--retire-dumps`` opts
into also archiving date-dump wiki notes (e.g. ``2026-11-18-learnings``),
which are daily artifacts rather than concept notes.

Out of scope for v1: merging memories that are semantically similar but
worded differently (that needs LLM judgment).
"""

import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku.thinking import TOOL_BLOCK_RE
from saku import provenance
from saku import transport

# Every metadata-comment shape dreaming has ever written into MEMORY.md:
# the canonical "<!-- s:3 last:2026-08-30 -->", a score-only leak
# "<!-- s:1 -->", and a last-only fragment "<!-- last:2026-08-30 -->".
# Since #79 the canonical shape may carry provenance ("src:..."), alone or
# after s:/last:; tidy recognises it so consolidation never drops an origin.
_META_COMMENT_RE = re.compile(
    r"<!--\s*(?:s:(?P<score>\d+)(?:\s+last:(?P<last1>\d{4}-\d{2}-\d{2}))?"
    r"(?:\s+src:(?P<src1>[^\s>]+))?"
    r"|last:(?P<last2>\d{4}-\d{2}-\d{2})(?:\s+src:(?P<src2>[^\s>]+))?"
    r"|src:(?P<src3>[^\s>]+))\s*-->"
)

# A wiki note whose stem starts with a date is a daily dump, not a concept.
_DATE_DUMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}($|-)")

# Double-bracket tag carrying arguments, e.g. [[READ_FILE path="meta.md"]].
_TAG_WITH_ARGS_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)\s+([^\]]*)\]\]")

# Thematic-break lines are structure, not duplicated content.
_SEPARATOR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

_CREATED_RE = re.compile(r"^> created:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _join_lines(lines: list[str], original: str) -> str:
    """Join lines back into text, preserving the original's trailing newline."""
    out = "\n".join(lines)
    if original.endswith("\n") and out and not out.endswith("\n"):
        out += "\n"
    return out


# ── MEMORY.md ────────────────────────────────────────────


def _clean_entry(line: str, parsed: dict) -> tuple[str, int | None, str | None, str | None, str]:
    """Strip leaked metadata comments from one parsed entry.

    Returns (text, score, last, src, rendered_line). Score/last are consolidated
    across *all* metadata comments found in the raw line: score is the max,
    last is the newest. Provenance refs (#79) are unioned in first-seen order --
    tidy consolidates origins, it never drops or invents them. Values already
    parsed from a well-formed trailing comment participate too. A value that
    appears nowhere stays None -- tidy never fabricates metadata.
    """
    scores: list[int] = []
    lasts: list[str] = []
    srcs: list[str | None] = []
    for m in _META_COMMENT_RE.finditer(line):
        if m.group("score"):
            scores.append(int(m.group("score")))
        last = m.group("last1") or m.group("last2")
        if last:
            lasts.append(last)
        srcs.append(m.group("src1") or m.group("src2") or m.group("src3"))
    if parsed["score"] is not None:
        scores.append(parsed["score"])
    if parsed["last"]:
        lasts.append(parsed["last"])
    srcs.append(parsed["src"])
    src = provenance.union_srcs(*srcs)

    text = _META_COMMENT_RE.sub("", parsed["text"])
    text = re.sub(r"\s+", " ", text).strip()
    score = max(scores) if scores else None
    last = max(lasts) if lasts else None
    if score is not None:
        from saku.dreaming import format_entry

        rendered = format_entry(parsed["date"], text, score, last or parsed["date"], src=src)
    elif src is not None:
        rendered = f"- {parsed['date']}: {text} <!-- src:{src} -->"
    else:
        rendered = f"- {parsed['date']}: {text}"
    return text, score, last, src, rendered


def tidy_memory_text(content: str) -> tuple[str, dict]:
    """Clean MEMORY.md text. Returns (new_content, stats).

    stats keys: ``cleaned`` (entries whose leaked metadata was consolidated),
    ``merged`` (duplicate entries dropped), ``blank_runs`` (runs of 3+ blank
    lines compressed to one).
    """
    from saku.dreaming import format_entry, parse_entry

    stats = {"cleaned": 0, "merged": 0, "blank_runs": 0}
    items: list = []  # str lines and entry-record dicts, in file order
    section = ""
    for line in content.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            items.append(line)
            continue
        parsed = parse_entry(line)
        if parsed is None:
            items.append(line)
            continue
        if _META_COMMENT_RE.search(line):
            text, score, last, src, rendered = _clean_entry(line, parsed)
            if rendered != line:
                stats["cleaned"] += 1
            items.append(
                {
                    "section": section,
                    "date": parsed["date"],
                    "text": text,
                    "score": score,
                    "last": last,
                    "src": src,
                    "rendered": rendered,
                    "merged": False,
                }
            )
        else:
            items.append(
                {
                    "section": section,
                    "date": parsed["date"],
                    "text": parsed["text"],
                    "score": parsed["score"],
                    "last": parsed["last"],
                    "src": parsed["src"],
                    "rendered": line,
                    "merged": False,
                }
            )

    # Merge exact duplicates within the same section: oldest date wins,
    # score is the max of the scores present, last-seen is the newest.
    # Missing metadata is treated as absent, never as zero or epoch.
    seen: dict[tuple[str, str], dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item["section"], item["text"])
        keeper = seen.get(key)
        if keeper is None:
            seen[key] = item
            continue
        keeper["date"] = min(keeper["date"], item["date"])
        present = [s for s in (keeper["score"], item["score"]) if s is not None]
        keeper["score"] = max(present) if present else None
        lasts = [d for d in (keeper["last"], item["last"]) if d]
        keeper["last"] = max(lasts) if lasts else None
        # A merged entry keeps every origin either copy recorded (#79).
        keeper["src"] = provenance.union_srcs(keeper["src"], item["src"])
        keeper["merged"] = True
        item["dropped"] = True
        stats["merged"] += 1

    lines: list[str] = []
    for item in items:
        if isinstance(item, str):
            lines.append(item)
        elif item.get("dropped"):
            continue
        elif item["merged"]:
            if item["score"] is not None:
                lines.append(
                    format_entry(
                        item["date"],
                        item["text"],
                        item["score"],
                        item["last"] or item["date"],
                        src=item["src"],
                    )
                )
            elif item["src"] is not None:
                lines.append(f"- {item['date']}: {item['text']} <!-- src:{item['src']} -->")
            else:
                lines.append(f"- {item['date']}: {item['text']}")
        else:
            lines.append(item["rendered"])

    # Compress runs of 3+ blank lines to a single blank line.
    # Shorter runs are the file's normal spacing and stay untouched.
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i] != "":
            out_lines.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j] == "":
            j += 1
        if j - i >= 3:
            stats["blank_runs"] += 1
            out_lines.append("")
        else:
            out_lines.extend([""] * (j - i))
        i = j

    return _join_lines(out_lines, content), stats


# ── wiki notes ───────────────────────────────────────────


def sanitize_note_text(content: str) -> str:
    """Remove tool-call syntax residue from a wiki note.

    Complete [[TOOL]]...[[END]] blocks, argument-carrying tags such as
    [[READ_FILE path="x"]], stray [[END]] markers, and single-bracket tool
    calls are removed. Argless [[Title]] wiki links are preserved: a tag
    only counts as tool syntax when it carries a key=value argument, the
    same rule transport uses for single-bracket calls.
    """
    text = TOOL_BLOCK_RE.sub("", content)
    text = _TAG_WITH_ARGS_RE.sub(
        lambda m: "" if transport.TOOL_ARG_RE.search(m.group(2)) else m.group(0),
        text,
    )
    text = text.replace("[[END]]", "")
    text = transport.strip_single_bracket_calls(text)
    return text


def dedupe_note_lines(content: str) -> tuple[str, int]:
    """Drop later occurrences of identical non-empty lines within a note.

    Comparison is on the rstripped line. Blank lines and thematic breaks
    (---, ***, ___) are structure and are never deduped.
    """
    seen: set[str] = set()
    out: list[str] = []
    removed = 0
    for line in content.splitlines():
        key = line.rstrip()
        if key and not _SEPARATOR_RE.match(line):
            if key in seen:
                removed += 1
                continue
            seen.add(key)
        out.append(line)
    return _join_lines(out, content), removed


def normalize_title(title: str) -> str:
    """Normalize a note title for collision detection (matching only).

    NFKC + casefold, underscores/hyphens/dashes become spaces, remaining
    punctuation is dropped (except '+' and '#', so C++ and C# stay distinct
    from C), and whitespace is removed entirely -- spacing is a common
    表記ゆれ in Japanese titles (予測 符号化 vs 予測符号化). Titles and
    filenames themselves are never rewritten.
    """
    t = unicodedata.normalize("NFKC", title).casefold()
    for ch in "_-–—":
        t = t.replace(ch, " ")
    t = "".join(
        c for c in t if not (unicodedata.category(c).startswith("P") and c not in "+#")
    )
    return re.sub(r"\s+", "", t)


def note_title(content: str, fallback: str) -> str:
    """The note's title: its first '# ' heading, or ``fallback``."""
    lines = content.splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip()
    return fallback


def note_body(content: str) -> str:
    """The note's body: text after the create_note header block.

    The header ends at the first '---' line; without one, the body is
    everything after the '# ' title line; without either, the whole text.
    """
    lines = content.splitlines()
    if lines and lines[0].startswith("# "):
        for i, line in enumerate(lines[:12]):
            if line.strip() == "---":
                body = "\n".join(lines[i + 1 :])
                return body[1:] if body.startswith("\n") else body
        return "\n".join(lines[1:])
    return content


def _created_of(content: str) -> str | None:
    m = _CREATED_RE.search(content)
    return m.group(1) if m else None


def _archive_target(wiki_root: Path, name: str) -> Path:
    """Where ``name`` goes inside .archive/ (suffix -2, -3... if taken)."""
    archive = wiki_root / ".archive"
    target = archive / name
    if not target.exists():
        return target
    stem = name[:-3] if name.endswith(".md") else name
    n = 2
    while True:
        candidate = archive / f"{stem}-{n}.md"
        if not candidate.exists():
            return candidate
        n += 1


# ── orchestration ────────────────────────────────────────


def run_tidy(
    memory_root: Path,
    wiki_root: Path | None,
    *,
    apply: bool = False,
    retire_dumps: bool = False,
    timestamp: str | None = None,
) -> dict:
    """Plan (and, with ``apply``, perform) a tidy of MEMORY.md and the wiki.

    Dry-run by default: nothing on disk changes. On apply, every file that
    will be written or moved is first copied to
    ``<memory_root>/state/backup/<timestamp>/`` so the run is reversible.
    Files are never deleted: colliding notes merge into a keeper and the
    donor moves to the wiki's ``.archive/``; date dumps (only with
    ``retire_dumps``) move there too, byte-for-byte.
    """
    memory_root = Path(memory_root)
    report: dict = {
        "applied": apply,
        "backup_dir": None,
        "memory": {"cleaned": 0, "merged": 0, "blank_runs": 0, "changed": False},
        "wiki": {
            "sanitized": [],
            "deduped": {},
            "merged": [],
            "retired": [],
            "index_regenerated": False,
        },
    }

    # ── MEMORY.md ──
    mem_path = memory_root / "MEMORY.md"
    mem_new: str | None = None
    if mem_path.exists():
        mem_orig = mem_path.read_text(encoding="utf-8")
        mem_new, stats = tidy_memory_text(mem_orig)
        report["memory"].update(stats)
        report["memory"]["changed"] = mem_new != mem_orig
        if not report["memory"]["changed"]:
            mem_new = None

    # ── wiki planning (all in memory; disk untouched until apply) ──
    notes: list[Path] = []
    if wiki_root is not None and Path(wiki_root).is_dir():
        wiki_root = Path(wiki_root)
        notes = sorted(p for p in wiki_root.glob("*.md") if p.name != "_index.md")
    dumps = [p for p in notes if retire_dumps and _DATE_DUMP_RE.match(p.stem)]
    dump_set = set(dumps)
    working = [p for p in notes if p not in dump_set]

    originals: dict[Path, str] = {}
    planned: dict[Path, str] = {}
    for p in working:
        originals[p] = p.read_text(encoding="utf-8")
        sanitized = sanitize_note_text(originals[p])
        if sanitized != originals[p]:
            report["wiki"]["sanitized"].append(p.name)
        new, removed = dedupe_note_lines(sanitized)
        if removed:
            report["wiki"]["deduped"][p.name] = removed
        planned[p] = new
    for p in dumps:
        originals[p] = p.read_text(encoding="utf-8")
    report["wiki"]["retired"] = [p.name for p in dumps]

    # ── title collisions: merge donors into the keeper, archive donors ──
    groups: dict[str, list[Path]] = {}
    for p in working:
        key = normalize_title(note_title(planned[p], p.stem))
        if key:
            groups.setdefault(key, []).append(p)
    donors: list[Path] = []
    for paths in groups.values():
        if len(paths) < 2:
            continue
        keeper = min(
            paths,
            key=lambda p: (_created_of(planned[p]) is None, _created_of(planned[p]) or "", p.name),
        )
        keeper_lines = {ln.rstrip() for ln in planned[keeper].splitlines()}
        for donor in sorted((p for p in paths if p != keeper), key=lambda p: p.name):
            body = note_body(planned[donor])
            additions = [
                ln
                for ln in body.splitlines()
                if ln.strip() and ln.rstrip() not in keeper_lines
            ]
            if additions:
                planned[keeper] = (
                    planned[keeper].rstrip() + "\n\n" + "\n".join(additions) + "\n"
                )
                keeper_lines.update(ln.rstrip() for ln in additions)
            donors.append(donor)
            report["wiki"]["merged"].append((donor.name, keeper.name))
    donor_set = set(donors)

    writes = {
        p: planned[p]
        for p in working
        if p not in donor_set and planned[p] != originals[p]
    }
    move_sources = dumps + donors
    wiki_changed = bool(writes or move_sources)

    if not apply or (mem_new is None and not writes and not move_sources):
        return report

    # ── backup BEFORE any mutation ──
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = memory_root / "state" / "backup" / ts
    n = 2
    while backup_dir.exists():
        backup_dir = memory_root / "state" / "backup" / f"{ts}-{n}"
        n += 1
    if mem_new is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mem_path, backup_dir / "MEMORY.md")
    for p in list(writes) + move_sources:
        (backup_dir / "wiki").mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, backup_dir / "wiki" / p.name)
    report["backup_dir"] = str(backup_dir)

    # ── mutate ──
    if mem_new is not None:
        mem_path.write_text(mem_new, encoding="utf-8")
    if move_sources:
        (wiki_root / ".archive").mkdir(exist_ok=True)
        for src in move_sources:
            shutil.move(str(src), str(_archive_target(wiki_root, src.name)))
    for p, text in writes.items():
        p.write_text(text, encoding="utf-8")
    if wiki_changed:
        from saku.wiki import regenerate_index

        regenerate_index(wiki_root)
        report["wiki"]["index_regenerated"] = True
    return report


def _resolve_roots() -> tuple[Path, Path]:
    from saku import config as cfg_mod

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    return memory_root, cfg_mod.resolve_wiki_root(cfg, base, memory_root)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    apply = "--apply" in args
    retire_dumps = "--retire-dumps" in args
    memory_root, wiki_root = _resolve_roots()
    report = run_tidy(memory_root, wiki_root, apply=apply, retire_dumps=retire_dumps)

    mode = "apply" if apply else "dry-run"
    print(f"[*] saku tidy ({mode})")
    m = report["memory"]
    mem_note = "" if m["changed"] else " (no change)"
    print(
        f"  MEMORY.md: cleaned {m['cleaned']}, merged {m['merged']}, "
        f"blank-runs {m['blank_runs']}{mem_note}"
    )
    w = report["wiki"]
    print(
        f"  wiki: sanitized {len(w['sanitized'])}, deduped {len(w['deduped'])}, "
        f"merged {len(w['merged'])}, retired {len(w['retired'])}"
    )
    for donor, keeper in w["merged"]:
        print(f"    merged: {donor} -> {keeper} (donor moved to .archive/)")
    for name in w["retired"]:
        print(f"    retired: {name} -> .archive/")
    if report["backup_dir"]:
        print(f"  backup: {report['backup_dir']}")
    if not apply:
        print("  (dry-run: nothing was written. Re-run with --apply to commit.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
