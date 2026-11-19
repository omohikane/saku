"""Learn from the Owner's Obsidian daily notes (#68).

Dreaming already distils SAKU's own records (journal/monologue) into durable
memory. The daily notes are the other half of the picture: what the Owner
actually did, decided and noticed. `saku learn` reads them and promotes the
durable parts into `MEMORY.md` through the same extraction and append path
(`dreaming.extract_items_from_text` + `dreaming.append_items`).

Three rules shape the module:

- **The notes are read-only.** Nothing here writes into the vault outside
  SAKU's own memory root.
- **Propose first.** The default run shows what would be learned and writes
  nothing; only ``--apply`` touches `MEMORY.md` and the state file. (`tidy`
  sets the same precedent.)
- **Only changed notes are re-read.** A note's content hash is stored in
  `state/learning.json`, so editing a note re-opens it and a run over 200
  unchanged notes costs nothing.

Provenance is `daily/<date>` (#79), so a promoted fact can be traced back to
the note it came from.

With `[learning] write_wiki` the same items are also organised into concept
notes under `wiki/`: one note per idea, never one per day (the vault's own
`tidy --retire-dumps` archives date-stamped dumps for exactly that reason).
An existing concept is appended to, never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from saku import config as saku_config
from saku import dreaming, provenance

_DATE_STEM_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Japanese and international phone shapes. Deliberately broad: a false positive
# only costs a masked number in a prompt; a false negative sends a real one.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-() ]{7,}\d)(?!\d)")
_CONCEPT_BLOCK_RE = re.compile(r"\[CONCEPT\](.*?)\[/CONCEPT\]", re.DOTALL)
_CONCEPT_FIELD_RE = re.compile(r"^([a-z_]+)\s*:\s*(.*)$", re.MULTILINE)
_DIGIT_RUN_RE = re.compile(r"\d{7,}")

STATE_VERSION = 1


@dataclass
class NoteInfo:
    path: Path
    name: str
    date: str
    sha256: str


def resolve_dir(cfg: dict, config_base: Path, memory_root: Path) -> Path:
    """Resolve the daily-note directory from ``[learning] dir``.

    Empty means the vault convention: ``<vault>/01_Notes/Daily_Note`` where the
    vault is the one that owns the memory root (``<vault>/_saku/memory``).
    """
    value = saku_config.expand_env(str(cfg.get("learning", {}).get("dir", "") or "")).strip()
    if value:
        p = Path(value).expanduser()
        return (p if p.is_absolute() else (config_base / p)).resolve()
    return (memory_root.parent.parent / "01_Notes" / "Daily_Note").resolve()


def resolve_state_file(cfg: dict, memory_root: Path) -> Path:
    value = str(cfg.get("learning", {}).get("state_file", "") or "").strip()
    p = Path(value or "state/learning.json").expanduser()
    return p if p.is_absolute() else memory_root / p


def _note_date(path: Path, text: str) -> str:
    m = _DATE_STEM_RE.match(path.stem)
    if m:
        return m.group(1)
    fm = re.search(r'^date:\s*"?(\d{4}-\d{2}-\d{2})"?', text, re.MULTILINE)
    return fm.group(1) if fm else ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_frontmatter(text: str) -> str:
    """Drop Obsidian YAML frontmatter so only the note body reaches the model."""
    return _FRONTMATTER_RE.sub("", text, count=1)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _mask_phone(match: re.Match) -> str:
    """Mask a phone-shaped run, but leave dates alone.

    ``2026-11-18`` fits the broad phone shape (digits and hyphens), so the
    decision is made on the digit count and on the ISO-date form.
    """
    candidate = match.group(0).strip()
    if _ISO_DATE_RE.match(candidate):
        return match.group(0)
    digits = sum(ch.isdigit() for ch in candidate)
    return "[redacted-phone]" if 10 <= digits <= 15 else match.group(0)


def has_contact_details(text: str) -> bool:
    """Whether a sentence looks like it carries a phone number, e-mail or ID."""
    if _EMAIL_RE.search(text) or _DIGIT_RUN_RE.search(text):
        return True
    return any(
        _mask_phone(m) == "[redacted-phone]" for m in _PHONE_RE.finditer(text)
    )


def redact(text: str) -> str:
    """Mask contact details before the note leaves the machine or enters memory."""
    text = _EMAIL_RE.sub("[redacted-email]", text)
    return _PHONE_RE.sub(_mask_phone, text)


def normalize(text: str) -> str:
    """Body text without frontmatter, with runs of blank lines collapsed."""
    body = strip_frontmatter(text)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def list_notes(directory: Path) -> list[NoteInfo]:
    """Daily notes, newest first. Only ``YYYY-MM-DD*.md`` names qualify."""
    if not directory.is_dir():
        return []
    notes = []
    for p in sorted(directory.glob("*.md")):
        if not _DATE_STEM_RE.match(p.stem):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        notes.append(
            NoteInfo(
                path=p,
                name=p.name,
                date=_note_date(p, text),
                sha256=_sha256(text),
            )
        )
    notes.sort(key=lambda n: n.date, reverse=True)
    return notes


# ── State ───────────────────────────────────────────────
def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "notes": {}}
    if not isinstance(data, dict) or not isinstance(data.get("notes"), dict):
        return {"version": STATE_VERSION, "notes": {}}
    data.setdefault("version", STATE_VERSION)
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["version"] = STATE_VERSION
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def is_processed(state: dict, note: NoteInfo, *, now: float | None = None) -> bool:
    """Whether this note's current content has already been handled (#129).

    ``ok``, ``empty`` and ``skipped`` are terminal. ``error`` is retryable, but
    only after its backoff delay; an entry written by an older extractor (or by
    the pre-#129 schema, which stored no version) is reprocessed.
    """
    entry = state.get("notes", {}).get(note.name)
    if not entry or entry.get("sha256") != note.sha256:
        return False
    if entry.get("extractor") != dreaming.EXTRACTOR_VERSION:
        return False
    status = entry.get("status", "ok")
    if status in ("ok", "empty", "skipped"):
        return True
    if status == "error":
        retry_at = float(entry.get("retry_after", 0) or 0)
        return (time.time() if now is None else now) < retry_at
    return False


def retry_delay(config: "saku_config.LearningConfig", attempts: int) -> int:
    """Exponential backoff for a failed extraction, capped by config."""
    base = max(1, config.retry_base_seconds)
    delay = base * (2 ** max(0, attempts - 1))
    return int(min(delay, max(base, config.retry_max_seconds)))


# ── Selection ───────────────────────────────────────────
def select_notes(
    notes: list[NoteInfo],
    state: dict,
    *,
    limit: int = 0,
    since: str = "",
    only: str = "",
    rebuild: bool = False,
    now: float | None = None,
) -> list[NoteInfo]:
    """Notes that still need learning, newest first."""
    picked = []
    for note in notes:
        if only and note.date != only and note.name != only:
            continue
        if since and note.date < since:
            continue
        if not rebuild and is_processed(state, note, now=now):
            continue
        picked.append(note)
    if limit and limit > 0:
        picked = picked[:limit]
    return picked


# ── One run ─────────────────────────────────────────────
def learn_notes(
    notes: list[NoteInfo],
    *,
    memory_root: Path,
    config: "saku_config.LearningConfig",
    state: dict,
    apply: bool = False,
    llm_cfg=None,
    now: float | None = None,
) -> dict:
    """Extract and (when ``apply``) store memory items from ``notes``.

    Returns a report the CLI prints: per-note items, skipped notes and totals.
    The state is only written for notes that were actually processed.
    """
    report = {
        "dry_run": not apply,
        "processed": [],
        "skipped": [],
        "errors": [],
        "empties": 0,
        "items": 0,
        "added": 0,
    }
    memory_path = memory_root / "MEMORY.md"
    if llm_cfg is None:
        # [llm] learn routes the note excerpts; unset falls back to the default
        # profile, exactly like every other role.
        llm_cfg = dreaming.agent.llm_for_role("learn")

    for note in notes:
        try:
            raw = note.path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            report["skipped"].append((note.name, f"read failed: {e}"))
            continue
        body = normalize(raw)
        if config.redact:
            body = redact(body)
        if len(body) < config.min_chars:
            report["skipped"].append((note.name, "below min_chars (template only)"))
            if apply:
                state.setdefault("notes", {})[note.name] = {
                    "sha256": note.sha256,
                    "status": "skipped",
                    "learned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "items": 0,
                    "skipped": "min_chars",
                    "extractor": dreaming.EXTRACTOR_VERSION,
                }
            continue
        if config.max_chars and len(body) > config.max_chars:
            body = body[: config.max_chars]

        result = dreaming.extract_items_from_text_result(
            body, date=note.date, kind="daily", llm_cfg=llm_cfg
        )
        items = result.items

        if result.status == "error":
            # A failed call is retryable, never "nothing to learn" (#129).
            # Record the attempt and a backoff deadline so the note is not
            # re-selected on the next run and a stuck model cannot hammer away.
            report["errors"].append((note.name, result.error or "extraction failed"))
            if apply:
                previous = state.get("notes", {}).get(note.name, {})
                attempts = int(previous.get("attempts", 0)) + 1
                base_ts = time.time() if now is None else now
                state.setdefault("notes", {})[note.name] = {
                    "sha256": note.sha256,
                    "status": "error",
                    "attempts": attempts,
                    "retry_after": base_ts + retry_delay(config, attempts),
                    "error": (result.error or "extraction failed")[:200],
                    "extractor": dreaming.EXTRACTOR_VERSION,
                    "learned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            continue

        report["processed"].append((note.name, items))
        report["items"] += len(items)
        if result.status == "empty":
            report["empties"] += 1

        added: list[str] = []
        if apply and items and config.write_memory:
            added = dreaming.append_items(
                memory_path,
                items,
                note.date,
                source=provenance.make_ref(provenance.KIND_DAILY, note.date),
            )
            report["added"] += len(added)

        if apply and (config.write_memory or not items):
            entry = {
                "sha256": note.sha256,
                "status": result.status,
                "items": len(items),
                "extractor": dreaming.EXTRACTOR_VERSION,
                "learned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            if items:
                entry["added"] = len(added)
            state.setdefault("notes", {})[note.name] = entry
    return report


# ── Wiki: one note per concept ──────────────────────────
def _wiki_title(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return path.stem
    return (lines[0].lstrip("# ").strip() if lines else path.stem) or path.stem


def valid_concept_title(title: str) -> bool:
    """Reject titles that would break the wiki's conventions (#68).

    No per-day notes (``tidy --retire-dumps`` archives those), no ``MEMORY``
    shadow, nothing that slugifies to the empty fallback, and no overlong
    headline.
    """
    from saku import wiki

    t = unicodedata.normalize("NFKC", title or "").strip()
    if not t or len(t) > 40:
        return False
    if _DATE_STEM_RE.match(t):
        return False
    if t.casefold() == "memory":
        return False
    return wiki.slugify(t) not in ("", "note")


def clean_concept_body(body: str) -> str:
    """Drop sentences carrying long digit runs (contact details, IDs)."""
    parts = re.split(r"(?<=。)", body or "")
    kept = [p.strip() for p in parts if p.strip() and not has_contact_details(p)]
    return "".join(kept).strip()


def build_concepts(
    items: list[tuple[str, str]],
    *,
    date: str,
    wiki_root: Path,
    llm_cfg=None,
) -> list[dict]:
    """Group a day's items into concept notes. Returns ``title/tags/links/body``."""
    if not items:
        return []
    existing = []
    if wiki_root.is_dir():
        existing = sorted(
            _wiki_title(p) for p in wiki_root.glob("*.md") if p.name != "_index.md"
        )
    listing = "\n".join(f"- {t}" for t in existing[:80]) or "(まだ無い)"
    item_lines = "\n".join(f"- [{category}] {content}" for category, content in items)
    prompt = f"""[system] 今日の記憶を、wiki の概念ノートに整理してください。

【既存ノート（同じ概念ならこの title をそのまま使う）】
{listing}

【今日の記憶】
{item_lines}

ルール:
- 1ノート = 1概念。日付ごとのまとめノートは作らない（title に日付を入れない）
- 既存ノートに吸収できる概念は、その title を一字一句そのまま使う
- 新しい概念だけ [CONCEPT] を出す。無ければ [NO_CONCEPT]
- title は日本語の名詞句で 30 文字以内。記号は最小限
- body は 1〜3 文の要約。箇条書きにしない。固有名詞の列挙より傾向を書く
- 既存ノートの内容を繰り返さない（新しい情報だけを書く）
- 電話番号・メールアドレス・長い数字列は書かない
- tags は 2〜6 個（カンマ区切り）。learn は自動で付くので書かない
- links は [[MEMORY]] を必ず含め、関連する既存ノートがあれば [[名前]] で足す

出力形式（この形式のみ。ツールは使わない）:
[CONCEPT]
title: 概念名
tags: タグ1, タグ2
links: [[MEMORY]], [[関連ノート]]
body: 1〜3文の要約
[/CONCEPT]
"""
    history = [
        {"role": "system", "content": dreaming.agent.build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    # Direct LLM call, not the agent loop: the loop would execute any
    # tool-shaped text the model emits. `[WIKI]` is a real tool name, which is
    # why the block marker is `[CONCEPT]` and why nothing here goes through
    # tool dispatch.
    raw = dreaming._chat_stream(
        history,
        llm_cfg or dreaming.agent.llm_for_role("learn"),
        on_token=lambda _t: None,
    )
    if not raw or raw.startswith("[ERROR]"):
        return []

    concepts = []
    for block in _CONCEPT_BLOCK_RE.findall(raw):
        fields = {
            m.group(1).lower(): m.group(2).strip()
            for m in _CONCEPT_FIELD_RE.finditer(block)
        }
        title = fields.get("title", "")
        body = clean_concept_body(fields.get("body", ""))
        if not valid_concept_title(title) or not body:
            continue
        concepts.append(
            {
                "title": unicodedata.normalize("NFKC", title).strip(),
                "tags": fields.get("tags", ""),
                "links": fields.get("links", "") or "[[MEMORY]]",
                "body": body,
            }
        )
    return concepts


def _stamp_source(path: Path, date: str) -> None:
    """Add the ``> source: daily/<date>`` line once, right after ``> updated:``."""
    text = path.read_text(encoding="utf-8")
    if "> source:" in text:
        return
    line = provenance.source_line(
        provenance.make_ref(provenance.KIND_DAILY, date)
    )
    lines = text.splitlines()
    for i, current in enumerate(lines):
        if current.startswith("> updated:"):
            lines.insert(i + 1, line)
            break
    else:
        lines.insert(1, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_concept(path: Path, body: str, date: str) -> bool:
    """Append new sentences to an existing concept note. Returns True if changed."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    bullets = [
        f"- {part.strip()}"
        for part in re.split(r"(?<=。)", body)
        if part.strip()
    ]
    # Compare on text, not on the bullet marker: a freshly created note stores
    # its summary as a plain sentence, later appends store bullets.
    existing_text = "\n".join(lines)
    fresh = [b for b in bullets if b[2:] not in existing_text]
    if not fresh:
        return False

    if "## 追記" in text:
        insert_at = lines.index("## 追記") + 1
        # Skip a blank line directly under the heading.
        if insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        lines[insert_at:insert_at] = fresh
    else:
        lines.extend(["", "## 追記", ""] + fresh)
    for i, current in enumerate(lines):
        if current.startswith("> updated:"):
            # Notes are processed newest first, so a later run can come from an
            # older day: never move `updated` backwards.
            previous = current.split(":", 1)[1].strip()
            if date > previous:
                lines[i] = f"> updated: {date}"
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def apply_concepts(
    concepts: list[dict],
    *,
    wiki_root: Path,
    date: str,
) -> dict:
    """Create or extend the concept notes. Never overwrites an existing note."""
    from saku import wiki
    from saku.tidy import normalize_title

    report = {"created": [], "updated": [], "skipped": []}
    if not concepts:
        return report

    existing = {}
    if wiki_root.is_dir():
        for p in wiki_root.glob("*.md"):
            if p.name != "_index.md":
                existing[normalize_title(_wiki_title(p))] = p

    wrote = False
    for concept in concepts:
        title = concept.get("title", "")
        # apply_concepts is a public helper: do not trust the caller to have
        # filtered titles or contact details already.
        body = clean_concept_body(concept.get("body", ""))
        if not valid_concept_title(title) or not body:
            report["skipped"].append((title, "invalid concept"))
            continue
        title = unicodedata.normalize("NFKC", title).strip()
        target = existing.get(normalize_title(title))
        if target is None:
            candidate = wiki.note_path(wiki_root, title)
            if candidate.exists():
                target = candidate
        if target is None:
            tags = "auto, learn"
            if concept.get("tags"):
                tags += ", " + concept["tags"]
            wiki.create_note(
                wiki_root,
                title,
                body,
                tags=tags,
                links=concept.get("links") or "[[MEMORY]]",
                date=date,
            )
            path = wiki.note_path(wiki_root, title)
            _stamp_source(path, date)
            existing[normalize_title(title)] = path
            report["created"].append(title)
            wrote = True
            continue
        if _append_concept(target, body, date):
            report["updated"].append(title)
            wrote = True
        else:
            report["skipped"].append((title, "already recorded"))

    if wrote:
        wiki.regenerate_index(wiki_root)
    return report


# ── CLI ─────────────────────────────────────────────────
def run_cli(
    cfg: dict,
    config_base: Path,
    memory_root: Path,
    *,
    apply: bool = False,
    limit: int | None = None,
    since: str = "",
    only: str = "",
    rebuild: bool = False,
    llm_cfg=None,
    verbose: bool = True,
    now: float | None = None,
) -> dict:
    """Resolve config paths, select notes and learn. Shared by the CLI and tests."""
    lcfg = saku_config.load_learning_config(cfg)
    directory = resolve_dir(cfg, config_base, memory_root)
    state_path = resolve_state_file(cfg, memory_root)
    state = load_state(state_path)

    notes = list_notes(directory)
    picked = select_notes(
        notes,
        state,
        limit=lcfg.max_notes_per_run if limit is None else limit,
        since=since,
        only=only,
        rebuild=rebuild,
        now=now,
    )

    if verbose:
        print(f"SAKU learn ({'apply' if apply else 'dry-run'}; notes: {directory})")
        print(f"  known notes: {len(notes)} / to process: {len(picked)}")
        if not lcfg.write_memory:
            print("  [!] [learning] write_memory = false: nothing will be stored")

    report = learn_notes(
        picked,
        memory_root=memory_root,
        config=lcfg,
        state=state,
        apply=apply and lcfg.write_memory,
        llm_cfg=llm_cfg,
        now=now,
    )
    report.setdefault("wiki_created", [])
    report.setdefault("wiki_updated", [])

    # The wiki stage is where "learning" becomes organised knowledge: one note
    # per concept, reusing existing titles (see build_concepts). It runs only
    # when applying, so a dry run never pays for the second call.
    if apply and lcfg.write_wiki:
        wiki_root = saku_config.resolve_wiki_root(cfg, config_base, memory_root)
        dates = {n.name: n.date for n in picked}
        for name, items in report["processed"]:
            if not items:
                continue
            concepts = build_concepts(
                items,
                date=dates.get(name, ""),
                wiki_root=wiki_root,
                llm_cfg=llm_cfg,
            )
            result = apply_concepts(
                concepts, wiki_root=wiki_root, date=dates.get(name, "")
            )
            report["wiki_created"] += result["created"]
            report["wiki_updated"] += result["updated"]

    if verbose:
        for name, items in report["processed"]:
            print(f"  {name}: {len(items)} item(s)")
            for category, content in items:
                print(f"    - [{category}] {content}")
        for name, why in report["skipped"]:
            print(f"  - skip {name}: {why}")
        for name, why in report["errors"]:
            print(f"  - error {name}: {why} (will retry after the backoff)")
        if report["empties"]:
            print(f"  - {report['empties']} note(s) had nothing durable (recorded as processed)")
        verb = "added" if apply else "would add"
        print(
            f"  total: {report['items']} item(s) extracted, "
            f"{report['added']} {verb}"
        )
        if apply and lcfg.write_wiki:
            print(
                f"  wiki: {len(report['wiki_created'])} new, "
                f"{len(report['wiki_updated'])} updated"
            )
        elif not apply:
            print("  wiki: skipped in dry-run")
        if apply and lcfg.write_memory:
            print(f"  state: {state_path}")
        elif not apply:
            print("  apply with: saku learn --apply")

    if apply and lcfg.write_memory:
        save_state(state_path, state)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="saku learn",
        description="Distil the Owner's daily notes into MEMORY.md (dry-run by default)",
    )
    parser.add_argument("--apply", action="store_true", help="write MEMORY.md and state")
    parser.add_argument("--dry-run", action="store_true", help="force proposal-only (default)")
    parser.add_argument("--limit", type=int, default=None, help="process at most N notes")
    parser.add_argument("--all", action="store_true", help="process every unprocessed note")
    parser.add_argument("--since", default="", help="only notes on/after YYYY-MM-DD")
    parser.add_argument("--note", default="", help="one note (date or filename)")
    parser.add_argument("--rebuild", action="store_true", help="ignore the state file")
    args = parser.parse_args(argv)

    cfg, base = saku_config.load_config()
    memory_root = saku_config.resolve_memory_root(cfg, base)
    report = run_cli(
        cfg,
        base,
        memory_root,
        apply=args.apply and not args.dry_run,
        limit=0 if args.all else args.limit,
        since=args.since,
        only=args.note,
        rebuild=args.rebuild,
    )
    if report["processed"] and all(not items for _name, items in report["processed"]):
        # A quiet run is a valid result ("nothing durable in these notes"), not
        # a failure, but say so explicitly instead of printing a bare zero.
        print("  (no durable items found; notes were read but nothing qualified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
