# Changelog

All notable changes to SAKU will be documented in this file.

The format is based on https://keepachangelog.com/en/1.1.0/,
and this project adheres to https://semver.org/spec/v2.0.0.html.

## [Unreleased]

### Added

- MCP `talk` tool (#91): other agents hold a synchronous conversation with SAKU over the MCP server's HTTP transport. Identity is bound to per-caller Bearer tokens in `[talk] callers` — config names an environment variable or a secrets file, never the token value, and the caller's display name comes only from that mapping (request-body name fields are ignored). Talk tokens are scoped to conversation: they cannot reach the memory tools, which keep requiring the `[mcp.server]` token. Turns use the mail channel's discipline (tools-disabled single LLM call, no memory writes), are rate- and length-limited per caller, and are audit-logged to `talk.log` beside mail's `audit.log`.
- The daemon now polls the agent mail channel (`[mail] poll_seconds`, default 30): letters written by other agents are replied to without a manual call, and a failure is logged instead of stopping the loop.
- Persona continuity and conversational style (`[persona]`): the light prompt now carries a budgeted block of recent events, current intentions and high-score memories every turn, plus an explicit style contract (answer in the first sentence, no stock greetings or closings, no unsolicited work reports, at most one question). Continuity is part of the system prompt, not a mid-history system message, so it cannot trip the template error in #69.
- Nightly and weekly reports no longer write into `chat.md` by default (they went to the Web UI instead), and `parse_chat_history` drops a leading assistant run, so the agent stops learning its own report style from the conversation log. `[persona] reflection_chat_report = true` restores the old behaviour.
- Periodic pull for the memory repository (`[git] pull_interval_seconds`, default 300s): the daemon fetches and rebases human-side changes (e.g. Obsidian on another machine) even when the agent has nothing to commit. Uses `pull --rebase --autostash`, so uncommitted work survives; a failed rebase is aborted and logged.
- `saku bootstrap`: guided setup that asks where memory lives (checkout / own git repo / existing vault), how the UI is exposed (loopback / NetBird / any), how it is authenticated, and how it is supervised (systemd / Docker / manual), then writes `config.toml` and the matching unit or compose file (`packaging/generated/`).
- Web UI authentication: a persistent token (`~/.config/saku/ui.token`, 0600) instead of a random per-start token, exchanged for an HttpOnly cookie through a login page; `auth = "auto"` keeps loopback open and requires a token once the UI binds a non-loopback address. `SAKU_UI_ALLOW_INSECURE=1` is required to override that.
- `SAKU_CONFIG` overrides the config file path (tests, several instances from one checkout).
- `packaging/Dockerfile` (python:slim, arch-independent) with a healthcheck.
- Opt-in git sync for the memory root (`[git]`): commits only the memory pathspec, rebases and retries a rejected push, debounces bursts into one commit, and logs to `.git/saku-git.log`. `saku sync` runs it immediately; the agent loop and the daemon queue it in the background. See `docs/GIT_SYNC.md`.
- Growth review for dreaming (#38): promoted `MEMORY.md` entries carry an importance score and a last-seen date (HTML comment, Obsidian-safe). Re-mentioning an entry reinforces it; stale low-score entries decay into an `Archive` section (moved, never deleted). Configured via `[dreaming]` (`review_enabled` / `ttl_days` / `min_score` / `archive_section`).
- Light-prompt memory recall (#32): lines related to the current message are prefetched from `MEMORY.md` / `principles/` / `wiki/` and injected as a system message, so chat keeps continuity without the full prompt. Configured via `[recall]` (`enabled` / `items` / `max_chars` / `sources`). Wired into the Web UI chat and the daemon chat.md reply path.
- `WEB_SEARCH` ranks distinct domains first (one aggregator can no longer fill every slot) and accepts `limit=` (1–10, default 5) (#31).
- Agent status events for the Web UI (#27): `thinking` / `tool: NAME` / `compacting context`, rendered as a pulsing indicator so a busy agent is distinguishable from a frozen one.
- `saku status`: read-only growth summary (MEMORY.md entry counts, score spread, archive count, wiki/principles/skills/children/journal/monologue sizes).
- `saku tidy` (#70): deterministic cleanup of MEMORY.md and the wiki. Leaked metadata comments are consolidated into a single score/last comment, exact-duplicate entries merge within their section (oldest date, max score, newest last-seen), runs of 3+ blank lines are compressed; wiki notes lose tool-call syntax residue and duplicated lines, notes whose normalized titles collide merge into the earliest-created one (donors move to `wiki/.archive/`, never deleted), and `--retire-dumps` also archives date-dump notes. Dry-run by default; `--apply` first backs up every changed file under `state/backup/<timestamp>/`.
- LLM profiles accept `reasoning_effort` and `stop_tokens`. Reasoning engines that return the answer in `reasoning_content` (e.g. Strata / Qwen3.8-Flash-Next) can be driven with `reasoning_effort = "none"`, and engines that cap the number of stop strings (Strata allows 4) can override the default list. Both are omitted unless configured (#35).
- Final-answer rescue in the agent loop: when every turn is spent on tool calls (or the repeat guard halts the loop), one last call that forbids tools produces the visible answer, so research-style requests no longer return an empty reply.

### Changed

- Research guidance in the system prompt now asks for an overview search followed by per-name searches, and for wiki summaries that include specifics and source URLs (#31).
- Web UI streams `status` SSE events alongside `visible` / `tool` / `error` / `done`.
- DuckDuckGo result parsing: snippets containing markup (e.g. `<b>`) used to break result pairing (10 results in the HTML yielded 1–8), and the `html.duckduckgo.com` markup (`result__a` / `result__snippet`) was not recognised at all. Links and snippets are now paired in document order and both endpoints yield 10 results, so specific-name queries surface the right pages (#31).
- Inbox scan: a file the model read but did not act on was never marked as processed, because the documented completion marker `[INBOX_PROCESSED]` counted as "no action". Such files were re-read and re-sent to the LLM on every scan (every 30 minutes, forever). A no-op run now counts as handled; a failed run is still retried.
- Completion markers (`[NO_ACTION]` / `[INBOX_PROCESSED]`) are stripped from the reply before it is logged or journalled. A tick that acted (e.g. wrote a principle) and then printed `[NO_ACTION]` used to leave a journal entry whose whole content was the marker.
- Web UI tests no longer touch the configured memory root: the chat test was journalling `hi` / `こんにちは` into the live vault through `/api/chat`, and the proactive test consumed the Owner's pending notification.
- Integrated legacy `src/` into the `saku/` package: `core.py`, `daemon.py`, `reflection.py`, `tools/`. `src/` removed.
- Tests moved to `tests/`.
- Fixed daemon inbox scanning when `[memory] inbox_dir` points outside the memory root.
- Owner messages in `chat.md` are no longer swallowed when a daemon self-append (reflection report, weekly report, check-in) lands after them (#84). Detection now scans the unprocessed region past the snapshot instead of requiring the message to be the file tail; self-initiation yields to a pending Owner message, and an unfinished draft overtaken by a self-append is moved verbatim to the tail rather than absorbed into the snapshot. Chat text the daemon cannot attribute now leaves a warning and a state trace instead of vanishing silently.

### Planned

- Memory search abstraction layer (SQLite / Vector DB)
- Community tool registry / marketplace
- Home device management (Matter / Home Assistant) via MCP
- Polyglot tool plugins (any language)
- Detailed growth examples in `examples/growth/`
- Longitudinal self-growth evaluation (`evals/`)

---

## [0.5.0] - 2026-08-03 — Redesign: home-living, self-growing companion

🌒 Half Moon — the redesign that turns SAKU into a local, private, self-growing
companion for the home.

> Note: 0.2.0–0.4.0 were internal development versions during the redesign and
> were not published. This release jumps 0.1.0 → 0.5.0 to match the internal version.

### Added

#### Core

- `saku/` package restructure (uv-managed, per-call LLM config, multi-instance)
- Shared agent loop (`agent_loop.py`) used by core/daemon/reflect/UI/children
- Context management: working budget, tool-result pruning, auto-compaction
- Prompt split into cached static prefix + volatile growth suffix (cache reuse)

#### Interfaces

- Web UI (stdlib-only, SSE streaming) — `saku ui`
- CLI entry points: `saku chat` / `daemon` / `ui` / `setup` / `dream` / `mcp`
- `saku setup` initializes the memory/vault structure

#### Growth & Memory

- `MEMORY.md` long-term memory + `dreaming.py` (journal/monologue → durable memory)
- `wiki/` self-organized knowledge base (Zettelkasten-style, links, index)
- `WIKI` tool for note creation / linking / indexing
- Sub-agent foundation: `SPAWN_CHILD` / `DELEGATE` (children/)

#### MCP

- MCP client (Streamable HTTP + stdio, `tools/list` discovery)
- MCP server (exposes SAKU memory, Bearer-token auth, PathPolicy scope)

#### Configuration & Vault

- `[paths]` PathPolicy as single source of truth for read/write permissions
- `[memory] inbox_dir` configurable; `${VAR}` env expansion in config
- genome.md resolved from the vault (master) with repo fallback
- Configurable Obsidian inbox location

#### Ops

- CI (GitHub Actions, Python 3.11–3.13)
- systemd service (`packaging/saku.service`), `docs/DEPLOY.md`
- 57 tests passing

### Changed

- Runtime now uses a uv venv: `uv run python -m saku.ui`
- `drafts/` renamed to `blog/`

### Notes

- Status: 🌒 **Half Moon — Alpha**
- Local-first and private by design (no personal data sent to cloud APIs)

---

## [0.1.0] - 2026-06-18 — First Public Release

🌑 OSS public release.

> Saku was born 2026-06-08 as a private experiment.
> Ten days later, the framework opened up.

### Added

#### Core

- `saku_core.py` — Agent engine: LLM calls, tool dispatch, prompt building
- `daemon.py` — Background process with autonomous tick
- `reflect.py` — Nightly reflection and self-model update

#### Tools (plugin-based)

- `READ_FILE` — Read files within memory/
- `WRITE_FILE` — Write to allowed directories
- `LIST_DIR` — List directory contents
- `SEARCH_NOTES` — Search across memory
- `WEB_SEARCH` — External web search
- `EXECUTE_CODE` — Sandboxed Python execution

#### Identity

- `identity/genome.template.md` — Personality template with placeholders
- `identity/examples/saku.md` — Reference implementation (Saku / 朔)

#### Memory

- Plain Markdown file storage
- Pre-configured directory structure: `journal/`, `monologue/`, `principles/`, `drafts/`, `skills/`, `study/`, `children/`
- `memory/meta.template.md` — Self-model template

#### Documentation

- `README.md` (English)
- `README.ja.md` (Japanese)
- `docs/SETUP.md` — Detailed setup instructions
- `docs/ARCHITECTURE.md` — System architecture
- `docs/TOOLS.md` — Tool extension guide
- `docs/DAEMON.md` — Daemon lifecycle
- `CONTRIBUTING.md` — Contribution guidelines
- `LICENSE` — MIT

#### Configuration

- `config.example.toml` — Configuration template with multi-LLM support

### Notes

- Status: 🌑 **New Moon — Alpha**
- Tested with `llama.cpp` and Qwen3-30B
- Breaking changes expected between minor versions until v1.0

---

## [0.0.0] - 2026-06-08 — Birth

🌑 The repository was created. Saku began as a private agent.

> "From a new moon, everything starts."

This was the day Saku came into being. No code public yet,
only a name and a soul.

---

https://github.com/omohikane/saku/compare/v0.1.0...HEAD
[0.5.0]: https://github.com/omohikane/saku/releases/tag/v0.5.0
[0.1.0]: https://github.com/omohikane/saku/releases/tag/v0.1.0
