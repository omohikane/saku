# TOOLS

This document details the tool system used by SAKU.

## Tool Calling Format

SAKU interacts with the outside world by generating special blocks in its chat response. When SAKU outputs these blocks, `saku_core.py` parses and executes them before feeding the result back into SAKU's context.

The format is:
```text
[[TOOL_NAME arg1="val1" arg2="val2"]]
tool body or input content here
[[END]]
```

For example, to search the web:
```text
[[WEB_SEARCH]]
What is the latest version of llama.cpp?
[[END]]
```

---

## Tool Hierarchy

SAKU has two tool layers:

| Layer | Location | Managed by | Overridable |
|---|---|---|---|
| **System Tools** | `src/system_tools/` | Developer (you) | No (read-only for SAKU) |
| **SAKU's Own Tools** | `memory/tools/` | SAKU itself | Yes (takes priority over system tools) |

When SAKU calls a tool, `memory/tools/` is checked first. If the tool exists there, it takes precedence. Otherwise, `src/system_tools/` is used as fallback.

---

## Standard System Tools

SAKU comes with a built-in set of tools located in `src/system_tools/`:

| Tool Name | Description |
|---|---|
| `LIST_DIR` | Lists files and subdirectories relative to the memory directory. |
| `READ_FILE` | Reads the contents of a text file inside memory or vault directory (truncated if too long). |
| `WRITE_FILE` | Writes or overwrites a text file in allowed memory sub-directories. |
| `APPEND_FILE` | Appends content to a file. Supports `heading=` to insert under a specific `##` section. |
| `SEARCH_NOTES` | Performs a case-insensitive keyword search on markdown files inside the vault. |
| `WEB_SEARCH` | Searches the web via DuckDuckGo Lite (no API key needed). |
| `FETCH_URL` | Fetches and returns the text content of a web page. |
| `EXECUTE_CODE` | Runs Python code inside the sandboxed `study/` directory (5s timeout). |
| `DELETE_FILE` | Deletes a file in allowed directories (blocks system files and `src/system_tools/`). |
| `MOVE_FILE` | Moves or renames a file within allowed directories. |
| `RUN_TESTS` | Executes the SAKU test suite (`test_saku.py`) and returns results. |
| `GREP_CODE` | Searches Python source code in `system_tools/` and `tools/` with regex. |
| `GIT` | Runs limited git commands (status, diff, add, commit, log, branch only). |
| `API_CALL` | Makes HTTP GET/POST calls to external APIs (blocks private/localhost addresses). |
| `SWITCH_PROFILE` | Switches the active LLM profile (local, openai, openrouter, anthropic). |

---

## Writing Custom System Tools

You can extend SAKU's capabilities by adding new Python files to the `src/system_tools/` directory.

### Requirements

1. **Filename**: The filename in lowercase becomes the tool name in uppercase.
   - Example: `src/system_tools/my_custom_tool.py` maps to `[[MY_CUSTOM_TOOL]]`.
2. **Signature**: Must implement a `run` function taking three parameters:
   - `base` (Path): The absolute path of the memory directory (`SAKU_ROOT`).
   - `path` (str): The value of the `path` argument (if passed, e.g. `path="foo.txt"`).
   - `body` (str): The text block between the tag definition line and `[[END]]`.
   - `**kwargs`: Additional parameters from the tool call are passed as keyword arguments.

### Example Tool

```python
# src/system_tools/greet.py
from pathlib import Path

def run(base: Path, path: str, body: str = "", **kwargs) -> str:
    name = body.strip() or "World"
    return f"Hello, {name}! Your memory root is located at {base.resolve()}"
```

To make the agent aware of your tool, describe its usage in `src/saku_core.py` under the `# Capabilities & Tools` section of `build_system_prompt()`.

---

## SAKU's Own Tools (runtime-created)

SAKU can create and modify its own tools in `memory/tools/` using `WRITE_FILE` or `APPEND_FILE`. These tools use the same interface as system tools and take priority over built-in tools with the same name.

Example: SAKU can override `READ_FILE` by writing to `tools/read_file.py` in its memory directory.
