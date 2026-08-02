"""LLM client.

``chat_stream`` takes ``LlmConfig`` as an argument (per-call settings).
It does not depend on global API_URL / API_KEY / MODEL.
"""

import json
from typing import Callable, Optional

import requests

from .config import LlmConfig

# Stop tokens: prevent the model from mimicking the Owner's speech
STOP_TOKENS = ["Owner:", "Owner>", "\nOwner:", "\nOwner>", "\n**Owner**", "**Owner**"]


def chat_stream(
    messages: list[dict],
    llm_cfg: LlmConfig,
    on_token: Optional[Callable[[str], None]] = None,
) -> str:
    """Send messages to the LLM API and receive them as a stream.

    - The return value is the complete response (including the <think> block)
    - If on_token is not given, visible tokens are printed to stdout
      (same behavior as the previous interactive/daemon display)
    """
    payload = {
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "top_p": 0.9,
        "stop": STOP_TOKENS,
    }
    if llm_cfg.model:
        payload["model"] = llm_cfg.model

    headers = {}
    if llm_cfg.api_key:
        headers["Authorization"] = f"Bearer {llm_cfg.api_key}"

    try:
        resp = requests.post(
            llm_cfg.api_url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=300,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        return "[ERROR] llama-server not reachable at " + llm_cfg.api_url
    except requests.HTTPError as e:
        try:
            detail = e.response.text[:300]
        except Exception:
            detail = ""
        return f"[ERROR] {e}" + (f"\n{detail}" if detail else "")

    emit = on_token if on_token is not None else _default_emit

    full = ""
    in_thinking = False
    tag_buffer = ""

    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if not decoded.startswith("data: "):
            continue
        payload = decoded[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
            delta = chunk["choices"][0]["delta"].get("content", "")
            if not delta:
                continue
        except (json.JSONDecodeError, KeyError, IndexError):
            continue

        full += delta

        for ch in delta:
            tag_buffer += ch

            if not in_thinking:
                if "<think>" in tag_buffer:
                    before = tag_buffer.split("<think>")[0]
                    if before:
                        emit(before)
                    tag_buffer = ""
                    in_thinking = True
                elif "<" in tag_buffer:
                    if len(tag_buffer) >= 7:
                        emit(tag_buffer)
                        tag_buffer = ""
                else:
                    emit(tag_buffer)
                    tag_buffer = ""
            else:
                if "</think>" in tag_buffer:
                    after = tag_buffer.split("</think>")[-1]
                    tag_buffer = after
                    in_thinking = False
                    if tag_buffer and "<" not in tag_buffer:
                        emit(tag_buffer)
                        tag_buffer = ""

    if tag_buffer and not in_thinking:
        emit(tag_buffer)

    emit("\n")
    return full


def _default_emit(text: str) -> None:
    """Previous stdout stream output."""
    print(text, end="", flush=True)
