"""LLM client.

``chat_stream`` takes ``LlmConfig`` as an argument (per-call settings).
It does not depend on global API_URL / API_KEY / MODEL.
"""

import json
import time
from typing import Callable, Optional

import requests

from .config import LlmConfig
from .thinking import strip_tool_blocks

# Stop tokens: prevent the model from mimicking the Owner's speech
STOP_TOKENS = ["Owner:", "Owner>", "\nOwner:", "\nOwner>", "\n**Owner**", "**Owner**"]


def chat_stream(
    messages: list[dict],
    llm_cfg: LlmConfig,
    on_token: Optional[Callable[[str], None]] = None,
    status: "dict | None" = None,
) -> str:
    """Send messages to the LLM API and receive them as a stream.

    - The return value is the complete response (including the <think> block)
    - If on_token is not given, visible tokens are printed to stdout
      (same behavior as the previous interactive/daemon display)
    - ``status`` (#127) is filled with ``complete`` / ``error`` / ``done`` so a
      caller can tell a finished generation from a stream that hit EOF early.
      The return value is unchanged, so existing callers keep working.
    """
    payload = {
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "top_p": 0.9,
        "stop": list(llm_cfg.stop_tokens) if llm_cfg.stop_tokens else STOP_TOKENS,
    }
    if llm_cfg.model:
        payload["model"] = llm_cfg.model
    if llm_cfg.reasoning_effort:
        # Reasoning engines (e.g. Strata / Qwen3.8-Flash-Next) put the answer in
        # reasoning_content unless reasoning is disabled; "none" gives plain content.
        payload["reasoning_effort"] = llm_cfg.reasoning_effort

    def _mark(*, complete: bool, error: bool) -> None:
        if status is not None:
            status["complete"] = complete
            status["error"] = error

    _mark(complete=False, error=False)

    headers = {str(k): str(v) for k, v in (llm_cfg.headers or {}).items()}
    if llm_cfg.api_key:
        # Set last: a profile header must not be able to replace the credential.
        headers["Authorization"] = f"Bearer {llm_cfg.api_key}"

    try:
        resp = requests.post(
            llm_cfg.api_url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=900,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        _mark(complete=False, error=True)
        return "[ERROR] llama-server not reachable at " + llm_cfg.api_url
    except requests.HTTPError as e:
        try:
            detail = e.response.text[:300]
        except Exception:
            detail = ""
        _mark(complete=False, error=True)
        return f"[ERROR] {e}" + (f"\n{detail}" if detail else "")

    emit = on_token if on_token is not None else _default_emit

    started = time.time()
    full = ""
    done = False
    in_thinking = False
    buf = ""  # undecided tail (a partial <think> / </think> tag)
    visible_raw_parts: list[str] = []

    def _visible() -> str:
        return "".join(visible_raw_parts)

    emitted = 0  # how many chars of the cleaned visible text have been emitted

    def _flush() -> None:
        """Emit the cleaned (tool-syntax-free) visible text up to the last settled point.

        The tail after an unclosed ``[[`` is held back so partial tool-call syntax
        never reaches the display.
        """
        nonlocal emitted
        visible_raw = _visible()
        hold = _hold_point(visible_raw)
        clean = strip_tool_blocks(visible_raw[:hold])
        if len(clean) > emitted:
            emit(clean[emitted:])
            emitted = len(clean)

    def _tag_prefix_suffix(text: str, tag: str) -> str:
        """Longest suffix of ``text`` that is a proper prefix of ``tag``."""
        for n in range(min(len(text), len(tag) - 1), 0, -1):
            if text.endswith(tag[:n]):
                return text[-n:]
        return ""

    def _feed(chunk: str) -> None:
        """Incremental thinking-tag scanner (#127).

        Chunk-boundary independent by construction: the state is the mode plus
        an undecided tail, never a guess about how much of a tag has arrived.
        """
        nonlocal buf, in_thinking
        buf += chunk
        while buf:
            if in_thinking:
                idx = buf.find("</think")
                if idx == -1:
                    buf = _tag_prefix_suffix(buf, "</think")
                    return
                end = buf.find(">", idx)
                if end == -1:
                    return
                buf = buf[end + 1 :]
                in_thinking = False
                continue
            idx = buf.find("<think")
            if idx == -1:
                hold = _tag_prefix_suffix(buf, "<think")
                visible_raw_parts.append(buf[: len(buf) - len(hold)] if hold else buf)
                buf = hold
                return
            end = buf.find(">", idx)
            if end == -1:
                visible_raw_parts.append(buf[:idx])
                buf = buf[idx:]
                return
            visible_raw_parts.append(buf[:idx])
            buf = buf[end + 1 :]
            in_thinking = True

    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if not decoded.startswith("data: "):
            continue
        payload = decoded[6:]
        if payload.strip() == "[DONE]":
            done = True
            break
        try:
            chunk = json.loads(payload)
            delta = chunk["choices"][0]["delta"].get("content", "")
            if not delta:
                continue
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

        full += delta
        _feed(delta)
        _flush()

    _flush()
    emit("\n")

    # #113: a stream that closes without ever producing content used to return
    # "" and the caller could not tell it apart from "the model had nothing to
    # say". Every consumer already treats a leading [ERROR] as a failed call,
    # so report the silent case as one instead of letting it look like a reply.
    if not full.strip():
        _mark(complete=False, error=True)
        return (
            f"[ERROR] empty reply from '{llm_cfg.name}': "
            f"the stream ended without content after {time.time() - started:.0f}s"
        )

    # #127: no [DONE] means the stream closed early. The text may be a complete
    # answer or a cut-off one; the caller decides, but side effects must not
    # start from it (agent_loop refuses to run tools on an incomplete turn).
    _mark(complete=done, error=False)
    return full


def _hold_point(visible: str) -> int:
    """Index from which display output must wait for more text (#127).

    A partially streamed tool tag must not be shown: the block is removed once
    it completes, and showing it first made the display depend on chunk
    boundaries. Hold an unclosed ``[[...`` (until ``[[END]]``) and an unclosed
    single-bracket tag (``[`` without ``]``).
    """
    i = visible.rfind("[")
    while i != -1:
        rest = visible[i:]
        if rest.startswith("[[") and "[[END]]" not in rest:
            return i
        if not rest.startswith("[[") and "]" not in rest:
            # Hold the preceding "[" as well: emitting the first half of a
            # potential "[[" is exactly what made the display chunk-dependent.
            while i > 0 and visible[i - 1] == "[":
                i -= 1
            return i
        i = visible.rfind("[", 0, i)
    return len(visible)


def _default_emit(text: str) -> None:
    """Previous stdout stream output."""
    print(text, end="", flush=True)
