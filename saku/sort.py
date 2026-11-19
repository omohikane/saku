"""Proposal-first classification of clippings into reference folders (#72).

``saku sort`` reads Markdown files from a clippings directory and asks the LLM
which existing reference folder each one belongs to. It never moves anything on
the first pass: the only write is a proposal under ``state/sort/<stamp>.json``.
``--apply`` consumes a saved proposal, classifies nothing again, backs up every
file it is about to move under ``state/backup/<stamp>/sort/``, and moves only
the entries the model was confident about.

Three rules keep the feature safe enough to run on the Owner's vault:

- **Existing folders only.** The prompt offers the folders that already exist
  under the target directory and nothing else; a folder that vanished between
  proposal and apply is a skip, never a ``mkdir``.
- **Proposing is separate from applying.** A classification is a suggestion
  until a person (or an explicitly enabled agent) consumes it.
- **Nothing is deleted.** A name collision becomes ``name-2.md`` (then ``-3``),
  and every moved file is copied to the backup directory first.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from saku import config as saku_config
from saku import core as agent
from saku.agent_loop import run_agent_loop

REVIEW_FOLDER = "要確認"

_CLASSIFY_RE = re.compile(
    r"\[CLASSIFY\]\s*(.*?)\s*\[/CLASSIFY\]", re.DOTALL
)
_FIELD_RE = re.compile(r"^([a-z_]+)\s*:\s*(.*)$", re.MULTILINE)


@dataclass
class SortPaths:
    vault_root: Path
    source_dir: Path
    target_dir: Path


@dataclass
class Proposal:
    stamp: str
    items: list[dict] = field(default_factory=list)
    review_below: float = 0.7
    created_at: str = ""
    applied_at: str = ""
    source_dir: str = ""
    target_dir: str = ""

    @property
    def applied(self) -> bool:
        return bool(self.applied_at)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "stamp": self.stamp,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
            "review_below": self.review_below,
            "source_dir": self.source_dir,
            "target_dir": self.target_dir,
            "items": self.items,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        items = data.get("items") or []
        return cls(
            stamp=str(data.get("stamp", "")),
            items=[dict(i) for i in items if isinstance(i, dict)],
            review_below=float(data.get("review_below", 0.7)),
            created_at=str(data.get("created_at", "")),
            applied_at=str(data.get("applied_at", "")),
            source_dir=str(data.get("source_dir", "")),
            target_dir=str(data.get("target_dir", "")),
        )


# ── Path resolution ─────────────────────────────────────
def resolve_paths(
    cfg: dict, config_base: Path, memory_root: Path
) -> SortPaths:
    """Resolve the clipping source and reference target from ``[sort]``.

    ``[sort] vault_root`` defaults to the vault that owns the memory root
    (``memory_root/../..`` for the usual ``<vault>/_saku/memory`` layout);
    both directories are resolved against it unless absolute.
    """
    s = cfg.get("sort", {})

    def _dir(value: str) -> Path | None:
        value = saku_config.expand_env(str(value or "")).strip()
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else (vault_root / p)

    vault = str(s.get("vault_root", "") or "")
    if vault.strip():
        vault_root = _dir(vault) or memory_root
    else:
        vault_root = memory_root.parent.parent
    vault_root = vault_root.resolve()

    source = _dir(s.get("source_dir", "Clippings")) or vault_root / "Clippings"
    target = _dir(s.get("target_dir", "02_Reference")) or vault_root / "02_Reference"
    return SortPaths(vault_root, source.resolve(), target.resolve())


def existing_folders(target_dir: Path) -> list[str]:
    """Names of the folders that already exist under the target directory."""
    if not target_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in target_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def list_clippings(source_dir: Path, limit: int = 0) -> list[Path]:
    """Markdown files waiting to be classified, newest first."""
    if not source_dir.is_dir():
        return []
    files = [p for p in source_dir.glob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit] if limit > 0 else files


def _excerpt(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Drop YAML frontmatter so the model sees the content, not the metadata.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


# ── Classification ──────────────────────────────────────
def _parse_blocks(raw: str, allowed: set[str]) -> list[dict]:
    items = []
    for block in _CLASSIFY_RE.findall(raw):
        fields = {m.group(1).lower(): m.group(2).strip() for m in _FIELD_RE.finditer(block)}
        name = fields.get("file", "")
        if not name:
            continue
        folder = fields.get("folder", "").strip()
        try:
            confidence = float(fields.get("confidence", "0") or 0)
        except ValueError:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if folder not in allowed:
            folder = REVIEW_FOLDER
            confidence = 0.0
        items.append(
            {
                "file": name,
                "folder": folder,
                "confidence": round(confidence, 2),
                "reason": fields.get("reason", ""),
            }
        )
    return items


def classify(
    files: list[Path],
    folders: list[str],
    *,
    max_chars: int = 800,
    llm_cfg=None,
) -> list[dict]:
    """Ask the LLM which folder each clipping belongs to.

    Returns one dict per file it could read. A file the model did not answer
    for is returned as ``要確認`` so it stays where it is instead of silently
    disappearing from the proposal.
    """
    if not files:
        return []

    listing = "\n".join(f"- {name}" for name in folders) or "(なし)"
    bodies = []
    for p in files:
        bodies.append(f"### ファイル: {p.name}\n{_excerpt(p, max_chars)}")
    prompt = f"""[system] クリッピングの分類です。以下のファイルを、既存フォルダのどれかに分類してください。

