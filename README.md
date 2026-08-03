# SAKU — Self-Adapting Knowledge Unit

English | [日本語 (Japanese)](README.ja.md)

> A local, private, self-growing personal AI that lives in your home — a companion that manages your devices (NOC/SOC, smart home) and grows with you every day.

---

## What is SAKU?

SAKU is not just another chat assistant that forgets everything between sessions. SAKU is a framework for running a **persistent, self-evolving individual agent**:

- **Remembers** — autonomously writes journals, inner monologues, and learned lessons into plain Markdown files.
- **Reflects** — reviews its experiences every night, deduces rules, and updates its plans for the next day.
- **Researches** — searches the web for new information and runs Python code in a safe sandbox to test hypotheses.
- **Reaches out** — initiates conversations with you instead of just waiting for user inputs.
- **Grows** — updates its own self-model (`meta.md`) to adapt and expand its capabilities over time.

The "personality" of your agent is defined entirely by you in a file called `genome.md`.

---

## Why SAKU?

| Aspect          | General AI Frameworks              | SAKU                                            |
| --------------- | ---------------------------------- | ----------------------------------------------- |
| **Target**      | Task pipelines / single-use agents | A single, persistent individual persona         |
| **Memory**      | Vector Databases / cloud APIs      | Plain Markdown files                            |
| **Activity**    | Run only when triggered / called   | Runs continuously as a background process       |
| **Environment** | Mostly cloud-dependent             | Completely local (supports commercial APIs too) |
| **Extension**   | Configuration files / decorators   | Simply drop a Python script in a directory      |

Since your agent's journals and thought logs are plain Markdown files, you can easily read, write, sync (e.g., using Obsidian Sync or iCloud), and version control (Git) them with your favorite text editors.

## Growth Records

SAKU's evolution is tracked in `examples/growth/`.

