"""Switch LLM profile dynamically."""

from pathlib import Path
import sys

def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    """Switch LLM profile. Usage: [[SWITCH_PROFILE]] profile_name [[END]]"""
    profile_name = body.strip()
    
    if not profile_name:
        return "[ERROR] profile name is required"
    
    # Import saku_core to access the switch function
    CODE_ROOT = Path(__file__).parent.parent
    sys.path.append(str(CODE_ROOT))
    
    try:
        import saku_core
        result = saku_core.switch_llm_profile(profile_name)
        return result
    except Exception as e:
        return f"[ERROR] Failed to switch profile: {e}"
