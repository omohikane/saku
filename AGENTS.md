# AGENTS.md — SAKU Project Rules

Additional rules for this repository, on top of the global `~/.config/opencode/AGENTS.md`.

## Language

- **Code, comments, internal docs, and agent-facing docs are English.** User-facing docs (e.g. `README.ja.md`, vault notes) remain Japanese.
- This file, `docs/DESIGN.md`, `docs/ARCHITECTURE.md`, etc. are English except where the user directly edits.

## Tests

- **Always run tests after code changes**:
  ```bash
  cd tests && for t in test_*.py; do uv run python "$t"; done
  ```
  (`test_mcp_server.py` is a stdio fixture, excluded. Include `tests/security/`.)
- CI (GitHub Actions) runs all tests.

## Personal Data Protection

The following are **user's personal data** — do not modify without explicit instruction:

- `memory/` (Obsidian vault: journal / monologue / principles / skills / wiki / MEMORY.md / meta.md etc.)
- `config.toml` (local, `.gitignore`'d)
- `identity/genome.md` (persona definition; master is in the vault)

## Commit Conventions

Follow the existing log with **type prefixes**:

- `feat:` feature / `fix:` bugfix / `docs:` docs / `refactor:` refactor (no behavior change) / `test:` tests / `chore:` chores / `ci:` CI

One logical change per commit. Do not mix behavior changes and refactors.

## Branching

- Working branch is **`dev`**. `main` is release.
- Non-trivial tasks: topic branches (`feat/*` `refactor/*`), merge to `dev` after tests pass.
- `main` is updated via PR only when the user instructs.

## Issue Workflow (even for solo dev)

File a **GitHub Issue** before fixing/investigating/designing:

- Bug: steps + error → `fix/*` branch → PR with `Closes #N` to `dev`
- Research/design (`question` label): leave findings + next steps as comments
- Trivial one-line/typo fixes may be committed directly without an issue
- Push is manual (deny). PRs only by user or explicit instruction.

## Runtime

- Run via venv (uv): `uv run python -m saku.cli <cmd>`
- LLM settings are in `config.toml` `[llm]`. Do not change without instruction.

## Development Policy

- **No hardcoding**: avoid fixed paths/thresholds/destinations. Make them configurable via `config.toml` (`[wiki] root` `[memory] inbox_dir` `[plugins] root` etc.) and via chat (tool args). Configurable from day one (see `docs/DESIGN.md` 8).
