# Changelog

All notable changes to SAKU will be documented in this file.

The format is based on https://keepachangelog.com/en/1.1.0/,
and this project adheres to https://semver.org/spec/v2.0.0.html.

## [Unreleased]

### Fixed

- Delivery to a general audience (`saku say`, #120 stage 2): `saku/speech.py` keeps one core — a reason produces text, the text is delivered, the delivery is recorded — while the recipient changes. `owner` goes to the durable outbox (Web UI / `saku outbox`); a configured agent receives a letter in `<memory>/mail/outbox/` written in the existing mail format (`from: 朔`, `to: <agent>`, no `in_reply_to`: a new thread the agent's reader already understands). Permission is #73's: the recipient must be a configured agent holding the `mail` scope, and an unknown name is refused. A stopped SAKU does not speak (kill switch), text is capped by `[speech] max_chars`, each recipient has an hourly cap, and every send is audited allowed or denied.
- Shared identity, scopes and an audit log (#73): `saku/identity.py` separates naming from permission. Principals are `owner` (the UI token, every scope), `saku` (the daemon acting on its own), configured agents, and `unknown` (no credential: no scopes). Agents come from `[identity.agents.*]`, merged with the existing `[talk] callers` (a talk caller is an agent with the `talk` scope); tokens resolve from an env var or a secrets file, never from a name, and the operator token can never be an agent identity. Every decision is appended to `state/audit.log` as JSONL (who, when, channel, action, allowed, detail) — talk turns, Web UI turns, daemon agent loops and each tool execution with the acting principal. `saku audit --tail N` reads it. The log is a record, not a gate: a write failure never changes an authorization outcome.
- One durable delivery queue for what SAKU says (#120): spontaneous speech and operational notices now go through `saku/outbox.py` instead of the single `state/ui_inbox.json` slot that the next poll consumed, so a message survives a closed browser and a daemon restart. Entries record `delivered_at` (a client saw it) separately from `read_at` (the Owner acknowledged it: replying in the Web UI acknowledges queued speech, notices stay until dismissed), old entries are pruned by `[outbox] max_items` / `retention_days`, and `saku outbox [list|read|clear]` works without the UI. The Web UI lists unread items, can ask for desktop-notification permission, and keeps `/api/proactive` as a legacy single-message view.
- Presence hint (#82): `saku presence [show|present|away|working|sleeping|clear] [--for MIN]` records a manual override (with optional expiry) in `state/presence.json`, and `presence.load()` combines it with configurable quiet hours, work hours and Owner activity (chat.md, today's journal, the UI's last-seen stamp) into one answer plus `can_speak`. The daemon gates spontaneous conversation on it — it stays quiet while the Owner is asleep, and during work hours unless `[presence] speak_while_working` says otherwise — while heartbeat/outage notices stay exempt, because a dead daemon must report itself even at night.
- The MCP contract is explicit (#132): the optional dependency is `mcp>=2.0,<3` (1.x's server API differs and failed the tests), the registry keys tools as `server/name` with the original case name and input schema while keeping an unambiguous bare alias, and the prompt advertises the qualified form. Dispatch accepts a typed JSON body (`{"flag": true, "items": [1,2]}`) or kwargs coerced to the schema's types, validates required fields and types before the call, preserves the tool's case, and refuses an ambiguous bare name instead of guessing. Complete `[[server/name]]` blocks are stripped from visible text; an unclosed `[[link]]` stays prose.
- Web UI turns are serialized per session (#131): the handler wrote the snapshot, ran inference and saved `result.history` without holding a lock, so two requests from one client (a duplicated tab, a retried fetch) both started from the same history and the slower one erased the faster one's exchange. A per-session execution lock now covers the whole turn; different sessions still run in parallel. The light system prompt is also rebuilt each turn instead of at session creation, so the current time and continuity block no longer freeze at the first message.
- One systemd template for both deployment paths (#133): `bootstrap.UNIT_TEMPLATE` now renders the units, and `packaging/saku.service` is that template filled with the documented manual defaults (`MANUAL_UNIT_DEFAULTS`), so the bundled unit carries `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only` and `ReadWritePaths` like a generated one. It also gains `EnvironmentFile=-/etc/saku/saku.env` and `SAKU_MEMORY_ROOT`. A test compares the bundled file with the template output, so the two cannot drift apart again.
- Recall keeps hiragana content words (#134): `recall.terms()` used to drop every kana-only segment and bigram to filter particles, so an everyday question like "いつものこうせいをおぼえてる？" produced no search terms at all. Kana segments up to eight characters and kana bigrams are kept now, with a function-word stop list (particles, auxiliaries, demonstratives) so casual sentences do not match everything; long kana runs stay covered by their bigrams.
- Agent-loop exit semantics (#130): `LoopResult` now carries `finish_reason` (`answered` / `budget_exhausted` / `cancelled` / `error`, plus `incomplete` and `repeated_tool`) and real counts — `llm_calls`, `tool_calls`, `tools_succeeded` (`turns` is now an alias of `llm_calls`). The final-answer rescue also runs when the last turn was a preamble followed by tools, so hitting the turn limit no longer posts "確認します" as the whole reply. The repetition guard compares the tool call *and* its result, so two different operations that return the same success string both run, while an identical call is still stopped.
- Recall distinguishes active from archived memory (#128): `recall.iter_memory_items()` tracks the `## <archive_section>` state inside `MEMORY.md` (the section dreaming decays stale entries into), and `recall()` skips archived entries for ordinary queries while still returning them — labelled `（過去）` and ranked below active knowledge — when the question is about the past (`以前` / `過去` / `old` …). A heading named `Archive` in any other file keeps no special meaning, and `iter_memory_lines()` remains as a compatibility wrapper.
- `saku learn` tells "nothing durable" apart from "the call failed" (#129): extraction returns a status (`ok` / `empty` / `error`; `dreaming.EXTRACTOR_VERSION`), so a valid `[NO_MEMORY]` reply is recorded as processed and stops being re-selected, while a failure is stored with an attempt count and an exponential backoff (`[learning] retry_base_seconds`, capped by `retry_max_seconds`) and retried after that. Each note's state entry records the extractor version, so changing the extraction prompt or its output contract reprocesses the affected notes without touching their hashes.
- The context budget is now enforced before every request (#126): `context.enforce_budget()` counts the system prompt, the request and the recall block together and makes them fit `working_budget_tokens`, shrinking the largest message first (head and tail kept), then dropping the oldest exchanges, and only then shrinking the system prompt itself. Compaction remains the cheap heuristic it was; this is the guarantee behind it, and it also covers `keep_recent_tokens` larger than the budget and a single oversized message. `[context] prune_tool_results = false` now really disables proactive pruning instead of being ignored. `recall()` enforces `max_chars` from the first entry (including the header and the label), truncating a single long memory instead of letting it through unbounded.
- Stream display is chunk-boundary independent (#127): `llm.chat_stream` now feeds an incremental `<think>` scanner (state + undecided tail, no length guesses) and holds back a partially received tool tag, so the same reply split one character at a time renders exactly as it does in one chunk. `chat_stream(..., status=...)` reports `complete`: a stream that ends without `[DONE]` is marked incomplete, and `agent_loop` keeps its text but refuses to run tools from it, so a cut-off generation cannot start side effects.
- Tool dispatch only sees actionable prose (#123): `agent_loop` now passes the *visible* text through `transport.executable_text()`, which drops fenced code, inline code and quoted lines, so a tool call that appears in a think block, a documentation example or a quote no longer runs. A `[[TOOL]]` start tag without `[[END]]` is treated as an incomplete reply and refused instead of auto-closed, so a truncated stream cannot write files; single-bracket calls (#47) are still rescued by closing them explicitly, since that form is complete by design. `exec_tools(..., allowed_tools=...)` (and `run_agent_loop(..., allowed_tools=...)`) give callers a code-level gate, with `set()` meaning tools disabled; the memory-extraction path already bypasses the loop entirely (#121).
- Continuity reads the same entries the writer produces (#125): `persona._memory_items()` now uses `dreaming.parse_entry()` instead of its own stricter regex, so entries carrying `src:` (#79) no longer fall out of the continuity block while their unsourced twins stay in. The metadata comment is parsed field by field (order and unknown keys are irrelevant), so a future field cannot silently drop the body either; legacy entries still parse with `score=None` and stay below the `memory_min_score` floor.
- Path policy now decides on the normalized path (#124): one file is one verdict. `study/../meta.md` used to pass the allow-list (starts with `study/`) and the deny-list (is not exactly `meta.md`), then resolve to `meta.md` and overwrite it; `study/../state/STOP` could be deleted the same way. `normalize_memory_path()` collapses dot segments, treats backslashes as separators, and rejects absolute, quoted and escaping paths, and every file tool (`READ_FILE`, `WRITE_FILE`, `APPEND_FILE`, `MOVE_FILE`, `DELETE_FILE`, `LIST_DIR`) uses it before the filesystem is touched. `PathPolicy.is_delete_allowed()` centralizes the delete rule. Child agent names are validated as identifiers, so `../` cannot place or read a genome outside `children/`.
- A chat turn whose LLM stream closed without producing any content no longer looks like an ordinary empty reply: the call returns `[ERROR] empty reply from '<profile>': the stream ended without content after Ns`, and the daemon logs every failed call (with its reason) instead of only "no text". Provider-side deadlines and rejected credentials were previously invisible in `saku.log`. (#113)

### Added

- Heartbeat (#75): the daemon writes `state/heartbeat.json` every loop (atomic replace, pid, start time, beat count). `saku status` shows how long ago the last beat was and whether that pid is still alive; `saku heartbeat --check` exits non-zero when the beat is stale, dead or missing, which is the hook for an external monitor (systemd timer, cron, another agent). On startup the daemon compares the file with the clock first: if the previous run was down longer than `[heartbeat] max_age_seconds` (default 900s, comfortably above one 270s LLM call), it says so once — log, stdout and Web UI notice — instead of silently pretending the gap never happened. Disable with `[heartbeat] enabled = false`.
- `saku learn` (#68): distil the Owner's Obsidian daily notes into `MEMORY.md`. The notes are read-only; the default run is a dry run that prints what it found, and `--apply` writes. Notes are selected by content hash (`state/learning.json`), so a second run over 200 unchanged notes costs nothing and an edited note is picked up again; `--limit` / `--all` / `--since` / `--note` / `--rebuild` control the range. Promoted entries carry `src:daily/<date>` provenance, `[learning] min_chars` drops template-only notes, and `[learning] redact` masks phone numbers and e-mail addresses before anything is sent to the model or written to memory. Extraction runs under the `learn` LLM role, so the excerpts can stay on a local profile; the shared extractor and the `daily` provenance kind came in #118.
- `saku learn` wiki stage (#68): with `[learning] write_wiki`, the same items are organised into concept notes under `wiki/` — one note per idea, never one per day (the vault's own `tidy --retire-dumps` archives date-stamped dumps). The concept pass is told to reuse an existing note's title verbatim; an existing note is extended under `## 追記` (deduplicated) with `> updated:` bumped, while `> created:` and `> source: daily/<date>` are never rewritten. Titles that would break the wiki (date-prefixed, `MEMORY`, empty or >40 chars) are rejected, sentences carrying phone numbers, e-mail addresses or long digit runs are dropped, and `_index.md` is regenerated once per run. A dry run never calls the concept pass.
- `saku sort` (#72): classify Obsidian `Clippings/*.md` into the reference folders that already exist. The first pass only writes a proposal (`state/sort/<stamp>.json`) and touches nothing else; `--apply [STAMP]` consumes a saved proposal without re-classifying, backs every file up under `state/backup/<stamp>/sort/`, moves only entries at or above `[sort] review_below`, and resolves name collisions with `-2`, never by overwriting. Folders are never created. `saku do "<request>"` routes a Japanese / romaji / English phrase to `sort` / `tidy` / `status` by keyword (no LLM, allow-list only, `--apply` forwarded only when asked), and the `[[SORT_NOTES]]` tool proposes the same way, applying only when `[sort] allow_agent_apply = true`. Classification runs under the `sort` LLM role, so the vault excerpts can be kept on a local profile.

- Dual-stack listening for the Web UI and the MCP server: `[ui] host` / `[mcp.server] bind` may be `"::"`, and the MCP SDK no longer assumes a localhost-only deployment (it answered 421 to any other Host header). cloudflared dials the origin over `[::1]`, which an IPv4-only bind cannot answer.
- Kill switch (#76): while the flag file (`[killswitch] flag_file`, default `state/STOP` under the memory root) exists, autonomous operation stops immediately — the daemon starts no study tick, inbox processing, self-initiation, scheduled task, reflection, dreaming or weekly review, and the mail / talk channels refuse to answer (audit-logged, never silently). Gates stat the flag at action time (self-initiation and mail re-check just before the outward write; autonomous agent loops re-check between turns), so a STOP dropped into the vault by git sync takes effect at the next action. Owner chat replies and git pull/sync are deliberately not gated. `saku stop` / `saku resume` operate the switch (engage/release are logged to `state/killswitch.log`), `saku status` shows its state, and the daemon logs every transition plus one Web UI notice on engagement. The default flag location is outside every tool write-allowed path (and on the write/delete deny lists), so SAKU's own file tools can neither create nor remove it.
- Memory provenance (#79): durable entries now record where they came from, so a fact can later be weighed and traced by its origin (the Owner's words vs an agent's report vs the Web). `MEMORY.md` entries carry a `src:` field inside the existing metadata comment (`<!-- s:3 last:2026-08-30 src:journal/2026-08-30 -->`); dreaming stamps the records a promotion actually read (journal and/or monologue of that date, a candidate set), reinforcement never rewrites the origin, decay moves entries verbatim, and `saku tidy` unions refs when consolidating or merging instead of dropping them. Recall surfaces the recorded provenance as a visible suffix (display only; scoring unchanged), and legacy entries without `src` work exactly as before. The LLM-driven flows (reflection, inbox import, chat archive) are instructed to head newly created `principles/`/`skills/` files with a `> source: <ref>` line, and the daemon logs a warning when a file created by an inbox run lacks one. New module `saku/provenance.py` holds the shared ref vocabulary (`journal/<date>`, `inbox/<file>`, `chat/<date>`, `agent/<name>`, `web`, `owner`, `self`, ...).
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