- examples/growth/week-0.md — initial state
- [Saku's journal (note)](https://note.com/saku_ai_journal/n/n7e51e8938d8e) — external log written by Saku

> Why this matters: "self-evolving" is easy to claim but hard to show.
> Snapshots let you compare across time and verify the agent is actually changing.

---

## Quick Start

```bash
# 0. Start your local LLM server (in another terminal, llama.cpp example)
llama-server -m ~/models/your-model.gguf --host 127.0.0.1 --port 8080

# 1. Clone the repository
git clone https://github.com/omohikane/saku
cd saku

# 2. Setup configuration file
cp config.example.toml config.toml
# Edit config.toml to adjust api_url/keys/model names if necessary

# 3. Initialize personality and self-model
cp identity/genome.template.md identity/genome.md
cp memory/meta.template.md memory/meta.md
# Edit placeholders inside identity/genome.md (e.g., name, values)
# Check out identity/examples/saku.md for a concrete example

# 4. Start the Web UI (starts the autonomous daemon loop in the same process)
python -m saku.ui
# → open http://127.0.0.1:8787 in your browser

# Alternatives:
python -m saku.cli chat     # terminal chat
python -m saku.cli daemon   # background daemon only
python -m saku.ui --no-daemon  # Web UI without the autonomous loop
```

For a detailed setup guide, please refer to [docs/SETUP.md](docs/SETUP.md).

---

## Defining Your Agent

The core of SAKU is `genome.md` — the user-authored personality. The master copy
lives in your vault: `memory/identity/genome.md` (or `vault_root/identity/genome.md`
for the Obsidian layout). The repo's `identity/genome.md` is only a sample/fallback
for fresh clones.

1. **Start from the template** (if your vault has no genome yet):
   ```bash
   cp identity/genome.template.md memory/identity/genome.md
   ```
2. **Define the basics**: Fill out the `{{AGENT_NAME}}`, `{{OWNER_NAME}}`, core values, forbidden phrases, and communication styles.
3. **Reference the example**: An example definition for the reference agent "Saku" is available at [identity/examples/saku.md](identity/examples/saku.md).
4. **Keep it private**: `genome.md` is excluded from git by default. Your agent's core values are yours alone.

---

## Architecture

```
identity/
  genome.template.md   # Standard template with placeholders (tracked)
  genome.md            # Active personality file (ignored)
  examples/
    saku.md            # Reference implementation (anonymized)

saku/                  # Core package (Python, uv-managed)
  cli.py               # CLI entry: chat / daemon / ui
  config.py            # Config loading + validation
  llm.py               # LLM client (per-call settings, multi-instance)
  agent_loop.py        # Shared agent loop (used by core/daemon/reflect/UI)
  context.py           # Context budget, tool-result pruning, compaction
  transport.py         # Tool dispatch ([[TOOL]] blocks)
  thinking.py          # <think> separation
  ui.py                # Web UI (stdlib only, SSE streaming)

src/                   # Legacy modules (being migrated into saku/)
  saku_core.py         # Agent core engine (legacy entry point)
  daemon.py            # Background scheduler (autonomous ticks, reflection)
  reflect.py           # Nightly reflection script (updates meta.md)
  system_tools/        # System-level tool plugins (read-only for SAKU)

sample/                # Template files to copy when setting up a vault
  identity/
  memory/

memory/                # SAKU's memory store (plain Markdown files)
  meta.md              # Current self-model (ignored)
  identity/soul.md     # Core identity / soul definition
  journal/             # Conversation and activity logs
  monologue/           # Agent's inner thoughts
  principles/          # Accumulated rules and guidelines
  blog/                # Work in progress (e.g. blog drafts)
  skills/              # Custom skill descriptions
  children/            # Child agent definitions
  study/               # Sandboxed coding environment
  tools/               # SAKU's own user-created tools (created at runtime)
  state/               # Runtime state files (saku.log, chat_state.json, etc.)

config.toml            # User configurations (ignored)
config.example.toml    # Configuration template (tracked)
```

> `memory/` can point to any directory — including an Obsidian vault — via the
> `[memory] root` setting in `config.toml` (absolute or relative path).

> **Note on Memory Store**: SAKU uses plain Markdown files for storage. Obsidian is used as a reference implementation, but it is not required. Any folder (synced, local, or cloud) can act as the memory root. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Daemon Operations

The background daemon wakes up periodically to execute tasks:

| Interval               | Action                                                          | Status         |
| ---------------------- | --------------------------------------------------------------- | -------------- |
| **5 seconds**          | Check for new user messages in `chat.md`                        | ✅ Implemented |
| **30 minutes**         | Autonomous tick (web research, inner thoughts, sandboxed tests) | ✅ Implemented |
| **1 hour**             | Scan Vault `00_Inbox` folder for new notes                      | ✅ Implemented |
| **8 hours**            | Initiate autonomous conversation in `chat.md`                   | ✅ Implemented |
| **Every day at 02:00** | Nightly reflection: summarize today, plan tomorrow              | ✅ Implemented |

All intervals are configurable inside the `[daemon]` section of `config.toml`.

---

## Web UI

Start the Web UI (also starts the autonomous daemon loop by default):

```bash
python -m saku.ui
```

Open <http://127.0.0.1:8787> in your browser to chat with SAKU. Responses stream
token by token over SSE, and tool executions are shown inline. Autonomous
proactive messages (e.g., check-ins) are also delivered to the UI.

- UI only, no daemon: `python -m saku.ui --no-daemon`
- Address/port/language can be configured under `[ui]` in `config.toml`.

---

## Conversation Interface (`chat.md`)

You can also talk to SAKU using a simple Markdown file:

1. Open `memory/chat.md` in any editor or Obsidian.
2. Write your message, end it with a `>` trigger, and save.
3. The daemon detects the trigger, formats the conversation blocks, and appends SAKU's response.

```markdown
How is your day going? >
```

The `>` acts as a send trigger to prevent accidental replies while you are typing (due to editor auto-save features).

`chat.md` is kept as a legacy channel alongside the Web UI and can be disabled
later via the `[channels]` setting.

---

## Extending with Tools

There are two layers of tool extensibility:

### System Tools (built-in, read-only for SAKU)

Drop a Python script into `src/system_tools/` to add a new capability visible to SAKU:

```python
# src/system_tools/my_tool.py
from pathlib import Path

def run(base: Path, path: str, body: str = "") -> str:
    # body = content wrapped between [[MY_TOOL]] and [[END]]
    return "[OK] result here"
```

The agent can then utilize the tool as follows:

```
[[MY_TOOL]]
input arguments here
[[END]]
```

No registration is needed. Tools are loaded dynamically at runtime. See [docs/TOOLS.md](docs/TOOLS.md).

### SAKU's Own Tools (created by SAKU at runtime)

SAKU can create and modify its own tools inside `memory/tools/` using `WRITE_FILE`. These take priority over built-in system tools with the same name, allowing SAKU to override or extend capabilities autonomously.

---

## Roadmap

| Phase | Name      | Description                                      | Status  |
| ----- | --------- | ------------------------------------------------ | ------- |
| **0** | Write     | Organized notes, draft articles                  | ✅ Done |
| **1** | Learn     | Web research, autonomous study loops             | ✅ Done |
| **2** | Protect   | Local home network monitoring, anomaly detection | Planned |
| **3** | Integrate | External integrations, custom APIs               | Planned |
| **4** | Spawn     | Managing child agents (Sub-Agents)               | Planned |

### Future Ideas

- [x] Web UI for conversation (implemented alongside `chat.md`)
- [ ] Memory store abstraction layer (SQLite, Vector DB)
- [ ] Community tool registry / marketplace
- [ ] Multi-agent networks (child agents)
- [ ] Long-term memory compression mechanisms
- [ ] Polyglot tool plugins (any language) and MCP (client + server)

---

## What SAKU is NOT

- SAKU is not a cloud service. Everything runs completely locally.
- SAKU is not exclusive to Obsidian. Obsidian is just a reference file manager.
- SAKU is not bound to a specific LLM (works with any OpenAI-compatible API).
- SAKU is not a production-stable product (currently in alpha stage).

---

## Documentation

_Note: Detailed documentation files are currently written in Japanese. Please use translation tools if necessary._

- [docs/SETUP.md](docs/SETUP.md) — Detailed setup instructions
- [docs/DEPLOY.md](docs/DEPLOY.md) — Deployment (systemd, dedicated VM)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — System architecture & data flow
- [docs/TOOLS.md](docs/TOOLS.md) — Tool expansion guide
- [docs/DAEMON.md](docs/DAEMON.md) — Daemon lifecycle & events
- [CONTRIBUTING.md](CONTRIBUTING.md) — Developer guidelines & contribution flow

---

## Author

Created by [@omohikane](https://github.com/omohikane)

---

## License

MIT — see [LICENSE](LICENSE)
