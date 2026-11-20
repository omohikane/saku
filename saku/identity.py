"""Who is speaking, and what they may do (#73).

The channel implementations grew their own identity rules: talk binds a Bearer
token to a caller name, mail trusts a self-declared `from`, the Web UI has the
Owner token, and the daemon acts as SAKU itself. This module is the shared
vocabulary so those channels agree on two separate things:

- **naming** — which principal is acting (recorded in memory and logs);
- **permission** — which scopes that principal holds, decided by a token, never
  by a name. "I am the Owner" in a request body grants nothing.

Principals
----------
``owner``   the human, authenticated by the UI token. Holds every scope.
``saku``    the resident daemon acting on its own (system principal).
``<agent>`` another agent, resolved from ``[identity.agents.*]`` or the
            existing ``[talk] callers`` (which become talk-scoped agents).
``unknown`` no credential matched: no scopes, nothing may be done.

Scopes
------
``talk`` / ``mail`` / ``read`` / ``request`` are grantable. ``approve`` and
``write`` belong to the Owner (or SAKU's own system principal); an agent config
cannot grant them. Unknown scopes are ignored.

The audit log is append-only JSONL under ``state/audit.log``: who, when,
through which channel, what was attempted, and whether it was allowed. It is a
record, never a gate — a logging failure must not change an authorization
outcome.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

KIND_OWNER = "owner"
KIND_SYSTEM = "system"
KIND_AGENT = "agent"
KIND_UNKNOWN = "unknown"

SCOPE_TALK = "talk"
SCOPE_MAIL = "mail"
SCOPE_READ = "read"
SCOPE_REQUEST = "request"  # may ask; may never approve
SCOPE_WRITE = "write"
SCOPE_APPROVE = "approve"

GRANTABLE_SCOPES = frozenset(
    {SCOPE_TALK, SCOPE_MAIL, SCOPE_READ, SCOPE_REQUEST, SCOPE_WRITE, SCOPE_APPROVE}
)
AGENT_DEFAULT_SCOPES = frozenset({SCOPE_TALK})


@dataclass(frozen=True)
class Principal:
    name: str
    kind: str = KIND_AGENT
    scopes: frozenset[str] = field(default_factory=frozenset)
    source: str = ""

    def can(self, scope: str) -> bool:
        if self.kind == KIND_OWNER:
            return True
        return scope in self.scopes

    @property
    def is_owner(self) -> bool:
        return self.kind == KIND_OWNER

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "scopes": sorted(self.scopes),
            "source": self.source,
        }


OWNER = Principal("owner", KIND_OWNER, frozenset(GRANTABLE_SCOPES), "ui-token")
SYSTEM = Principal(
    "saku",
    KIND_SYSTEM,
    frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_REQUEST, SCOPE_TALK, SCOPE_MAIL}),
    "daemon",
)
UNKNOWN = Principal("unknown", KIND_UNKNOWN, frozenset(), "none")


_current: contextvars.ContextVar[Principal] = contextvars.ContextVar(
    "saku_principal", default=UNKNOWN
)


def current() -> Principal:
    """The principal for the work in progress (OWNER/SYSTEM/UNKNOWN by default)."""
    return _current.get()


@contextlib.contextmanager
def as_principal(principal: Principal):
    token = _current.set(principal)
    try:
        yield principal
    finally:
        _current.reset(token)


# ── Token-based resolution ──────────────────────────────
def _equal(a: str, b: str) -> bool:
    """Length-independent comparison; a missing value never matches."""
    if not a or not b:
        return False
    return (
        len(a) == len(b)
        and sum(ord(x) ^ ord(y) for x, y in zip(a, b)) == 0
    )


def _read_token(spec: dict) -> str:
    env = str(spec.get("token_env", "") or "")
    if env:
        return os.environ.get(env, "").strip()
    path = str(spec.get("token_file", "") or "")
    if path:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def _scopes(spec: dict, default: frozenset[str]) -> frozenset[str]:
    raw = spec.get("scopes")
    if not isinstance(raw, list):
        return default
    return frozenset(str(s) for s in raw if str(s) in GRANTABLE_SCOPES)


def agent_specs(cfg: dict) -> dict[str, dict]:
    """Agent definitions, merging ``[identity.agents.*]`` with ``[talk] callers``.

    A talk caller is an agent that may converse; an identity entry with the same
    name overrides its scopes and token source. This keeps the existing talk
    configuration working while giving it one place to grow.
    """
    agents: dict[str, dict] = {}
    for caller in cfg.get("talk", {}).get("callers", []):
        if not isinstance(caller, dict):
            continue
        name = str(caller.get("name", "")).strip()
        if not name:
            continue
        agents[name] = {
            "token_env": caller.get("token_env", ""),
            "token_file": caller.get("token_file", ""),
            "scopes": [SCOPE_TALK],
        }
    for name, spec in (cfg.get("identity", {}).get("agents", {}) or {}).items():
        if not isinstance(spec, dict):
            continue
        merged = dict(agents.get(str(name), {}))
        merged.update(spec)
        agents[str(name)] = merged
    return agents


def resolve_token(
    presented: str,
    cfg: dict,
    *,
    reserved: "tuple[str, ...]" = (),
) -> Principal | None:
    """Resolve a presented token to an agent principal, or None.

    The operator token (``[mcp.server]``) is reserved: it is not an agent
    identity even if someone configures it as one. Every entry is compared
    (no early exit) so timing does not reveal which one matched.
    """
    if not presented:
        return None
    if any(_equal(presented, r) for r in reserved if r):
        return None
    match: Principal | None = None
    for name, spec in agent_specs(cfg).items():
        token = _read_token(spec)
        if _equal(presented, token):
            match = Principal(
                name=name,
                kind=KIND_AGENT,
                scopes=_scopes(spec, AGENT_DEFAULT_SCOPES),
                source="token",
            )
    return match


def principal_for_agent(cfg: dict, name: str) -> Principal | None:
    """The principal a configured agent name maps to (scopes from config)."""
    spec = agent_specs(cfg).get(name)
    if spec is None:
        return None
    return Principal(
        name=name,
        kind=KIND_AGENT,
        scopes=_scopes(spec, AGENT_DEFAULT_SCOPES),
        source="config",
    )


# ── Audit ───────────────────────────────────────────────
def audit_path(memory_root: Path, cfg: dict | None = None) -> Path:
    # Tests set SAKU_AUDIT_FILE so a run never writes into the Owner's log.
    override = os.environ.get("SAKU_AUDIT_FILE", "")
    if override:
        return Path(override)
    rel = str((cfg or {}).get("audit", {}).get("file", "state/audit.log"))
    path = Path(rel).expanduser()
    return path if path.is_absolute() else Path(memory_root) / path


def audit(
    memory_root: Path,
    principal: Principal,
    action: str,
    *,
    channel: str = "",
    allowed: bool = True,
    detail: str = "",
    cfg: dict | None = None,
    now: float | None = None,
) -> None:
    """Append one decision to the audit log. Never raises."""
    path = audit_path(memory_root, cfg)
    now = time.time() if now is None else now
    record = {
        "ts": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "principal": principal.name,
        "kind": principal.kind,
        "scopes": sorted(principal.scopes),
        "channel": channel,
        "action": action,
        "allowed": bool(allowed),
        "detail": (detail or "")[:500],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # A record is not a gate.
        pass


def tail(memory_root: Path, limit: int = 50, cfg: dict | None = None) -> list[dict]:
    path = audit_path(memory_root, cfg)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ── CLI ─────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    import argparse

    from saku import config as cfg_mod

    parser = argparse.ArgumentParser(
        prog="saku audit", description="Show the append-only identity/action log"
    )
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg, base = cfg_mod.load_config()
    memory_root = cfg_mod.resolve_memory_root(cfg, base)
    records = tail(memory_root, args.tail, cfg)
    if not records:
        print("(audit log is empty)")
        return 0
    for r in records:
        if args.json:
            print(json.dumps(r, ensure_ascii=False))
            continue
        verdict = "allow" if r.get("allowed") else "DENY"
        print(
            f"{r.get('ts', '')} [{verdict}] {r.get('principal', '?')}"
            f" ({r.get('kind', '?')}) {r.get('channel', '')}/{r.get('action', '')}"
            + (f"  {r.get('detail', '')}" if r.get("detail") else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
