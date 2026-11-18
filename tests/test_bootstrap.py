#!/usr/bin/env python3
"""Tests for saku.auth and saku.bootstrap (server exposure + guided setup)."""

import os
import sys
import tempfile
import tomllib
from argparse import Namespace
from pathlib import Path

CODE_ROOT = Path(__file__).parent
if str(CODE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT.parent))

from saku import auth, bootstrap
from saku.config import UiConfig


def _ui(**kw) -> UiConfig:
    base = dict(host="127.0.0.1", auth="auto", token_file="")
    base.update(kw)
    return UiConfig(**base)


# ── auth ────────────────────────────────────────────────
def test_token_is_created_private_and_stable():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "saku" / "ui.token"
        cfg = _ui(token_file=str(path))
        token = auth.ensure_token(cfg)
        assert len(token) > 20
        assert path.exists()
        assert (path.stat().st_mode & 0o077) == 0, "token file must not be group/world readable"
        assert auth.ensure_token(cfg) == token, "token must survive restarts"


def test_env_token_takes_precedence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ui.token"
        path.write_text("from-file\n", encoding="utf-8")
        cfg = _ui(token_file=str(path))
        os.environ["SAKU_UI_TOKEN"] = "from-env"
        try:
            assert auth.load_token(cfg) == "from-env"
        finally:
            del os.environ["SAKU_UI_TOKEN"]


def test_auth_mode_defaults():
    assert auth.auth_mode(_ui(host="127.0.0.1", auth="auto")) == "none"
    assert auth.auth_mode(_ui(host="100.102.38.56", auth="auto")) == "token"
    assert auth.auth_mode(_ui(host="0.0.0.0", auth="none")) == "none"
    assert auth.auth_mode(_ui(host="127.0.0.1", auth="token")) == "token"


def test_verify_is_strict():
    assert auth.verify("abc", "abc")
    assert not auth.verify("abc", "abd")
    assert not auth.verify("", "abc")
    assert not auth.verify("abc", "")


# ── bootstrap ───────────────────────────────────────────
def _args(**kw) -> Namespace:
    base = dict(
        mode="manual",
        memory="local",
        memory_path=None,
        bind="local",
        host=None,
        port=8787,
        auth=None,
        token_file=None,
        llm_url="http://127.0.0.1:8080/v1/chat/completions",
        llm_model="",
        insecure=False,
        force=False,
        yes=True,
    )
    base.update(kw)
    return Namespace(**base)


def test_plan_requires_token_when_exposed():
    plan = bootstrap.build_plan(_args(bind="any"))
    assert plan.auth_mode == "token", "a non-loopback bind must not default to no auth"


def test_plan_refuses_insecure_exposure():
    try:
        bootstrap.build_plan(_args(bind="any", auth="none"))
    except SystemExit as e:
        assert "insecure" in str(e)
    else:
        raise AssertionError("auth=none on a non-loopback host must be refused")


def test_plan_insecure_override():
    plan = bootstrap.build_plan(_args(bind="any", auth="none", insecure=True))
    assert plan.host == "0.0.0.0"
    assert plan.auth_mode == "none"


def test_git_memory_plan_enables_sync():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "saku-memory"
        plan = bootstrap.build_plan(_args(memory="git", memory_path=str(target)))
        assert plan.git_enabled
        assert plan.memory_path == target
        text = bootstrap.render_config(plan)
        assert "[git]" in text
        assert "enabled = true" in text


def test_vault_memory_plan_points_at_saku_layout():
    with tempfile.TemporaryDirectory() as tmp:
        plan = bootstrap.build_plan(_args(memory="vault", memory_path=tmp))
        assert plan.memory_path == Path(tmp) / "_saku" / "memory"
        assert 'inbox_dir = "%s/00_Inbox"' % (Path(tmp).parent / Path(tmp).name) in bootstrap.render_config(plan)


def test_config_contains_auth_and_ui():
    plan = bootstrap.build_plan(_args(bind="netbird", host="100.64.0.5"))
    text = bootstrap.render_config(plan)
    assert '[ui]' in text
    assert 'host = "100.64.0.5"' in text
    assert 'auth = "token"' in text
    assert "token_file" in text


def test_unit_has_hardening_and_memory_path():
    with tempfile.TemporaryDirectory() as tmp:
        plan = bootstrap.build_plan(_args(memory_path=tmp))
        unit = bootstrap.render_unit(plan, "saku")
        assert "ExecStart=" in unit
        assert sys.executable in unit, "the venv interpreter must survive into the unit"
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert f"ReadWritePaths={tmp}" in unit
        assert "EnvironmentFile=-/etc/saku/saku.env" in unit


