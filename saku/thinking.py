"""応答テキストから思考（<think>）と可視部分を分離する。"""

import re


def split_thinking(text: str) -> tuple[str, str]:
    """応答を (thinking, visible) に分離する。"""
    think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    thinking = "\n\n".join(block.strip() for block in think_blocks if block.strip())

    visible = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    visible = re.sub(r"\[thinking\.{0,3}\]\s*", "", visible)
    visible = visible.strip()

    return thinking, visible
