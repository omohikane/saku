"""Editable identity files: soul / genome / persona (#73 follow-up).

These three Markdown files are the part of SAKU its Owner is meant to edit:
``soul`` (core values), ``genome`` (who it is) and ``persona`` (how it
speaks). They used to be resolved ad hoc — genome had a three-step fallback,
soul was hard-wired to ``identity/soul.md``, persona was new — which made an
OSS fork hard to understand and impossible to edit from the Web UI.

Now one place resolves them, one place validates a write, and the Web UI can
edit exactly these files and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from saku.config import expand_env

NAMES = ("soul", "genome", "persona")
MAX_CHARS = 60000

DEFAULTS = {
    "soul": "identity/soul.md",
    "genome": "identity/genome.md",
    "persona": "identity/persona.md",
}


@dataclass
class IdentityFiles:
    soul: Path
    genome: Path
    persona: Path

    def as_dict(self) -> dict[str, Path]:
        return {"soul": self.soul, "genome": self.genome, "persona": self.persona}


def _resolve(memory_root: Path, value: str, name: str) -> Path:
    raw = expand_env(value or DEFAULTS[name]).strip()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return Path(memory_root) / path


def resolve(cfg: dict, memory_root: Path) -> IdentityFiles:
    """Resolve the three files from config.

    ``[identity] soul / genome`` and ``[persona] file`` are memory-relative
    (absolute and ``${VAR}`` paths are honoured). The old genome fallbacks
    (vault root, repo sample) still apply when the configured file is absent,
    so an existing installation keeps working after an upgrade.
    """
    identity_cfg = cfg.get("identity", {}) or {}
    persona_cfg = cfg.get("persona", {}) or {}
    soul = _resolve(memory_root, str(identity_cfg.get("soul", DEFAULTS["soul"])), "soul")
    genome = _resolve(memory_root, str(identity_cfg.get("genome", DEFAULTS["genome"])), "genome")
    persona = _resolve(memory_root, str(persona_cfg.get("file", DEFAULTS["persona"])), "persona")

    if not genome.exists():
        for candidate in (
            Path(memory_root).parent / "identity" / "genome.md",  # <vault>/_saku/identity
            Path(memory_root).parent.parent / "identity" / "genome.md",  # <vault>/identity
            Path(__file__).resolve().parent.parent / "identity" / "genome.md",  # repo sample
        ):
            if candidate.exists():
                genome = candidate
                break
    return IdentityFiles(soul=soul, genome=genome, persona=persona)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def validate(name: str, content: str) -> str | None:
    """Return an error message, or None when the write is acceptable."""
    if name not in NAMES:
        return f"unknown identity file: {name}"
    if not isinstance(content, str):
        return "content must be text"
    if len(content) > MAX_CHARS:
        return f"content is too large ({len(content)} > {MAX_CHARS} chars)"
    return None


def write(path: Path, content: str) -> None:
    """Write an identity file, atomically, keeping one .bak of the previous.

    **Owner-only.** This module is for the Web UI editor and the CLI; SAKU's
    tool layer must never reach it (the identity files are on the write and
    delete deny lists, so WRITE_FILE / APPEND_FILE / MOVE_FILE / DELETE_FILE
    refuse them even if ``[paths] write_allowed`` is widened).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