def test_compose_mounts_config_and_memory():
    with tempfile.TemporaryDirectory() as tmp:
        plan = bootstrap.build_plan(_args(memory_path=tmp, bind="any", insecure=True, auth="token"))
        compose = bootstrap.render_compose(plan)
        assert "services:" in compose
        assert "restart: unless-stopped" in compose
        assert f"{tmp}:{tmp}" in compose


def test_saku_config_override(tmp_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "conf.toml"
        cfg.write_text('[memory]\nroot = "mem"\n', encoding="utf-8")
        previous = os.environ.get("SAKU_CONFIG")
        os.environ["SAKU_CONFIG"] = str(cfg)
        try:
            from saku.config import load_config, resolve_memory_root

            data, base = load_config()
            assert data["memory"]["root"] == "mem"
            assert base == Path(tmp)
            assert resolve_memory_root(data, base) == Path(tmp) / "mem"
        finally:
            if previous is None:
                os.environ.pop("SAKU_CONFIG", None)
            else:
                os.environ["SAKU_CONFIG"] = previous


def test_bootstrap_end_to_end_writes_config_and_unit():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        (repo / "packaging").mkdir(parents=True)
        saved_repo, saved_gen = bootstrap.REPO_ROOT, bootstrap.GENERATED
        bootstrap.REPO_ROOT = repo
        bootstrap.GENERATED = repo / "packaging" / "generated"
        try:
            rc = bootstrap.main(
                [
                    "--yes",
                    "--mode",
                    "systemd",
                    "--memory",
                    "local",
                    "--memory-path",
                    str(tmp / "mem"),
                    "--bind",
                    "local",
                    "--auth",
                    "token",
                    "--token-file",
                    str(tmp / "ui.token"),
                ]
            )
            assert rc == 0
            config = (repo / "config.toml").read_text(encoding="utf-8")
            assert "[memory]" in config and 'auth = "token"' in config
            assert (tmp / "ui.token").exists()
            assert (repo / "packaging" / "generated" / "saku.service").exists()
            assert (tmp / "mem" / "MEMORY.md").exists()
        finally:
            bootstrap.REPO_ROOT, bootstrap.GENERATED = saved_repo, saved_gen


def test_bootstrap_refuses_to_clobber_config():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        repo.mkdir()
        (repo / "config.toml").write_text("original", encoding="utf-8")
        saved_repo = bootstrap.REPO_ROOT
        bootstrap.REPO_ROOT = repo
        try:
            rc = bootstrap.main(["--yes", "--mode", "manual", "--memory-path", str(tmp / "mem")])
            assert rc == 1
            assert (repo / "config.toml").read_text(encoding="utf-8") == "original"
        finally:
            bootstrap.REPO_ROOT = saved_repo


def test_vault_mode_enables_git_sync():
    with tempfile.TemporaryDirectory() as tmp:
        plan = bootstrap.build_plan(_args(memory="vault", memory_path=tmp))
        assert plan.git_enabled
        assert plan.git_root == tmp
        assert "[git]" in bootstrap.render_config(plan)


def test_existing_sections_are_preserved_on_rewrite():
    existing = {
        "llm": {
            "active_profile": "litellm",
            "profiles": {"litellm": {"api_url": "http://gw/v1", "model": "m"}},
        },
        "daemon": {"tick_interval_seconds": 60},
        "memory": {"root": "old/path"},
    }
    plan = bootstrap.build_plan(_args())
    text = bootstrap.render_config(plan, existing)
    parsed = tomllib.loads(text)  # duplicate tables would raise here
    assert parsed["llm"]["active_profile"] == "litellm"
    assert parsed["llm"]["profiles"]["litellm"]["api_url"] == "http://gw/v1"
    assert parsed["daemon"]["tick_interval_seconds"] == 60
    assert parsed["memory"]["root"] != "old/path"  # managed section is regenerated


def test_generated_config_is_valid_toml_without_previous():
    plan = bootstrap.build_plan(_args())
    parsed = tomllib.loads(bootstrap.render_config(plan))
    assert parsed["llm"]["active_profile"] == "local"
    assert parsed["memory"]["root"] == str(plan.memory_path)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} auth/bootstrap tests PASSED!")


if __name__ == "__main__":
    run_tests()
