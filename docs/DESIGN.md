# SAKU Redesign Plan (Vision Integrated)

> This document records the redesign's design policy. Implementation proceeds
> phase by phase on the `dev` branch. Data (`memory/` `identity/`) is not changed;
> only code is reorganized into the new structure.

## Vision

**A self-growing partner AI that lives at home.**

- **Home device & infra management**: NOC/SOC (network monitoring/anomaly detection) plus
  **Matter / Home Assistant smart-home management**
- **Privacy**: no personal data sent to cloud APIs. Runs entirely on local LLM
- **Growth**: daily logs, reflection, and learning (dreaming/wiki) to grow as your dedicated companion
- Saku judges its own capability gaps, spawns child agents, and builds abilities
- Multiple local LLMs are used selectively and run as sub-agents

## Design Principles

1. **Core = Python (uv), Tools = Polyglot**
   - Separate core language and tool language. Tools can be written in any language (JSON protocol)
2. **Context Management under VRAM/RAM Constraints**
   - Keep context "small + on-demand recall + compression". Full history stays on disk
   - Working budget per `[llm.instances.*]` (varies by model)
3. **Prompt Separation**
   - Fixed prefix (identity/genome/capabilities) + volatile suffix (time/state)
   - Keep the prefix stable for llama.cpp cache reuse / API prefix-caching
4. **File-Based Memory (Markdown)**
   - Git-tracked, Obsidian-compatible, plain text
5. **MCP Bidirectional**
   - Client (external service connection + dynamic tool discovery) + Server (expose externally)
   - Auth via token + existing scope constraints
6. **Approval Boundary**
   - Reads = automatic, destructive ops = require Owner approval via `request_list.md`
7. **LLM Config per Call**
   - No global. Multi-instance and per-child-agent LLMs possible
8. **No Hardcoding — Configurable via Config and Chat**
   - Avoid fixed paths/thresholds/destinations. Make them configurable via `config.toml` (`[wiki] root` `[memory] inbox_dir` `[plugins] root` etc.) with `${VAR}` expansion
   - Tools accept `root`/`path` args to address any vault location, so the AI can choose via chat
   - New features are configurable from day one, not "hardcode now, make configurable later"
9. **Language**
   - Code, comments, internal docs, prompts, and agent-facing docs are English. User-facing docs (e.g. `README.ja.md`, vault notes) remain Japanese

## Target Structure

```
saku/                     # package (former src/)
  cli.py                  # interactive / daemon / ui / mcp serve
  config.py               # settings+validation ([context][llm.instances][mcp][ui][ops])
  llm.py                  # chat_stream(messages, llm_cfg) / profiles / instances
  agent_loop.py           # shared loop (Saku + children, LLM config via args)
  context.py              # working budget, prompt fixed/volatile split, compaction, tool pruning
  memory.py               # file memory access layer (incl. MEMORY.md)
  dreaming.py             # short-term signals → scoring → MEMORY.md/wiki promotion
  transport.py            # [[TOOL]] + polyglot + MCP conversion/dispatch
  mcp_server.py           # expose memory/tools/chat via MCP (token+scope)
  channels/               # channel abstraction
    base.py               # send / receive / state
    chatmd.py             # file-based (legacy, optional)
    webui.py              # Web UI (SSE)
    discord.py            # Discord (future)
  daemon.py               # scheduler (monitoring/compaction/dreaming/reflect)
  reflect.py              # nightly reflection
  tools/                  # built-in tools (former system_tools/)

memory/
  MEMORY.md               # long-term memory (new)
  meta.md                 # self-model (kept)
  wiki/                   # self-organized knowledge base (new, Phase D)
  children/<name>/        # child agents (identity+manifest+LLM)
  journal/ monologue/ principles/ skills/ study/ chat.md ...
```

## Channel Abstraction

- `[channels] enabled` selects send/receive destinations (webui / chatmd / discord)
- `[channels] proactive` selects where proactive messages/alerts are sent
- Proactive messages, alerts, and request_list escalations go to the configured channel
- chat.md remains the default legacy channel for the migration period, can be disabled later
- Each channel has its own state

## Memory Layers (3)

```
① raw (chronological)      journal/ monologue/
② distilled (durable)      wiki/ principles/ skills/
③ self-model               meta.md MEMORY.md
```

- `wiki/` = one concept per note (Zettelkasten) + `[[links]]` + tags + source + index
- dreaming/reflection promote to ② and ③
- Importance scoring (surprise-metric-like), decay (TTL review), consolidation (link update), digest hierarchy

## Phases

### Phase A — Foundation
- `saku/` packaging (pyproject.toml + uv)
- `config.py`: `[context]` `[llm.instances]` `[mcp]` `[ui]` `[ops]` `[channels]`
- `llm.py` per-call refactor (remove global)
- Split modules while keeping existing behavior (saku_core/daemon/reflect)

### Phase B — Conversation
- `agent_loop.py` shared (Saku/daemon/reflect/UI)
- `context.py`: prompt separation, working budget, tool pruning, auto-compaction
- Channel abstraction + Web UI (stdlib + SSE streaming, tool display, Markdown)
- Keep chat.md as legacy channel

### Phase C — Capabilities
- Polyglot tools: `tool.toml` manifest + JSON protocol
  - Python = in-process, others (node/shell/rust) = subprocess
  - EXECUTE_CODE with language param
- MCP bidirectional: client (external server + tools/list discovery) + server (token+scope)

### Phase D — Growth
- Child agents: `memory/children/<name>/` with identity + `llm` pin
  - `SPAWN_CHILD` / `DELEGATE` to spawn/delegate
  - Recursively run `agent_loop` with child's identity/scope/LLM
