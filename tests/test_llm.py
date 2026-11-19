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
    def __init__(self, lines=None):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self):
        if self._lines is not None:
            return iter(self._lines)
        return iter([
            b'data: {"choices":[{"delta":{"content":"ok"}}]}',
            b"data: [DONE]",
        ])


def _capture(llm_cfg, lines=None):
    captured = {}

    def _fake_post(url, json=None, headers=None, stream=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse(lines)

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


def test_empty_stream_is_reported_as_an_error():
    """#113: a stream that closes without content must not look like a reply."""
    lines = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        b'data: {"choices":[{"delta":{"content":""}}]}',
        b"data: [DONE]",
    ]
    _captured, out = _capture(LlmConfig(name="opencode-ds", api_url="http://x"), lines)
    assert out.startswith("[ERROR] empty reply from 'opencode-ds'")
    assert "without content" in out


def test_whitespace_only_stream_is_reported_as_an_error():
    _captured, out = _capture(
        LlmConfig(name="x", api_url="http://x"),
        [b'data: {"choices":[{"delta":{"content":"\\n\\n"}}]}', b"data: [DONE]"],
    )
    assert out.startswith("[ERROR]")


def test_think_only_stream_is_not_an_error():
    """Tool/thinking text is still a response: only a truly empty stream errors."""
    _captured, out = _capture(
        LlmConfig(name="x", api_url="http://x"),
        [b'data: {"choices":[{"delta":{"content":"<think>memo</think>"}}]}', b"data: [DONE]"],
    )
    assert not out.startswith("[ERROR]")
    assert "<think>memo</think>" in out


def test_stream_status_reports_completion():
    captured, out = _capture(LlmConfig(name="x", api_url="http://x"))
    assert captured  # sanity
    # _capture does not forward status; exercise chat_stream directly.
    box = {}
    original = llm_mod.requests.post
    llm_mod.requests.post = lambda *a, **k: _FakeResponse()
    try:
        out = llm_mod.chat_stream(
            [{"role": "user", "content": "hi"}],
            LlmConfig(name="x", api_url="http://x"),
            on_token=lambda _t: None,
            status=box,
        )
    finally:
        llm_mod.requests.post = original
    assert out == "ok"
    assert box == {"complete": True, "error": False}


def test_stream_status_marks_a_missing_done_as_incomplete():
    box = {}
    lines = [b'data: {"choices":[{"delta":{"content":"half"}}]}']  # no [DONE]
    original = llm_mod.requests.post
    llm_mod.requests.post = lambda *a, **k: _FakeResponse(lines)
    try:
        out = llm_mod.chat_stream(
            [{"role": "user", "content": "hi"}],
            LlmConfig(name="x", api_url="http://x"),
            on_token=lambda _t: None,
            status=box,
        )
    finally:
        llm_mod.requests.post = original
    assert out == "half"
    assert box["complete"] is False
    assert box["error"] is False


def test_display_does_not_depend_on_chunk_boundaries():
    """#127: the same reply, chunked differently, must display the same text."""

    def render(deltas):
        emitted = []
        lines = [
            ("data: " + __import__("json").dumps({"choices": [{"delta": {"content": d}}]}))
            .encode("utf-8")
            for d in deltas
        ] + [b"data: [DONE]"]
        original = llm_mod.requests.post
        llm_mod.requests.post = lambda *a, **k: _FakeResponse(lines)
        try:
            llm_mod.chat_stream(
                [{"role": "user", "content": "hi"}],
                LlmConfig(name="x", api_url="http://x"),
                on_token=emitted.append,
            )
        finally:
            llm_mod.requests.post = original
        return "".join(emitted)

    text = 'Hello 1 < 2 [[READ_FILE path="x.md"]]\n[[END]] Done'
    one_shot = render([text])
    per_char = render(list(text))
    # Split inside the tool tag and around the comparison operator.
    awkward = render(["Hello 1 < 2 [", "[READ_FILE path=\"x.md\"]]\n", "[[END]] Done"])
    assert one_shot == per_char == awkward
    assert "READ_FILE" not in one_shot
    assert "Hello 1 < 2" in one_shot


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


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} llm payload tests PASSED!")


if __name__ == "__main__":
    run_tests()
