# Contributing to SAKU

Thank you for your interest in contributing to SAKU! We welcome all contributions, including bug fixes, feature suggestions, documentation improvements, and tool extensions.

## Coding Style & Guidelines

- **Keep it simple:** Avoid adding heavy external dependencies. SAKU values local execution with standard libraries as much as possible. Currently, only `requests` and Python standard libraries are used.
- **Maintain docstrings and comments:** Explain the logic clearly. Keep comments concise and informative.
- **Unified Tool Signature:** All tool plugins in `src/system_tools/` must implement a `run(base: Path, path: str, body: str, **kwargs) -> str` signature.
- **Safety first:** Ensure file operations do not access directories outside the allowed scope of the vault or memory root.

## Adding a New Tool

1. Create a Python file under `src/system_tools/` (e.g. `src/system_tools/my_tool.py`).
2. Implement the entrypoint function:
   ```python
   from pathlib import Path

   def run(base: Path, path: str, body: str = "") -> str:
       # base is SAKU_ROOT (configured memory root)
       # path is an optional argument passed from SAKU
       # body is the content wrapped inside the [[MY_TOOL]] ... [[END]] block
       
       # Perform your tool logic here...
       return "[OK] result here"
   ```
3. Update the capability instructions inside `src/saku_core.py` under the `# Capabilities & Tools` section of `build_system_prompt()` to describe the tool's signature and usage examples.

## How to Test

Before submitting a Pull Request, run the test suite to ensure no existing functionalities are broken.
```bash
python -m unittest discover -s tests -p "*test*.py"
```

## Pull Request Process

1. Fork the repository and create your branch from `main`.
2. Commit your changes with descriptive commit messages.
3. Make sure all tests pass.
4. Submit a Pull Request targeting the `main` branch.
