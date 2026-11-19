#!/usr/bin/env python3
"""
Unit tests for the LLM request payload (saku.llm).

No network: requests.post is replaced with a fake that captures the payload.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import saku.llm as llm_mod
from saku import config as cfg_mod
from saku.config import LlmConfig


class _FakeResponse:
    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter([
            b'data: {"choices":[{"delta":{"content":"ok"}}]}',
            b"data: [DONE]",
        ])


def _capture(llm_cfg):
    captured = {}

    def _fake_post(url, json=None, headers=None, stream=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse()

    original = llm_mod.requests.post
    llm_mod.requests.post = _fake_post
    try:
        out = llm_mod.chat_stream(
            [{"role": "user", "content": "hi"}], llm_cfg, on_token=lambda t: None
        )
    finally:
        llm_mod.requests.post = original
    return captured, out


def test_reasoning_effort_sent_when_configured():
    captured, out = _capture(
        LlmConfig(name="strata", api_url="http://x", model="m", reasoning_effort="none")
    )
    assert captured["payload"]["reasoning_effort"] == "none"
    assert out == "ok"


def test_reasoning_effort_omitted_when_unset():
    captured, _ = _capture(LlmConfig(name="local", api_url="http://x", model="m"))
    assert "reasoning_effort" not in captured["payload"]
    assert captured["payload"]["stream"] is True


def test_stop_tokens_override_default():
    captured, _ = _capture(
        LlmConfig(name="strata", api_url="http://x", stop_tokens=["Owner:", "**Owner**"])
    )
    assert captured["payload"]["stop"] == ["Owner:", "**Owner**"]


def test_stop_tokens_default_used_when_unset():
    captured, _ = _capture(LlmConfig(name="local", api_url="http://x"))
    assert captured["payload"]["stop"] == llm_mod.STOP_TOKENS


def test_load_profile_reads_reasoning_effort():
    cfg = {
        "llm": {
            "profiles": {
                "strata": {
                    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
                    "model": "qwen3.8-flash-next-iq2_xs",
                    "reasoning_effort": "none",
                }
            }
        }
    }
    prof = cfg_mod.load_profile(cfg, "strata")
    assert prof.reasoning_effort == "none"
    plain = cfg_mod.load_profile({"llm": {"profiles": {"x": {"api_url": "u"}}}}, "x")
    assert plain.reasoning_effort == ""


def test_load_profile_expands_api_key_from_env():
    import os

    os.environ["SAKU_TEST_KEY"] = "sk-from-env"
    try:
        cfg = {
            "llm": {
                "profiles": {
                    "d": {"api_url": "http://x", "api_key": "${SAKU_TEST_KEY}"}
                }
            }
        }
        assert cfg_mod.load_profile(cfg, "d").api_key == "sk-from-env"
        del os.environ["SAKU_TEST_KEY"]
        # An unset variable is not sent as a literal bearer token.
        assert cfg_mod.load_profile(cfg, "d").api_key == ""
    finally:
        os.environ.pop("SAKU_TEST_KEY", None)


def test_profile_headers_are_sent():
    cfg = {
        "llm": {
            "profiles": {
                "opencode": {
                    "api_url": "http://x/v1/chat/completions",
                    "headers": {"x-opencode-session": "saku"},
                }
            }
        }
    }
    prof = cfg_mod.load_profile(cfg, "opencode")
    assert prof.headers == {"x-opencode-session": "saku"}

    captured = {}

    def _fake_post(url, json=None, headers=None, stream=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse()

    orig = llm_mod.requests.post
    llm_mod.requests.post = _fake_post
    try:
        llm_mod.chat_stream([{"role": "user", "content": "hi"}], prof)
    finally:
        llm_mod.requests.post = orig
    assert captured["headers"]["x-opencode-session"] == "saku"


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} llm payload tests PASSED!")


if __name__ == "__main__":
    run_tests()
