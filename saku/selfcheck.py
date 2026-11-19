"""Self-verification loop (#102): audit SAKU's own completion claims.

``verify.py`` (#39/#52) checks one reply's claimed file writes at the end of
a turn. That leaves three gaps, which this module closes at the day level:

- **Commit claims.** The #69 report cited a commit (``fd5712b``) that never
  existed. A hash is not a path, so the turn-level check never saw it.
- **Persistence.** A turn-level log line is gone by morning. Claims need a
  durable record the nightly reflection can judge, or an unverified
  self-image is promoted into principles as fact.
- **Recurrence.** One unbacked claim is an accident; the same kind on
  several days is a habit, and habits are what principles are for. A small
  JSON ledger counts flagged claims per kind across days.

The audit runs inside the nightly reflection (``reflection.py``): it
extracts the day's completion claims from SAKU's own journal / monologue
text, checks each evidence reference against the filesystem and git,
appends the rendered result to the day's journal, and updates the ledger.

Honesty rules:

- ``unchecked`` is not ``false``. If git cannot be asked (not installed, or
  the directory is not a repository), a commit claim stays ``unchecked``;
  it is only ``unbacked`` when a repository answered "no such object".
- A completion claim with **no** evidence reference at all is extracted as
  a ``bare`` claim and flagged, instead of being silently trusted
  (proposal 1 of #102: no evidence means 未確認, said out loud).
- Only SAKU's own words are audited: Owner blocks in the journal are
  skipped, as are ``<details>`` thinking blocks and previous audit
  sections (an audit quoting a claim must not re-flag it the next day).
- A day-level write check proves existence, not that the file was written
  that day; the turn-level timing check stays in ``verify.py``.

This module only reads the filesystem and git, plus two explicit writes,
both under the memory root: the journal audit section and the ledger.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .verify import claimed_writes

# Claim kinds.
KIND_WRITE = "write"  # "I wrote <path>" -- evidence is a file path
KIND_COMMIT = "commit"  # "I committed / fixed in <hash>" -- evidence is a git ref
KIND_BARE = "bare"  # "I fixed it" with no evidence reference at all

# Verification outcomes.
STATUS_VERIFIED = "verified"  # evidence checked and present
STATUS_UNBACKED = "unbacked"  # evidence checked and NOT there (contradicted)
STATUS_UNCHECKED = "unchecked"  # could not be checked (bare claim, git unavailable)

# A completion claim in past form. Mirrors verify.py's write verbs and adds
# the fix / send / commit verbs the #69 report used. Plans ("修正します")
# and passives ("記録されています") do not match.
_CLAIM_VERB_RE = re.compile(
    r"(?:記録|作成|更新|保存|追記|書き込|修正|送信|反映|マージ|コミット)"
    r"(?:しました|した|済み)"
    r"|(?:直しました|直した)"
    r"|完了しました"
    r"|(?:記録|作成|更新|保存|追記|修正)\s*[。.]?$"
)

# A git object name: 7-40 hex chars with at least one a-f letter, so plain
# numbers (dates, counts) are not mistaken for commit hashes.
_COMMIT_REF_RE = re.compile(r"\b(?=[0-9a-f]{7,40}\b)[0-9a-f]*[a-f][0-9a-f]*\b")

# English words that make a hash on the same line a commit claim
# ("committed as abc1234", "fixed in abc1234").
_COMMIT_CONTEXT_WORDS = ("commit", "コミット")

# Section heading marker of an audit block in the journal (see
# append_audit_to_journal); blocks under it are the audit's own output.
_AUDIT_HEADING = "自己検証"

_DETAILS_RE = re.compile(r"<details>.*?</details>", re.DOTALL)
_HEADING_RE = re.compile(r"^## .*$", re.MULTILINE)


@dataclass
class Claim:
    kind: str
    evidence: str  # path, commit ref, or "" for a bare claim
    line: str  # the source line the claim was extracted from
    status: str = STATUS_UNCHECKED


@dataclass
class AuditResult:
    date: str
    claims: list[Claim] = field(default_factory=list)

    @property
    def verified(self) -> list[Claim]:
        return [c for c in self.claims if c.status == STATUS_VERIFIED]

    @property
    def flagged(self) -> list[Claim]:
        """Claims that must not pass as fact: contradicted, or evidence-free."""
        return [
            c
            for c in self.claims
            if c.status == STATUS_UNBACKED or c.kind == KIND_BARE
        ]


# ── Extraction ──────────────────────────────────────────


def saku_voice(journal_text: str) -> str:
    """Return only SAKU's own words from a journal file's text.

    The journal interleaves ``**Owner**`` and ``**SAKU**`` blocks under
    ``## HH:MM`` headings. Owner blocks are skipped (the Owner's claims are
    not SAKU's to answer for), ``<details>`` thinking blocks are stripped
    (private reasoning is not a report), and previous audit sections are
    skipped so an audit quoting a claim cannot re-flag it the next day.
    """
    if not journal_text:
        return ""
    parts: list[str] = []
    # Split into headed blocks, keeping the heading with its block.
    blocks = _HEADING_RE.split("\n" + journal_text)
    headings = _HEADING_RE.findall("\n" + journal_text)
    for heading, block in zip(["", *headings], blocks):
        if _AUDIT_HEADING in heading:
            continue
        # Everything after a **SAKU** marker, up to the next **Owner**.
        for segment in block.split("**SAKU**")[1:]:
            parts.append(segment.split("**Owner**")[0])
    return _DETAILS_RE.sub("", "\n".join(parts))


def claimed_commits(text: str) -> list[tuple[str, str]]:
    """Return ``(ref, line)`` for commit claims in ``text``, in order.

    A commit claim is a line that carries a completion verb (or the word
    commit / コミット) together with a hash-like token.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not (
            _CLAIM_VERB_RE.search(line)
            or any(w in line for w in _COMMIT_CONTEXT_WORDS)
        ):
            continue
        for match in _COMMIT_REF_RE.finditer(line):
            ref = match.group(0)
            if ref not in seen:
                seen.add(ref)
                found.append((ref, line))
    return found


