"""Run the SAKU test suite."""

import subprocess
import sys
from pathlib import Path


_TIMEOUT = 30


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    test_dir = Path(__file__).resolve().parent.parent
    test_file = test_dir / "test_saku.py"

    if not test_file.exists():
        return f"[ERROR] test file not found: {test_file}"

    print(f"[*] Running tests: {test_file}")
    try:
        proc = subprocess.run(
            [sys.executable, str(test_file)],
            cwd=str(test_dir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )

        output = []
        if proc.stdout:
            output.append(proc.stdout.strip())
        if proc.stderr:
            output.append(f"[STDERR]\n{proc.stderr.strip()}")

        result = "\n".join(output).strip()
        if proc.returncode != 0:
            result = f"[FAIL] exit code {proc.returncode}\n{result}"
        else:
            result = f"[OK]\n{result}"

        return result

    except subprocess.TimeoutExpired:
        return "[ERROR] tests timed out (limit: 30s)"
    except Exception as e:
        return f"[ERROR] failed to run tests: {e}"
