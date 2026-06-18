import os
import subprocess
import tempfile
from pathlib import Path

def run(base: Path, path: str, body: str = "") -> str:
    """Execute arbitrary Python code block provided by SAKU inside the study directory.

    Expects python code block in the body.
    """
    code_content = body.strip()
    if not code_content:
        return "[ERROR] Code block is empty."

    study_dir = base / "study"
    study_dir.mkdir(exist_ok=True)

    # Write code to a temporary python file inside _saku/study/ to keep execution local
    # We use a temp file prefix to avoid collision
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            dir=study_dir,
            delete=False,
            encoding="utf-8"
        ) as f:
            f.write(code_content)
            temp_file_path = Path(f.name)

        # Run script with a timeout to prevent infinite loops
        # cwd is set to study_dir so files created by SAKU's script stay there
        print(f"[*] Running SAKU script: {temp_file_path.name}")
        proc = subprocess.run(
            ["python3", str(temp_file_path)],
            cwd=study_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0  # 5-second execution limit
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        exit_code = proc.returncode

        # Cleanup script file after run
        if temp_file_path.exists():
            temp_file_path.unlink()

        output = []
        if stdout:
            output.append(f"[STDOUT]\n{stdout}")
        if stderr:
            output.append(f"[STDERR]\n{stderr}")
        
        result_text = "\n".join(output).strip()
        if not result_text:
            result_text = f"(Success with no output, exit code: {exit_code})"
        else:
            result_text = f"{result_text}\n(Exit code: {exit_code})"
            
        return result_text

    except subprocess.TimeoutExpired:
        # Cleanup file if timeout expired
        if 'temp_file_path' in locals() and temp_file_path.exists():
            temp_file_path.unlink()
        return "[ERROR] Execution timed out (limit: 5.0 seconds)."
    except Exception as e:
        if 'temp_file_path' in locals() and temp_file_path.exists():
            temp_file_path.unlink()
        return f"[ERROR] Execution failed: {e}"
