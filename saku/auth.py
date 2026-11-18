"""Web UI authentication (single-user bearer token).

The UI started life on localhost, where "no auth" is acceptable. On a server it is
reachable over a VPN, and the chat endpoint can run tools — file writes, code
execution — so reachability must not equal authority.

Design:

* **One persistent token**, not a random one per start. A token that changes on
  every restart forces the Owner to copy it again; that friction is what makes
  people disable auth entirely. The token is generated once at bootstrap and
  stored in ``$XDG_CONFIG_HOME/saku/ui.token`` (0600).
* **Cookie after one login**, so the browser only needs the token once.
* **Constant-time comparison** to avoid turning the token into an oracle.
* **Loopback may opt out** (``auth = "auto"``): local development keeps the old
  friction-free behaviour, while binding any non-loopback address requires a token.

The token value is never printed by this module; callers should refer to the file.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from .config import UiConfig

TOKEN_ENV = "SAKU_UI_TOKEN"
_TOKEN_BYTES = 32


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "saku"


def default_token_file() -> Path:
    return config_dir() / "ui.token"


def token_file(ui_cfg: UiConfig) -> Path:
    if ui_cfg.token_file:
        return Path(os.path.expanduser(ui_cfg.token_file))
    return default_token_file()


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def auth_mode(ui_cfg: UiConfig) -> str:
    """Resolve ``auth`` to "token" or "none"."""
    mode = (ui_cfg.auth or "auto").lower()
    if mode in ("none", "off", "false"):
        return "none"
    if mode in ("token", "on", "true"):
        return "token"
    # auto: localhost stays open, anything reachable from the network needs a token
    return "none" if is_loopback(ui_cfg.host) else "token"


def load_token(ui_cfg: UiConfig) -> str:
    """Return the configured token, or "" when none exists. Never logs it."""
    env = os.environ.get(TOKEN_ENV, "").strip()
    if env:
        return env
    path = token_file(ui_cfg)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_token(ui_cfg: UiConfig) -> str:
    """Return the token, creating the file (0600) on first use."""
    existing = load_token(ui_cfg)
    if existing:
        return existing

    path = token_file(ui_cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    # Create with 0600 from the start instead of chmod afterwards: between the
    # write and the chmod the secret would be world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


def verify(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)
