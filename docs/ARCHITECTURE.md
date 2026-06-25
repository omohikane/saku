# ARCHITECTURE

This document describes the structure, data flow, and components of SAKU.

## Overview

SAKU is designed as a modular, file-based agent framework. Unlike typical agents that depend on external vector databases, APIs, or complex databases, SAKU's memory and state are represented entirely by **plain Markdown and JSON files**.

```mermaid
graph TD
    Owner[Owner / User] <-->|chat.md / saku_core.py| Core[saku_core.py]
    Daemon[daemon.py] <-->|polls & checks| Core
    Daemon -->|triggers reflection| Reflect[reflect.py]
    Core -->|uses| Tools[src/system_tools/*]
    
    subgraph Memory Root (e.g. Obsidian Vault)
        ChatFile[chat.md]
        Journal[journal/ Daily logs]
        Monologue[monologue/ Self reflection]
        Principles[principles/ Learned lessons]
        Blog[blog/ Working drafts]
        Study[study/ Sandboxed coding]
        Tools[memory/tools/ SAKU-created tools]
        Meta[meta.md Self-model]
        State[state/ processed_inbox.json, chat_state.json, saku.log]
        Identity[identity/ soul.md & genome.md]
    end
    
    Core <-->|Read/Write| Memory
    Reflect <-->|Read/Write| Memory
    Tools <-->|Isolated Read/Write| Memory
```

---

## Directory Structure

- **`identity/`**: Holds foundational files that define SAKU's core.
  - `genome.md`: The owner-defined values, persona, constraints, and speaking style. This is read-only for SAKU.
- **`src/`**: Contains execution logic.
  - `saku_core.py`: The agent's brain. Orchestrates system prompts, formats LLM requests, handles tool calling, and drives the interactive terminal.
  - `daemon.py`: The background scheduler that triggers autonomous tasks, processes Obsidian inputs, runs reflections, and monitors conversations.
  - `reflect.py`: Triggered daily to aggregate the day's experiences, extract lessons, and update `meta.md`.
  - `system_tools/`: Auto-discovered system plugins that SAKU can call dynamically.
- **`memory/`**: The directory representing SAKU's long-term memory.
  - `journal/`: Logs of conversations and autonomous actions.
  - `monologue/`: SAKU's inner thoughts and research motivations.
  - `principles/`: Deduced insights, guidelines, and lessons.
  - `blog/`: Working drafts of blogs and other output files.
  - `study/`: Sandbox for running Python code experiments.
  - `tools/`: SAKU's own user-created tools (created at runtime, override system_tools/).

---

## Safety and Sandboxing

To ensure that a local autonomous agent does not harm your system, SAKU incorporates multiple design safeguards:

1. **Path Scoping**:
   - The file I/O tools (`read_file`, `write_file`, `list_dir`, `search_notes`) enforce strict path scoping.
   - SAKU is restricted to write only in allowed memory sub-directories: `blog/`, `monologue/`, `principles/`, `skills/`, `tools/`, `chat.md`, `study/`, `journal/`, `request_list.md`.
   - Modifying `meta.md`, `genome.md`, or writing to `src/` and `identity/` is blocked.
2. **Dynamic Tool Loading**:
   - System tools are dynamically loaded from `src/system_tools/` at runtime.
   - SAKU can create and modify its own tools in `memory/tools/`, which take priority over system tools with the same name.
3. **Isolated Code Execution**:
   - The `EXECUTE_CODE` tool executes Python code in a separate subprocess.
   - It runs inside `memory/study/` to prevent cluttering other folders.
   - The subprocess enforces a **5.0-second execution timeout** to prevent infinite loops from hanging the daemon.
