"""Switch LLM profile dynamically."""

from pathlib import Path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    """Switch LLM profile. Usage: [[SWITCH_PROFILE]] profile_name [[END]]"""
    profile_name = body.strip()

    if not profile_name:
        return "[ERROR] profile name is required"

    try:
        from saku import core

        return core.switch_llm_profile(profile_name)
    except Exception as e:
        return f"[ERROR] Failed to switch profile: {e}"
