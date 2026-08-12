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

    Falls back to config.example.toml if config.toml is missing.
    """
    base = CODE_ROOT.parent
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
    return LlmConfig(
        name=name,
        api_url=prof.get("api_url", ""),
        api_key=prof.get("api_key") or _env_api_key(name),
        model=prof.get("model", ""),
        context_window=prof.get("context_window", 0),
        working_budget_tokens=prof.get("working_budget_tokens", 0),
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
        api_key=llm_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
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

    for key in ("api_url", "api_key", "model", "context_window", "working_budget_tokens"):
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
DEFAULT_WRITE_DENIED_EXACT = ["meta.md"]
DEFAULT_DELETE_DENIED_EXACT = ["meta.md", "chat.md", "request_list.md", "genome.md", "soul.md"]


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
