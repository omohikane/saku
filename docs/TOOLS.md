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

## Standard Tools

SAKU comes with a built-in set of tools located in `src/tools/`:

| Tool Name | Class / File | Description | Output |
|---|---|---|---|
| `LIST_DIR` | `list_dir.py` | Lists the files and subdirectories relative to the memory directory. | Multi-line string of file/folder prefixes. |
| `READ_FILE` | `read_file.py` | Reads the contents of a text file inside memory or vault directory (truncated if too long). | Contents of the file. |
| `WRITE_FILE` | `write_file.py` | Writes or overwrites a text file in allowed memory sub-directories. | `[OK] wrote <path> (<size> bytes)` or error. |
| `SEARCH_NOTES` | `search_notes.py` | Performs a case-insensitive search on files inside the memory directory. | List of matching files and matching lines. |
| `WEB_SEARCH` | `web_search.py` | Uses DuckDuckGo Lite (no external API keys needed) to query terms on the internet. | Title, URL, and snippet list of top 5 results. |
| `EXECUTE_CODE` | `execute_code.py` | Runs Python code inside the sandboxed `study/` directory with a 5s limit. | Combined standard output and error. |

---

## Writing Custom Tools

You can extend SAKU's capabilities by adding new Python files to the `src/tools/` directory.

### Requirements

1. **Filename**: The filename in lowercase becomes the tool name in uppercase.
   - Example: `src/tools/my_custom_tool.py` maps to `[[MY_CUSTOM_TOOL]]`.
2. **Signature**: Must implement a `run` function taking three parameters:
   - `base` (Path): The absolute path of the memory directory (`SAKU_ROOT`).
   - `path` (str): The value of the `path` argument (if passed, e.g. `path="foo.txt"`).
   - `body` (str): The text block between the tag definition line and `[[END]]`.

### Example Tool

```python
# src/tools/greet.py
from pathlib import Path

def run(base: Path, path: str, body: str = "") -> str:
    name = body.strip() or "World"
    return f"Hello, {name}! Your memory root is located at {base.resolve()}"
```

To enable the agent to use your tool, you must also describe its usage in the system prompt. Edit `src/saku_core.py` under the `# Capabilities & Tools` section of `build_system_prompt()` to include your new tool's format and instructions.
