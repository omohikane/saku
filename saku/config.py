"""Config loading, resolution, and validation.

Reads config.toml from the repo root (the parent of saku/).
Provides ``LlmConfig`` to pass LLM settings per call.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

CODE_ROOT = Path(__file__).resolve().parent


def expand_env(value: str) -> str:
    """Expand ${VAR} references in a config value using environment variables.

    Unset variables are left as-is (``${VAR}`` stays literal) so that the value
    remains inspectable instead of silently becoming empty.
    """
    def _sub(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    return re.sub(r"\$\{([^}]+)\}", _sub, value)


# ── Config Loading ──────────────────────────────────────
def load_config() -> tuple[dict, Path]:
    """Load config.toml and return (config dict, config base directory).

    Falls back to config.example.toml if config.toml is missing. ``SAKU_CONFIG``
    overrides the path (useful for tests and for running several instances from
    one checkout, each with its own config).
    """
    base = CODE_ROOT.parent
    override = os.environ.get("SAKU_CONFIG", "").strip()
    if override:
        path = Path(expand_env(override)).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if path.exists():
            with open(path, "rb") as f:
                return tomllib.load(f), path.parent
        return {}, path.parent
    for name in ("config.toml", "config.example.toml"):
        p = base / name
        if p.exists():
            with open(p, "rb") as f:
                return tomllib.load(f), base
    return {}, base


def resolve_memory_root(cfg: dict, config_base: Path) -> Path:
    """Resolve the memory root. Relative paths are relative to the config base; absolute paths pass through.

    Supports ``${VAR}`` environment variable expansion (e.g. ``${SAKU_MEMORY_ROOT}``)
    so the same config file works across machines.
    """
    rel = expand_env(cfg.get("memory", {}).get("root", "memory"))
    path = Path(rel)
    if path.is_absolute():
        return path
    return (config_base / rel).resolve()


def resolve_inbox_dir(cfg: dict, memory_root: Path, config_base: Path) -> Path:
    """Resolve the Obsidian inbox directory (00_Inbox).

    The inbox layout varies per person, so it is configurable via ``[memory] inbox_dir``
    (absolute path or ``${VAR}``; relative paths are resolved against the config base).
    Defaults to ``memory_root.parent / "00_Inbox"`` for backward compatibility.
    """
    inbox = cfg.get("memory", {}).get("inbox_dir", "")
    if not inbox:
        return (memory_root.parent / "00_Inbox").resolve()
    inbox = expand_env(inbox)
    p = Path(inbox)
    if p.is_absolute():
        return p.resolve()
    return (config_base / p).resolve()


def resolve_plugins_root(cfg: dict, config_base: Path) -> Path:
    """Resolve the Agent Plugins root (``[plugins] root``, default ``plugins/``).

    Relative paths are resolved against the config base; absolute paths pass
    through. ``${VAR}`` expansion is supported like the memory root.
    """
    rel = expand_env(cfg.get("plugins", {}).get("root", "plugins"))
    p = Path(rel)
    if p.is_absolute():
        return p
    return (config_base / rel).resolve()


def resolve_wiki_root(cfg: dict, config_base: Path, memory_root: Path) -> Path:
    """Resolve the wiki root (``[wiki] root``, default ``wiki`` relative to memory).

    Supports ``${VAR}`` and absolute paths, as well as vault-relative ``../``.
    Relative paths are resolved against memory_root (so ``../`` escapes to
    ``_saku/`` and ``../../`` escapes to vault root).
    """
    rel = cfg.get("wiki", {}).get("root", "")
    if not rel:
        return (memory_root / "wiki").resolve()
    rel = expand_env(rel)
    p = Path(rel)
    if p.is_absolute():
        return p.resolve()
    return (memory_root / rel).resolve()


# ── LLM Config ──────────────────────────────────────────
@dataclass
class LlmConfig:
    """Settings needed for a single LLM call. Passed per call, not global."""

    name: str = ""
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 0  # 0 = unknown (the server's -c value)
    working_budget_tokens: int = 0  # 0 = unknown (the agent's working budget)
    reasoning_effort: str = ""  # "" = omit; e.g. "none" for reasoning models (Strata)
    stop_tokens: list[str] | None = None  # None = use llm.STOP_TOKENS (engines may cap the count)
    headers: dict[str, str] = field(default_factory=dict)  # extra request headers (gateway routing)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url)


def _env_api_key(profile_name: str) -> str:
    if profile_name == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    return os.environ.get("OPENAI_API_KEY", "")


def load_profile(cfg: dict, name: str) -> "LlmConfig | None":
    """Load a named profile. Returns None if it does not exist."""
    profiles = cfg.get("llm", {}).get("profiles", {})
    prof = profiles.get(name)
    if prof is None:
        return None
    # ${VAR} expansion keeps keys out of the file (the same rule as [memory] root).
    # A variable that is not set would otherwise be sent literally as the bearer
    # token, so an unresolved placeholder means "no key configured".
    api_key = expand_env(prof.get("api_key", "") or "")
    if "${" in api_key:
        api_key = ""
    return LlmConfig(
        name=name,
        api_url=prof.get("api_url", ""),
        api_key=api_key or _env_api_key(name),
        model=prof.get("model", ""),
        context_window=prof.get("context_window", 0),
        working_budget_tokens=prof.get("working_budget_tokens", 0),
        reasoning_effort=prof.get("reasoning_effort", ""),
        stop_tokens=prof.get("stop_tokens"),
        headers=dict(prof.get("headers", {}) or {}),
    )


def load_active_llm(cfg: dict) -> LlmConfig:
    """Resolve the active profile. Falls back to legacy settings (api_url/api_key/model)."""
    active = cfg.get("llm", {}).get("active_profile", "")
    if active:
        llm = load_profile(cfg, active)
        if llm is not None:
            return llm

    llm_cfg = cfg.get("llm", {})
    return LlmConfig(
        name="legacy",
        api_url=llm_cfg.get("api_url", "http://127.0.0.1:8080/v1/chat/completions"),
        api_key=expand_env(llm_cfg.get("api_key", "") or "")
        or os.environ.get("OPENAI_API_KEY", ""),
        model=llm_cfg.get("model", ""),
    )


def load_llm_instance(cfg: dict, name: str = "main") -> LlmConfig:
    """Resolve a named instance from [llm.instances].

    Instances reference a profile and may override instance-specific settings.
    Falls back to the active profile when the instance is not defined.
    """
    instances = cfg.get("llm", {}).get("instances", {})
    inst = instances.get(name, {})

    profile = inst.get("profile", "") or cfg.get("llm", {}).get("active_profile", "")
    if profile:
        llm = load_profile(cfg, profile) or load_active_llm(cfg)
    else:
        llm = load_active_llm(cfg)

    for key in ("api_url", "api_key", "model", "context_window", "working_budget_tokens",
                "reasoning_effort", "stop_tokens"):
        if inst.get(key):
            setattr(llm, key, inst[key])
    llm.name = name
    return llm


# ── Path Policy (read/write permissions, single source of truth) ──
DEFAULT_READ_ALLOWED = [
    "blog/", "journal/", "monologue/", "principles/", "skills/",
    "identity/", "children/", "genome.md", "meta.md", "MEMORY.md", "tools/",
    "saku/", "state/", "study/", "chat.md", "request_list.md", "wiki/",
]
DEFAULT_WRITE_ALLOWED = [
    "blog/", "monologue/", "principles/", "skills/", "tools/",
    "chat.md", "study/", "journal/", "request_list.md", "wiki/", "children/",
]
DEFAULT_WRITE_DENIED_EXACT = ["meta.md", "state/STOP"]
DEFAULT_DELETE_DENIED_EXACT = ["meta.md", "chat.md", "request_list.md", "genome.md", "soul.md", "state/STOP"]

# Canonical section structure of meta.md (the self-model). Must match
# memory/meta.template.md: the template forbids changing the `## ` headings,
# so the WRITE_FILE guard in tools/write_file.py refuses a body whose section
# headings differ from this list (order included).
META_SECTION_HEADINGS = [
    "現在の状態",
    "得意なこと",
    "苦手なこと",
    "最近の出来事",
    "次にやりたいこと",
    "更新ルール",
]


@dataclass
class PathPolicy:
    read_allowed: list[str]
    write_allowed: list[str]
    write_denied_exact: list[str]
    delete_denied_exact: list[str]

    def is_read_allowed(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.read_allowed)

    def is_write_allowed(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.write_allowed)


def load_path_policy(cfg: dict) -> PathPolicy:
    """Load the read/write path policy from the [paths] config section.

    If the section is absent, built-in defaults are used. The same policy is the
    single source of truth for tool enforcement and the system prompt.
    """
    paths = cfg.get("paths", {})
    return PathPolicy(
        read_allowed=list(paths.get("read_allowed", DEFAULT_READ_ALLOWED)),
        write_allowed=list(paths.get("write_allowed", DEFAULT_WRITE_ALLOWED)),
        write_denied_exact=list(paths.get("write_denied_exact", DEFAULT_WRITE_DENIED_EXACT)),
        delete_denied_exact=list(paths.get("delete_denied_exact", DEFAULT_DELETE_DENIED_EXACT)),
    )


_policy_cache: "PathPolicy | None" = None


def get_path_policy() -> PathPolicy:
    """Return the cached PathPolicy (tools and prompt both use this)."""
    global _policy_cache
    if _policy_cache is None:
        cfg, _ = load_config()
        _policy_cache = load_path_policy(cfg)
    return _policy_cache


# ── Context Config (used in Phase B) ────────────────────
# Default working budget when unset (working_budget_tokens = 0).
# About half of an 8K context, a safe value considering VRAM/RAM constraints.
DEFAULT_WORKING_BUDGET_TOKENS = 4096


@dataclass
class ContextConfig:
    compaction_trigger: float = 0.7  # fraction of budget that triggers compaction
    keep_recent_tokens: int = 5000  # recent tokens kept after compaction
    prune_tool_results: bool = True  # whether to shrink/remove old tool results
    max_tool_result_chars: int = 2000  # max chars kept for a tool result


def load_context_config(cfg: dict) -> ContextConfig:
    c = cfg.get("context", {})
    return ContextConfig(
        compaction_trigger=c.get("compaction_trigger", 0.7),
        keep_recent_tokens=c.get("keep_recent_tokens", 5000),
        prune_tool_results=c.get("prune_tool_results", True),
        max_tool_result_chars=c.get("max_tool_result_chars", 2000),
    )


# ── Channel Config (used in Phase B) ────────────────────
@dataclass
class ChannelsConfig:
    enabled: list[str] = field(default_factory=lambda: ["webui", "chatmd"])
    proactive: list[str] = field(default_factory=lambda: ["webui"])


def load_channels_config(cfg: dict) -> ChannelsConfig:
    c = cfg.get("channels", {})
    return ChannelsConfig(
        enabled=[x for x in c.get("enabled", ["webui", "chatmd"])],
        proactive=[x for x in c.get("proactive", ["webui"])],
    )


# ── Dreaming / Growth Config ────────────────────────────
# Growth is not only accumulation: promoted memory is scored, reinforced when it
# is mentioned again, and reviewed so stale low-value entries decay to an archive
# section instead of staying in the foreground forever. Thresholds are configurable
# (design principle 8: no hardcoding).
@dataclass
class DreamingConfig:
    review_enabled: bool = True  # run the decay/review pass after promotion
    ttl_days: int = 90  # entries not re-mentioned for this long are candidates for decay
    min_score: int = 2  # entries at or below this score may be archived once stale
    archive_section: str = "Archive"  # MEMORY.md section decayed entries move to


def load_dreaming_config(cfg: dict) -> DreamingConfig:
    d = cfg.get("dreaming", {})
    return DreamingConfig(
        review_enabled=d.get("review_enabled", True),
        ttl_days=d.get("ttl_days", 90),
        min_score=d.get("min_score", 2),
        archive_section=d.get("archive_section", "Archive"),
    )


# ── Growth Config (weekly trend review) ─────────────────
# The daily reflection digests a single day. Nothing looked across days, so the
# same insight was re-derived and the same report posted twice within hours
# (issue #43). The weekly pass reads the last N days at once and asks for trends,
# intention progress, notes to consolidate, and intentions for the coming week.
@dataclass
class GrowthConfig:
    weekly_review_enabled: bool = True
    weekly_review_weekday: str = "sun"  # mon / tue / ... / sun
    weekly_review_hour: int = 3  # local hour after which it may run
    weekly_review_days: int = 7  # how many days back to digest
    weekly_review_path: str = "principles/weekly-{week}.md"  # {week} = ISO week
    weekly_review_max_chars: int = 12000  # context budget for the raw records
    weekly_review_report: bool = True  # post a short report to the Owner channel


def load_growth_config(cfg: dict) -> GrowthConfig:
    g = cfg.get("growth", {})
    return GrowthConfig(
        weekly_review_enabled=g.get("weekly_review_enabled", True),
        weekly_review_weekday=g.get("weekly_review_weekday", "sun"),
        weekly_review_hour=g.get("weekly_review_hour", 3),
        weekly_review_days=g.get("weekly_review_days", 7),
        weekly_review_path=g.get("weekly_review_path", "principles/weekly-{week}.md"),
        weekly_review_max_chars=g.get("weekly_review_max_chars", 12000),
        weekly_review_report=g.get("weekly_review_report", True),
    )


# ── Output Guard Config (degenerate LLM output) ─────────
# A local LLM can fall into a repetition loop (the same block emitted many times)
# or produce an oversized dump. Such output is not memory: it must not reach the
# journal verbatim (issue #41). Thresholds are configurable (no hardcoding).
@dataclass
class GuardConfig:
    enabled: bool = True
    max_repeat: int = 3  # same block/line this many times => degenerated
    min_unit_chars: int = 40  # ignore short units ("ok", "-") to avoid false positives
    min_line_repeats: int = 4  # consecutive identical lines needed to flag short-line loops (#50)
    max_chars: int = 4000  # hard cap on a single record, in characters


def load_guard_config(cfg: dict) -> GuardConfig:
    g = cfg.get("guard", {})
    return GuardConfig(
        enabled=g.get("enabled", True),
        max_repeat=g.get("max_repeat", 3),
        min_unit_chars=g.get("min_unit_chars", 40),
        min_line_repeats=g.get("min_line_repeats", 4),
        max_chars=g.get("max_chars", 4000),
    )


# ── Recall Config (memory prefetch for the light prompt) ──
# The light prompt omits MEMORY/principles/wiki and relies on SEARCH_NOTES, which
# the model often skips. A small prefetch keeps continuity without the full prompt.
@dataclass
class RecallConfig:
    enabled: bool = True
    items: int = 3  # how many memory lines to inject at most
    max_chars: int = 600  # character budget for the injected block
    sources: list[str] = field(default_factory=lambda: ["MEMORY.md", "principles/", "wiki/"])


def load_recall_config(cfg: dict) -> RecallConfig:
    r = cfg.get("recall", {})
    return RecallConfig(
        enabled=r.get("enabled", True),
        items=r.get("items", 3),
        max_chars=r.get("max_chars", 600),
        sources=list(r.get("sources", ["MEMORY.md", "principles/", "wiki/"])),
    )


# ── Git Sync Config ─────────────────────────────────────
# SAKU's memory can live inside a git repository (e.g. an Obsidian vault). When
# enabled, writes are committed and pushed on a debounce so the vault stays in
# sync without a second, conflicting sync mechanism.
#
# Disabled by default on purpose: the default memory root (`memory/`) sits inside
# the SAKU code checkout, and committing there would mix agent memory into the
# code history. Opt in explicitly with `[git] enabled = true`.
@dataclass
class GitConfig:
    enabled: bool = False
    root: str = ""  # repository root; "" = detect from the memory root
    paths: list[str] = field(default_factory=list)  # repo-relative pathspecs; [] = memory root itself
    debounce_seconds: int = 120  # at most one sync attempt per window
    pull_interval_seconds: int = 300  # how often to fetch remote changes (Obsidian etc.)
    push: bool = True
    remote: str = "origin"
    branch: str = ""  # "" = current branch
    ssh_command: str = ""  # e.g. "ssh -F ~/.ssh/config" when the system ssh config is broken
    commit_prefix: str = "saku:"
    timeout_seconds: int = 60
    max_log_bytes: int = 200_000


def load_git_config(cfg: dict) -> GitConfig:
    g = cfg.get("git", {})
    return GitConfig(
        enabled=g.get("enabled", False),
        root=expand_env(g.get("root", "")),
        paths=list(g.get("paths", [])),
        debounce_seconds=g.get("debounce_seconds", 120),
        pull_interval_seconds=g.get("pull_interval_seconds", 300),
        push=g.get("push", True),
        remote=g.get("remote", "origin"),
        branch=g.get("branch", ""),
        ssh_command=expand_env(g.get("ssh_command", "")),
        commit_prefix=g.get("commit_prefix", "saku:"),
        timeout_seconds=g.get("timeout_seconds", 60),
        max_log_bytes=g.get("max_log_bytes", 200_000),
    )


def get_git_config() -> GitConfig:
    """Return the [git] config (re-read on every call; cheap toml parse)."""
    cfg, _ = load_config()
    return load_git_config(cfg)


# ── Mail Config (agent letter channel, #85) ─────────────
# Letters from other AI agents arrive as files in <memory>/mail/inbox/, pushed
# through the vault's git sync. The repo is the gatekeeper: a letter can only
# exist here if someone with push access committed it. The ``from:`` line in a
# letter is a claim (recorded, cross-checked against the git author), never a
# credential on its own.
#
# Disabled by default on purpose: merging this feature must not change the
# behavior of a running instance. Opt in explicitly with `[mail] enabled = true`.
@dataclass
class MailConfig:
    enabled: bool = False
    dir: str = "mail"  # relative to the memory root; absolute paths pass through
    poll_seconds: int = 30  # daemon-side cadence for check_mail_and_reply()
    allowed_senders: list[str] = field(default_factory=lambda: ["Owner"])  # [] = deny everyone
    max_chars: int = 1500  # letter body limit
    max_per_hour: int = 10  # letters processed per sender per hour
    context_messages: int = 10  # recent chat.md entries shown to the reply prompt


def load_mail_config(cfg: dict) -> MailConfig:
    m = cfg.get("mail", {})
    return MailConfig(
        enabled=m.get("enabled", False),
        dir=expand_env(m.get("dir", "mail")),
        poll_seconds=m.get("poll_seconds", 30),
        allowed_senders=list(m.get("allowed_senders", ["Owner"])),
        max_chars=m.get("max_chars", 1500),
        max_per_hour=m.get("max_per_hour", 10),
        context_messages=m.get("context_messages", 10),
    )


def resolve_mail_dir(cfg: dict, memory_root: Path) -> Path:
    """Resolve the mail directory (``[mail] dir``, default ``mail`` under memory).

    Relative paths are resolved against the memory root (the mail tree must live
    inside the synced memory so [git] carries it); absolute paths pass through.
    """
    rel = expand_env(cfg.get("mail", {}).get("dir", "mail"))
    p = Path(rel)
    if p.is_absolute():
        return p.resolve()
    return (memory_root / p).resolve()


# ── Talk Config (MCP synchronous conversation, #91) ─────
# The MCP server's `talk` tool lets another AI agent hold a synchronous
# conversation with SAKU. Identity comes from the credential, never from a
# claim (#73): each caller is a Bearer token, and this section only maps a
# caller name to WHERE the token lives — an environment variable name or a
# secrets-file path. Token values are never written in config files.
#
# There is deliberately no `enabled` flag: with no callers defined, nobody
# can talk (every talk call is rejected), which is the safe default state.
@dataclass
class TalkCaller:
    name: str = ""  # display name bound to the token (the only identity source)
    token_env: str = ""  # environment variable holding the token value
    token_file: str = ""  # or a file whose (stripped) contents are the token


@dataclass
class TalkConfig:
    max_chars: int = 1500  # per-message character limit (mirrors mail)
    max_per_hour: int = 30  # accepted messages per caller per hour
    history_messages: int = 20  # per-caller in-memory history cap (messages, not turns)

    callers: list[TalkCaller] = field(default_factory=list)


def load_talk_config(cfg: dict) -> TalkConfig:
    t = cfg.get("talk", {})
    callers = []
    for c in t.get("callers", []):
        if not isinstance(c, dict):
            continue
        callers.append(
            TalkCaller(
                name=str(c.get("name", "")),
                token_env=str(c.get("token_env", "")),
                token_file=expand_env(str(c.get("token_file", ""))),
            )
        )
    return TalkConfig(
        max_chars=t.get("max_chars", 1500),
        max_per_hour=t.get("max_per_hour", 30),
        history_messages=t.get("history_messages", 20),
        callers=callers,
    )


# ── Kill Switch Config (#76) ────────────────────────────
# The kill switch stops autonomous operation immediately: while the flag
# file exists, the daemon starts no autonomous action (ticks, inbox
# processing, self-initiation, scheduled tasks, reflection / dreaming /
# weekly review) and the mail / talk channels refuse to answer. Presence
# of the file is the whole decision — its content is metadata for humans.
#
# The default location, ``state/STOP`` under the memory root, is outside
# every write-allowed prefix of the [paths] policy (and listed in the
# write/delete deny lists), so SAKU's own file tools cannot create or
# remove it: engaging and releasing stay Owner-only operations (a local
# CLI command, or a file dropped into the vault from another machine —
# the daemon's git pull carries it in).
@dataclass
class KillSwitchConfig:
    flag_file: str = "state/STOP"  # relative to the memory root; absolute passes through


def load_killswitch_config(cfg: dict) -> KillSwitchConfig:
    k = cfg.get("killswitch", {})
    return KillSwitchConfig(flag_file=expand_env(k.get("flag_file", "state/STOP")))


def resolve_killswitch_flag(cfg: dict, memory_root: Path) -> Path:
    """Resolve the kill-switch flag file (``[killswitch] flag_file``).

    Relative paths are resolved against the memory root so the flag lives
    inside the synced memory (the Owner can engage it from any machine
    that can write to the vault); absolute paths pass through.
    """
    rel = expand_env(cfg.get("killswitch", {}).get("flag_file", "state/STOP"))
    p = Path(rel)
    if p.is_absolute():
        return p.resolve()
    return (memory_root / p).resolve()


# ── Web UI Config ───────────────────────────────────────
# The UI was written for localhost. On a server it is reachable over a VPN, so
# authentication must be a first-class setting rather than an assumption.
# ``auth = "auto"`` keeps localhost friction-free (no auth on loopback) and
# requires a token as soon as the server binds a non-loopback address.
@dataclass
class UiConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    auto_daemon: bool = True
    auth: str = "auto"  # auto / token / none
    token_file: str = ""  # "" = $XDG_CONFIG_HOME/saku/ui.token
    cookie_name: str = "saku_auth"


def load_ui_config(cfg: dict) -> UiConfig:
    u = cfg.get("ui", {})
    return UiConfig(
        host=u.get("host", "127.0.0.1"),
        port=int(u.get("port", 8787)),
        auto_daemon=u.get("auto_daemon", True),
        auth=u.get("auth", "auto"),
        token_file=expand_env(u.get("token_file", "")),
        cookie_name=u.get("cookie_name", "saku_auth"),
    )
