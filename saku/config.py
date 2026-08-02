"""設定の読み込み・解決・バリデーション。

config.toml はリポジトリルート（saku/ の親）から読み込む。
LLM設定は per-call で渡すための ``LlmConfig`` を提供する。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

CODE_ROOT = Path(__file__).resolve().parent


# ── 設定読み込み ─────────────────────────────────────────
def load_config() -> tuple[dict, Path]:
    """config.toml を読み込み、 (設定dict, 設定ベースディレクトリ) を返す。

    config.toml が無ければ config.example.toml にフォールバックする。
    """
    base = CODE_ROOT.parent
    for name in ("config.toml", "config.example.toml"):
        p = base / name
        if p.exists():
            with open(p, "rb") as f:
                return tomllib.load(f), base
    return {}, base


def resolve_memory_root(cfg: dict, config_base: Path) -> Path:
    """memory root を解決する。相対パスは設定ベース基準、絶対パスはそのまま。"""
    rel = cfg.get("memory", {}).get("root", "memory")
    path = Path(rel)
    if path.is_absolute():
        return path
    return (config_base / rel).resolve()


# ── LLM 設定 ────────────────────────────────────────────
@dataclass
class LlmConfig:
    """1回のLLM呼び出しに必要な設定。グローバルではなく per-call で渡す。"""

    name: str = ""
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    context_window: int = 0  # 0 = 不明（サーバの -c 値）
    working_budget_tokens: int = 0  # 0 = 不明（エージェントの作業予算）

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url)


def _env_api_key(profile_name: str) -> str:
    if profile_name == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "")
    return os.environ.get("OPENAI_API_KEY", "")


def load_profile(cfg: dict, name: str) -> "LlmConfig | None":
    """名前付きプロファイルを読み込む。存在しなければ None。"""
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
    """active_profile を解決する。無ければレガシー設定（api_url/api_key/model）にフォールバック。"""
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
    """[llm.instances] の名前付きインスタンスを解決する。

    インスタンスはプロファイルを参照し、インスタンス固有の上書きを持てる。
    インスタンス定義が無ければ active_profile にフォールバックする。
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


# ── コンテキスト設定（Phase B で使用）──────────────────────
# 作業予算が未設定（working_budget_tokens = 0）のときの既定値。
# 8K コンテキスト前提で約半分、と VRAM/RAM 制約を踏まえた安全側の値。
DEFAULT_WORKING_BUDGET_TOKENS = 4096


@dataclass
class ContextConfig:
    compaction_trigger: float = 0.7  # 予算の何割で自動コンパクションするか
    keep_recent_tokens: int = 2000  # コンパクション後に保持する直近トークン数
    prune_tool_results: bool = True  # 古いツール結果を縮小/削除するか
    max_tool_result_chars: int = 2000  # ツール結果を残す最大文字数


def load_context_config(cfg: dict) -> ContextConfig:
    c = cfg.get("context", {})
    return ContextConfig(
        compaction_trigger=c.get("compaction_trigger", 0.7),
        keep_recent_tokens=c.get("keep_recent_tokens", 2000),
        prune_tool_results=c.get("prune_tool_results", True),
        max_tool_result_chars=c.get("max_tool_result_chars", 2000),
    )


# ── チャネル設定（Phase B で使用）────────────────────────
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