- Memory layers: `MEMORY.md` + `wiki/` + `dreaming.py`
- Self-improving skills (auto-generate procedures to skills/)

### Phase E — Operations (Home NOC/SOC)
- Monitoring tools (liveness/resources/log analysis) + `[ops]` approval boundary
- Alert routing (selected channel + request_list escalation)
- Incident logging → reflection → lessons to principles/
- **Monitoring ≠ LLM**: health checks are fast non-LLM path, LLM only for analysis/incident
- Roll out incrementally from one host, expand via tools+config
- Child division example: NOC "watcher" / SOC "guardian" / backup "gardener"

## Cross-Cutting Concerns

1. Compatibility/migration for existing tools (`memory/tools/*.py` `run(base,path,body,**kwargs)`) and tests
2. Daemon vs Web UI mutual exclusion (serial loop vs llama.cpp slot parallelism)
3. chat.md vs Web UI write collision (solved via channel abstraction)
4. Credential handling (`[ops]` SSH keys/tokens, gitignore/scope separation)
5. EXECUTE_CODE sandbox hardening with polyglot (timeout+scope+network block, Docker future)
6. Tests without LLM (mock chat_stream)
7. Per-instance context budget
8. Retry/degraded mode and child failure propagation
9. Docs update (README ja/en, ARCHITECTURE, quickstart `saku` CLI)
10. Future: SQLite FTS5 memory search / MCP server TLS / external push / Docker sandbox

## Core Features to Keep

| Feature | Current | New Location |
|---|---|---|
| Blog writing | System prompt "Blog Publishing Workflow" + skills/blog_writing.md + request_list approval | Keep in prompt fixed prefix |
| Monologue | Daemon tick appends to monologue/YYYY-MM-DD.md | Keep daemon schedule, source for dreaming |
| Proactive ask | check_autonomous_initiation / saku_self_initiate | Move to channel abstraction (proactive destinations) |

## References

- Hermes Agent (Nous Research): skill self-improvement loop, prompt layers (stable→context→volatile)
- OpenClaw: compaction/pruning/context engine, Dreaming promotion, MCP bidirectional, channel Gateway
- Google Titans / Memory Bank: surprise metric, TTL decay, consolidation
- A-MEM: Zettelkasten for agent memory (wiki basis)

---

## Implementation Status (dev branch)

| Phase | Content | Status |
|---|---|---|
| A | `saku/` packaging, `config.py`, `llm.py` per-call | ✅ Done |
| A-2 | Legacy `src/` → `saku/` integration, `tests/` | ✅ Done |
| B-1 | `agent_loop.py`, `context.py`, `transport.py`, `thinking.py` | ✅ Done |
| B-2 | Prompt fixed prefix / volatile suffix split (static cache) | ✅ Done |
| B-3 | Web UI (stdlib, SSE) + daemon proactive→UI | ✅ Done |
| — | Ops: `saku setup`, env expansion, systemd/DEPLOY, README | ✅ Done |
| — | Output: hide tool syntax, loop prevention | ✅ Done |
| B-4 | Channel abstraction (`saku/channels/` / chatmd split) | Deferred (when Discord). Used for dialog/growth/processing separation |
| C-1 | Polyglot tools | Deferred (Python sufficient for local LLM for now) |
| C-2 | MCP client (external server + tools/list discovery) | ✅ Done |
| C-3 | MCP server (expose memory/tools via Bearer + PathPolicy) | ✅ Done |
| D-1 | `MEMORY.md` + `dreaming.py` (journal/monologue→scored→promotion) | ✅ Done |
| D-2 | `wiki/` self-organized KB (create/link/index) | ✅ Done |
| D-3 | Child agent infra (`SPAWN_CHILD`/`DELEGATE`, `children/`, depth guard) | ✅ Done (infra; autonomous use incremental) |
| E | Home NOC/SOC | Not started |

## Dialog / Growth / Processing Separation (2026-08)

Diagnosis: chat and inbox both sent the same full self-model (~7650 tokens) every time, contradicting "quick lightweight chat".

```
Dialog (chat)     Light prompt (soul/genome + time only)
                    Heavy memory recalled async after. Prioritize immediacy.
Growth (growth)   dreaming / reflection / wiki — not just accumulation but consolidation
                    Add importance scoring, TTL review, link consolidation.
Processing        Heavy analysis/long docs delegated to external LLM / sub-agent / skill
                    Cover local LLM (16GB VRAM, 32768 ctx) limits via delegation.
```

- **Dialog**: keep full model but add new light prompt (soul/genome+time only). No memory/principles in chat; fetch on demand via tools.
- **Growth**: current dreaming only promotes journal/monologue → MEMORY.md, without整理/consolidation/decay. Enhance it.
- **Processing**: local LLM cannot handle long analysis. Delegate heavy work to sub-agents (`memory/children/`) or external profiles (`config.toml [llm.profiles]`). e.g. inbox via cloud model, chat stays local.
- This enforces the top design principle (keep context small).

## Priority (revised 2026-08)

Context bloat (principles 91KB) showed the char-limit is a band-aid. "Organize memory" must come before "add capabilities".

1. ~~**D-1 Memory**: `MEMORY.md` + `dreaming.py`~~ ✅ Done
2. ~~**D-2 KB**: `wiki/` (Zettelkasten + links)~~ ✅ Done
3. **D-3 Child agents**: `children/` + `SPAWN_CHILD`/`DELEGATE`
4. **C-2 MCP client**: external server (prereq for home integration / Phase E)
5. **C-3 MCP server**: token+scope
6. **B-4 Channel abstraction**: when Discord etc. needed
7. **C-1 Polyglot**: deferred

Web UI is zero-dep (http.server + SSE). See commit log for details.
