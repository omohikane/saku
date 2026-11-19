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
"""

from __future__ import annotations

import hashlib
import json
import re
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


def redact(text: str) -> str:
    """Mask contact details before the note leaves the machine or enters memory."""
    text = _EMAIL_RE.sub("[redacted-email]", text)
    return _PHONE_RE.sub("[redacted-phone]", text)


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


def is_processed(state: dict, note: NoteInfo) -> bool:
    entry = state.get("notes", {}).get(note.name)
    return bool(entry) and entry.get("sha256") == note.sha256


# ── Selection ───────────────────────────────────────────
def select_notes(
    notes: list[NoteInfo],
    state: dict,
    *,
    limit: int = 0,
    since: str = "",
    only: str = "",
    rebuild: bool = False,
) -> list[NoteInfo]:
    """Notes that still need learning, newest first."""
    picked = []
    for note in notes:
        if only and note.date != only and note.name != only:
            continue
        if since and note.date < since:
            continue
        if not rebuild and is_processed(state, note):
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
) -> dict:
    """Extract and (when ``apply``) store memory items from ``notes``.

    Returns a report the CLI prints: per-note items, skipped notes and totals.
    The state is only written for notes that were actually processed.
    """
    report = {
        "dry_run": not apply,
        "processed": [],
        "skipped": [],
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
                    "learned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "items": 0,
                    "skipped": "min_chars",
                }
            continue
        if config.max_chars and len(body) > config.max_chars:
            body = body[: config.max_chars]

        items = dreaming.extract_items_from_text(
            body, date=note.date, kind="daily", llm_cfg=llm_cfg
        )
        report["processed"].append((note.name, items))
        report["items"] += len(items)

        added: list[str] = []
        if apply and items and config.write_memory:
            added = dreaming.append_items(
                memory_path,
                items,
                note.date,
                source=provenance.make_ref(provenance.KIND_DAILY, note.date),
            )
            report["added"] += len(added)
            state.setdefault("notes", {})[note.name] = {
                "sha256": note.sha256,
                "learned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "items": len(items),
                "added": len(added),
            }
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
    )

    if verbose:
        for name, items in report["processed"]:
            print(f"  {name}: {len(items)} item(s)")
            for category, content in items:
                print(f"    - [{category}] {content}")
        for name, why in report["skipped"]:
            print(f"  - skip {name}: {why}")
        verb = "added" if apply else "would add"
        print(
            f"  total: {report['items']} item(s) extracted, "
            f"{report['added']} {verb}"
        )
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