def extract_claims(journal_text: str, monologue_text: str) -> list[Claim]:
    """Extract the day's completion claims from SAKU's own words.

    Sources: SAKU blocks of the journal plus the whole monologue (the
    monologue is SAKU alone). A line that claims a write or a commit is
    that kind of claim; a completion line with neither a path nor a hash
    is a bare claim.
    """
    voice = saku_voice(journal_text)
    if monologue_text:
        voice = voice + "\n" + _DETAILS_RE.sub("", monologue_text)

    claims: list[Claim] = []
    write_paths = set(claimed_writes(voice))
    commit_refs = {ref for ref, _ in claimed_commits(voice)}

    for path in claimed_writes(voice):
        line = next(
            (ln.strip() for ln in voice.splitlines() if path in ln), ""
        )
        claims.append(Claim(KIND_WRITE, path, line))
    for ref, line in claimed_commits(voice):
        claims.append(Claim(KIND_COMMIT, ref, line))

    for raw in voice.splitlines():
        line = raw.strip()
        if not line or not _CLAIM_VERB_RE.search(line):
            continue
        if any(p in line for p in write_paths):
            continue
        if any(r in line for r in commit_refs):
            continue
        claims.append(Claim(KIND_BARE, "", line))
    return claims


# ── Verification ────────────────────────────────────────


def _git_answers(repo: Path) -> bool:
    """Whether ``repo`` is inside a git repository git can answer for."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-dir"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _commit_exists(repo: Path, ref: str) -> bool:
    """Whether ``ref`` names a commit in ``repo`` (repo must answer)."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{ref}^{{commit}}"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def verify_claims(
    claims: list[Claim], memory_root: Path, repo_roots: list[Path]
) -> list[Claim]:
    """Check each claim's evidence and set its status, in place."""
    answering = [r for r in dict.fromkeys(repo_roots) if _git_answers(r)]
    for claim in claims:
        if claim.kind == KIND_WRITE:
            target = memory_root / claim.evidence
            try:
                claim.status = (
                    STATUS_VERIFIED if target.exists() else STATUS_UNBACKED
                )
            except OSError:
                claim.status = STATUS_UNBACKED
        elif claim.kind == KIND_COMMIT:
            if not answering:
                claim.status = STATUS_UNCHECKED
            elif any(_commit_exists(r, claim.evidence) for r in answering):
                claim.status = STATUS_VERIFIED
            else:
                claim.status = STATUS_UNBACKED
        # Bare claims stay unchecked by construction: there is nothing
        # to check, which is exactly what the audit flags.
    return claims


def audit_texts(
    date_str: str,
    journal_text: str,
    monologue_text: str,
    memory_root: Path,
    repo_roots: list[Path],
) -> AuditResult:
    """Extract and verify one day's claims from already-loaded texts."""
    claims = extract_claims(journal_text, monologue_text)
    verify_claims(claims, memory_root, repo_roots)
    return AuditResult(date=date_str, claims=claims)