【既存フォルダ（この中からだけ選ぶ。新しいフォルダを作ってはいけない）】
{listing}

【ファイル】
{chr(10).join(bodies)}

出力形式（この形式のみ。前置き・後書きなし。1ファイルにつき1ブロック）:
[CLASSIFY]
file: ファイル名.md
folder: 既存フォルダ名（判断できない・どれにも合わないなら 要確認）
confidence: 0.0〜1.0
reason: 20文字以内の理由
[/CLASSIFY]

確信が持てない場合は folder に 要確認、confidence を 0.0 にすること。
ツールは使わないこと。
"""

    history = [
        {"role": "system", "content": agent.build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    result = run_agent_loop(
        history,
        llm_cfg or agent.llm_for_role("sort"),
        agent.context_config,
        agent.MEMORY_ROOT,
        agent.CODE_ROOT,
        max_turns=1,
    )
    if not result.last_raw or result.last_raw.startswith("[ERROR]"):
        raise RuntimeError(result.last_raw or "empty reply from the LLM")

    allowed = set(folders) | {REVIEW_FOLDER}
    parsed = {item["file"]: item for item in _parse_blocks(result.last_raw, allowed)}
    items = []
    for p in files:
        items.append(
            parsed.get(
                p.name,
                {
                    "file": p.name,
                    "folder": REVIEW_FOLDER,
                    "confidence": 0.0,
                    "reason": "no answer",
                },
            )
        )
    return items


# ── Proposals on disk ───────────────────────────────────
def _sort_state_dir(memory_root: Path) -> Path:
    return memory_root / "state" / "sort"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def save_proposal(memory_root: Path, proposal: Proposal) -> Path:
    d = _sort_state_dir(memory_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{proposal.stamp}.json"
    n = 2
    while path.exists():
        path = d / f"{proposal.stamp}-{n}.json"
        n += 1
    path.write_text(
        json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_proposal(path: Path) -> Proposal:
    return Proposal.from_dict(json.loads(path.read_text(encoding="utf-8")))


def latest_proposal(memory_root: Path) -> Path | None:
    d = _sort_state_dir(memory_root)
    if not d.is_dir():
        return None
    files = sorted(p for p in d.glob("*.json") if p.is_file())
    return files[-1] if files else None


def build_proposal(
    memory_root: Path,
    paths: SortPaths,
    *,
    limit: int = 0,
    max_chars: int = 800,
    review_below: float = 0.7,
    llm_cfg=None,
) -> Proposal:
    """Classify the clippings and return an unsaved proposal."""
    folders = existing_folders(paths.target_dir)
    files = list_clippings(paths.source_dir, limit)
    items = classify(files, folders, max_chars=max_chars, llm_cfg=llm_cfg)
    return Proposal(
        stamp=_stamp(),
        items=items,
        review_below=review_below,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_dir=str(paths.source_dir),
        target_dir=str(paths.target_dir),
    )


# ── Applying a proposal ─────────────────────────────────
def _unique_target(folder_dir: Path, name: str) -> Path:
    """Return a path that does not exist yet (``name-2.md``, ``name-3.md``...)."""
    candidate = folder_dir / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        candidate = folder_dir / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def apply_proposal(
    memory_root: Path,
    proposal: Proposal,
    *,
    review_below: float | None = None,
) -> dict:
    """Move the confident entries of a proposal. Never deletes, never mkdirs.

    Returns a report: ``moved``, ``skipped`` (each with a reason), ``backup_dir``.
    """
    report: dict = {"moved": [], "skipped": [], "backup_dir": ""}
    if proposal.applied:
        report["skipped"].append(("*", "proposal already applied"))
        return report

    threshold = proposal.review_below if review_below is None else review_below
    source = Path(proposal.source_dir)
    target = Path(proposal.target_dir)
    stamp = proposal.stamp or _stamp()

    backup_dir = memory_root / "state" / "backup" / stamp / "sort"
    pending = []
    for item in proposal.items:
        name = item.get("file", "")
        folder = item.get("folder", "")
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not name:
            continue
        if folder == REVIEW_FOLDER or confidence < threshold:
            report["skipped"].append((name, "needs review"))
            continue
        folder_dir = target / folder
        if not folder_dir.is_dir():
            report["skipped"].append((name, f"folder not found: {folder}"))
            continue
        src = source / name
        if not src.is_file():
            report["skipped"].append((name, "source missing"))
            continue
        pending.append((src, folder_dir))

    if not pending:
        return report

    backup_dir.mkdir(parents=True, exist_ok=True)
    for src, folder_dir in pending:
        dest = _unique_target(folder_dir, src.name)
        try:
            shutil.copy2(src, backup_dir / src.name)
            shutil.move(str(src), str(dest))
        except OSError as e:
            report["skipped"].append((src.name, f"move failed: {e}"))
            continue
        report["moved"].append((src.name, str(dest.relative_to(target))))
    report["backup_dir"] = str(backup_dir)
    return report


def mark_applied(memory_root: Path, proposal: Proposal) -> None:
    """Record that a proposal was consumed, so it cannot be applied twice."""
    proposal.applied_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d = _sort_state_dir(memory_root)
    path = d / f"{proposal.stamp}.json"
    if path.exists():
        path.write_text(
            json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── CLI ─────────────────────────────────────────────────
def _resolve() -> tuple[dict, Path, Path, SortPaths]:
    cfg, base = saku_config.load_config()
    memory_root = saku_config.resolve_memory_root(cfg, base)
    return cfg, base, memory_root, resolve_paths(cfg, base, memory_root)


def _print_report(report: dict, *, dry_run: bool) -> None:
    for name, reason in report["skipped"]:
        print(f"  - skip  {name}: {reason}")
    for name, dest in report["moved"]:
        verb = "would move" if dry_run else "moved"
        print(f"  - {verb}  {name} -> {dest}")
    if report.get("backup_dir") and not dry_run:
        print(f"  backup: {report['backup_dir']}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="saku sort",
        description="Classify Clippings into existing reference folders (proposal first)",
    )
    parser.add_argument(
        "--apply",
        nargs="?",
        const="latest",
        default="",
        metavar="STAMP",
        help="consume a saved proposal (default: the latest one)",
    )
    parser.add_argument("--limit", type=int, default=0, help="classify at most N files")
    parser.add_argument(
        "--review-below",
        type=float,
        default=None,
        help="confidence below this stays put (overrides [sort] review_below)",
    )
    args = parser.parse_args(argv)

    cfg, _base, memory_root, paths = _resolve()
    scfg = saku_config.load_sort_config(cfg)

    if args.apply:
        if args.apply == "latest":
            path = latest_proposal(memory_root)
            if path is None:
                print("[!] no saved proposal; run `saku sort` first")
                return 1
        else:
            path = _sort_state_dir(memory_root) / f"{args.apply}.json"
            if not path.is_file():
                print(f"[!] proposal not found: {path}")
                return 1
        proposal = load_proposal(path)
        print(f"SAKU sort --apply ({path.name})")
        print(f"  source: {proposal.source_dir}")
        print(f"  target: {proposal.target_dir}")
        report = apply_proposal(
            memory_root,
            proposal,
            review_below=args.review_below
            if args.review_below is not None
            else scfg.review_below,
        )
        if report["moved"]:
            mark_applied(memory_root, proposal)
        _print_report(report, dry_run=False)
        print(f"  moved: {len(report['moved'])} / skipped: {len(report['skipped'])}")
        return 0

    print(f"SAKU sort (proposal only; nothing is moved)")
    print(f"  source: {paths.source_dir}")
    print(f"  target: {paths.target_dir}")
    folders = existing_folders(paths.target_dir)
    if not folders:
        print(f"[!] no folders under {paths.target_dir}; nothing to classify into")
        return 1
    print(f"  folders: {', '.join(folders)}")
    proposal = build_proposal(
        memory_root,
        paths,
        limit=args.limit or scfg.max_files,
        max_chars=scfg.max_chars,
        review_below=args.review_below
        if args.review_below is not None
        else scfg.review_below,
    )
    if not proposal.items:
        print("[!] no clippings to classify")
        return 0
    for item in proposal.items:
        print(
            f"  {item['confidence']:.2f}  {item['folder']:<12} {item['file']}"
            + (f"  ({item['reason']})" if item.get("reason") else "")
        )
    path = save_proposal(memory_root, proposal)
    print(f"  proposal: {path}")
    print("  apply with: saku sort --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
