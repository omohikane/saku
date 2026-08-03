# Changelog

All notable changes to SAKU will be documented in this file.

The format is based on https://keepachangelog.com/en/1.1.0/,
and this project adheres to https://semver.org/spec/v2.0.0.html.

## [Unreleased]

### Changed

- Integrated legacy `src/` into the `saku/` package: `core.py`, `daemon.py`, `reflection.py`, `tools/`. `src/` removed.
- Tests moved to `tests/`.
- Fixed daemon inbox scanning when `[memory] inbox_dir` points outside the memory root.

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
