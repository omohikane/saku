# CONSIDERATIONS — Open Design Questions

Record design questions that need discussion before implementation.
Each item: **Question → Background → Options → Status (Open / In Progress / Decided)**

---

## 1. Memory Search (On-Demand vs RAG)

- **Status**: Open
- **Background**: Local LLM eval noted "context bloat needs mitigation". Currently `MEMORY.md`/`principles`/`skills` are truncated to the most recent N chars and embedded every turn. We want to move to on-demand recall ("search when needed").
- **Options**:
  - A) Lightweight on-demand: use `SEARCH_NOTES` (grep) via system-prompt instruction, minimize always-embedded content. No extra deps.
  - B) Vector DB / RAG: embedding model + vector DB (SQLite FTS5 / sqlite-vec / external). More accurate but more deps/complexity.
  - C) Hybrid: small recent + on-demand search
- **Discussion**: For local-first/privacy, A/C has better cost/benefit. B only when memory truly bloats. Key question: **will the model reliably call the search tool** (local model reliability).

## 2. Sandboxing Autonomous Code Execution (Docker)

- **Status**: Open (future)
- **Background**: `EXECUTE_CODE` runs as subprocess in `study/` with 5s timeout + path scope, but **no container isolation**. Local LLM eval flagged privilege-escalation / filesystem risks.
- **Options**:
  - A) Keep as is (5s timeout + study/ scope)
  - B) Docker-isolated (per-run container, resource limits)
  - C) Require container when moving to dedicated VM (Phase E)
- **Discussion**: Containers add setup/run cost. How much autonomy to grant, and how it interacts with `[ops]` approval boundary.

## 3. Concurrent File Access (Locking)

- **Status**: Open (low priority)
- **Background**: daemon / Web UI / external editor (Obsidian) may touch the same Markdown. Currently mitigated with mtime tracking (`chat_state.json`). Acceptable for single-user, but race risk remains.
- **Options**:
  - A) Keep as is (single-user + mtime)
  - B) File locks (`fcntl`) to serialize writes
  - C) Channel abstraction (Phase B-4) to single write path
- **Discussion**: Becomes visible when multiple processes (UI + daemon separately) increase. Consider with B-4.

## 4. Evaluation Harness (with LLM)

- **Status**: Open
- **Background**: `evals/longitudinal/` (structural snapshots) is done. **Qualitative with-LLM evals** (`tests/persona/` `tests/memory/` `tests/autonomy/`) are planned.
- **Options**:
  - A) Keep snapshot comparison (done) and operate it
  - B) With-LLM harness (persona consistency, memory accuracy, false memory, failure reduction over time)
- **Discussion**: With-LLM eval costs tokens and local LLM variance. How to define interpretation criteria.

## 5. When to Introduce RAG

- **Status**: Open
- **Background**: Roadmap's "Memory store abstraction (SQLite, Vector DB)". When `MEMORY.md`/`principles`/`wiki` grow and grep becomes insufficient.
- **Discussion**: Define trigger (memory size threshold) before introducing. Linked to #1.

---

## How to Add a New Item

Add a new question in the same format. When decided, change **Status** to **Decided** and move implementation to TODO.