def audit_day(
    date_str: str, memory_root: Path, repo_roots: list[Path]
) -> AuditResult:
    """Audit one day, loading its journal / monologue from ``memory_root``."""
    def _load(sub: str) -> str:
        try:
            return (memory_root / sub / f"{date_str}.md").read_text(
                encoding="utf-8"
            )
        except OSError:
            return ""

    return audit_texts(
        date_str, _load("journal"), _load("monologue"), memory_root, repo_roots
    )


# ── Ledger (recurrence across days) ─────────────────────


def load_ledger(path: Path) -> dict:
    """Load the recurrence ledger; a missing/corrupt file starts empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("kinds"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"kinds": {}}


def record_day(ledger: dict, date_str: str, result: AuditResult) -> None:
    """Record one day's flagged claims in the ledger (in place)."""
    kinds = ledger.setdefault("kinds", {})
    per_kind: dict[str, int] = {}
    for claim in result.flagged:
        per_kind[claim.kind] = per_kind.get(claim.kind, 0) + 1
    for kind, count in per_kind.items():
        entry = kinds.setdefault(kind, {"days": [], "total": 0})
        if date_str not in entry["days"]:
            entry["days"].append(date_str)
            entry["days"].sort()
        entry["total"] = int(entry.get("total", 0)) + count


def recurring_kinds(ledger: dict, threshold: int) -> list[str]:
    """Kinds whose flagged claims span at least ``threshold`` distinct days."""
    return sorted(
        kind
        for kind, entry in ledger.get("kinds", {}).items()
        if len(entry.get("days", [])) >= max(1, threshold)
    )


def save_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def record_and_find_recurring(
    ledger_path: Path, date_str: str, result: AuditResult, threshold: int
) -> list[str]:
    """Update the ledger with one day and return the now-recurring kinds."""
    ledger = load_ledger(ledger_path)
    record_day(ledger, date_str, result)
    save_ledger(ledger_path, ledger)
    return recurring_kinds(ledger, threshold)


# ── Rendering / journal write-back ──────────────────────

_KIND_LABEL = {
    KIND_WRITE: "write",
    KIND_COMMIT: "commit",
    KIND_BARE: "bare",
}


def _short(line: str, limit: int = 120) -> str:
    return line if len(line) <= limit else line[: limit - 1] + "…"


def render_audit(result: AuditResult, recurring: list[str]) -> str:
    """Render the audit as a journal-ready Markdown section.

    Returns "" when the day held no completion claims at all, so quiet
    days add nothing to the journal.
    """
    if not result.claims:
        return ""
    lines = [
        f"**{_AUDIT_HEADING}（{result.date} の完了報告と証拠の照合）**",
        "",
        f"- 検証済み: {len(result.verified)}件",
        f"- 未確認: {len(result.flagged)}件",
    ]
    for claim in result.flagged:
        label = _KIND_LABEL.get(claim.kind, claim.kind)
        if claim.kind == KIND_BARE:
            lines.append(f"  - [{label}] 証拠の提示なし: 「{_short(claim.line)}」")
        else:
            why = (
                "ファイルが存在しない"
                if claim.kind == KIND_WRITE
                else "証拠のコミットが見つからない"
            )
            lines.append(
                f"  - [{label}] `{claim.evidence}` — {why}: 「{_short(claim.line)}」"
            )
    # Claims git could not answer for are recorded too (never silently
    # dropped), but they are not 未確認 and never feed the ledger.
    for claim in result.claims:
        if claim.kind == KIND_COMMIT and claim.status == STATUS_UNCHECKED:
            lines.append(
                f"  - [commit] `{claim.evidence}` — 照合できなかった"
                f"（git で確認できず）: 「{_short(claim.line)}」"
            )
    for kind in recurring:
        lines.append(
            f"- 再発: 「{kind}」種別の未確認が複数日に達した。"
            "教訓として principles への記録を検討すること。"
        )
    return "\n".join(lines)


def append_audit_to_journal(
    memory_root: Path, date_str: str, rendered: str
) -> None:
    """Append the audit section to the audited day's journal file.

    Same shape as ``core.save_autonomous_log``, but addressed to the
    audited date (not today) so each day's record carries its own audit.
    """
    journal_dir = memory_root / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / f"{date_str}.md"
    now = datetime.now().strftime("%H:%M")
    entry = f"\n## {now} [{_AUDIT_HEADING}]\n\n**SAKU**\n{rendered}\n"
    is_new = not journal_file.exists() or journal_file.stat().st_size == 0
    with journal_file.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Journal — {date_str}\n")
        f.write(entry)
