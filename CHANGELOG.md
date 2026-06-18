# Changelog

All notable changes to SAKU will be documented in this file.

The format is based on https://keepachangelog.com/en/1.1.0/,
and this project adheres to https://semver.org/spec/v2.0.0.html.

## [Unreleased]

### Planned

- Memory search abstraction layer (SQLite / Vector DB)
- Web UI for conversation (replacing `chat.md`)
- Multi-agent coordination
- Long-term memory compression
- Detailed growth examples in `examples/growth/`

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
[0.1.0]: https://github.com/omohikane/saku/releases/tag/v0.1.0
