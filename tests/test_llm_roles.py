#!/usr/bin/env python3
"""
Unit tests for role-based model routing (issue #105).

Two layers are covered:
- saku.config.load_llm_for_role: pure config resolution. A configured role
  profile wins over active_profile; an unset role is exactly
  load_active_llm().
- saku.core.llm_for_role: the live resolver used by call sites. An unset
  role returns the current LLM object itself, so SWITCH_PROFILE keeps
  governing every task whose role is not configured.

No network, no LLM: profiles are plain config dicts.
"""

import sys
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as cfg_mod
from saku.config import LlmConfig


def _cfg(**llm_extra):
    llm = {
        "active_profile": "cloud",
        "profiles": {
            "local": {"api_url": "http://127.0.0.1:8080/v1", "model": "local-model"},
            "cloud": {"api_url": "http://cloud.example/v1", "model": "cloud-model"},
        },
    }
    llm.update(llm_extra)
    return {"llm": llm}


# ── config.load_llm_for_role ────────────────────────────

def test_role_profile_wins_over_active():
    cfg = _cfg(chat="local", background="cloud")
    chat = cfg_mod.load_llm_for_role(cfg, "chat")
    assert chat.name == "local" and chat.model == "local-model", chat
    background = cfg_mod.load_llm_for_role(cfg, "background")
    assert background.name == "cloud" and background.model == "cloud-model", background


def test_unset_role_falls_back_to_active():
    cfg = _cfg()  # no role keys at all
    for role in ("chat", "background"):
        routed = cfg_mod.load_llm_for_role(cfg, role)
        active = cfg_mod.load_active_llm(cfg)
        assert routed.name == active.name == "cloud", routed
        assert routed.api_url == active.api_url and routed.model == active.model


def test_partially_set_roles():
    cfg = _cfg(chat="local")  # background unset
    assert cfg_mod.load_llm_for_role(cfg, "chat").name == "local"
    assert cfg_mod.load_llm_for_role(cfg, "background").name == "cloud"


def test_role_naming_missing_profile_falls_back_to_active():
    cfg = _cfg(chat="does-not-exist")
    routed = cfg_mod.load_llm_for_role(cfg, "chat")
    assert routed.name == "cloud" and routed.model == "cloud-model", routed


def test_no_profiles_at_all_uses_legacy_fallback():
    cfg = {"llm": {"api_url": "http://legacy.example/v1", "model": "legacy-model"}}
    routed = cfg_mod.load_llm_for_role(cfg, "chat")
    active = cfg_mod.load_active_llm(cfg)
    assert routed.api_url == active.api_url == "http://legacy.example/v1"
    assert routed.model == active.model == "legacy-model"


# ── core.llm_for_role ───────────────────────────────────

def _with_core_cfg(cfg):
    """Point saku.core at a synthetic config; returns a restore callable."""
    from saku import core

    old_cfg, old_llm = core._cfg, core._current_llm
    sentinel = LlmConfig(name="sentinel", api_url="http://sentinel/v1", model="s-model")
    core._cfg = cfg
    core._current_llm = sentinel

    def restore():
        core._cfg, core._current_llm = old_cfg, old_llm

    return core, sentinel, restore


def test_core_unset_role_returns_current_llm_object():
    core, sentinel, restore = _with_core_cfg(_cfg())
    try:
        # Identity, not just equality: callers must see the very object
        # SWITCH_PROFILE swaps out.
        assert core.llm_for_role("chat") is sentinel
        assert core.llm_for_role("background") is sentinel
    finally:
        restore()


def test_core_configured_role_wins():
    core, sentinel, restore = _with_core_cfg(_cfg(chat="local"))
    try:
        routed = core.llm_for_role("chat")
        assert routed is not sentinel
        assert routed.name == "local" and routed.model == "local-model", routed
        assert core.llm_for_role("background") is sentinel
    finally:
        restore()


def test_core_switch_profile_governs_unset_roles_only():
    core, sentinel, restore = _with_core_cfg(_cfg(chat="local"))
    try:
        msg = core.switch_llm_profile("cloud")
        assert msg.startswith("[OK]"), msg
        # Unset role follows the switch...
        assert core.llm_for_role("background").name == "cloud"
        assert core.llm_for_role("background") is core._current_llm
        # ...while the configured role keeps its config profile.
        assert core.llm_for_role("chat").name == "local"
    finally:
        restore()


def test_core_role_naming_missing_profile_returns_current():
    core, sentinel, restore = _with_core_cfg(_cfg(chat="does-not-exist"))
    try:
        assert core.llm_for_role("chat") is sentinel
    finally:
        restore()


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} llm role-routing tests PASSED!")


if __name__ == "__main__":
    run_tests()
