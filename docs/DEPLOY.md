# DEPLOY — Running and Deploying

How to run SAKU and deploy it as a systemd service or on a dedicated VM.

## 0. Environment (venv)

System Python may be externally managed (PEP 668) and block pip, so use the **project venv (uv)**:

```bash
uv venv
uv pip install -e ".[mcp]"   # mcp is optional (skip if you don't use MCP servers)
```

Run via venv, e.g. `uv run python -m saku.ui`
(`.venv/bin/python -m saku.ui` also works).

## 1. Manual Run

From the repo root:

```bash
# Web UI + autonomous loop (daemon) together (recommended)
uv run python -m saku.ui

# Options
uv run python -m saku.cli chat      # terminal chat only
uv run python -m saku.cli daemon    # daemon only
uv run python -m saku.cli dream     # run dreaming (memory promotion) manually
uv run python -m saku.ui --no-daemon  # Web UI only (no autonomous loop)
```

Open http://127.0.0.1:8787 in your browser.

## 2. Memory (vault) Configuration

Set the memory location in `config.toml` `[memory] root`:

```toml
[memory]
root = "memory"                          # inside repo (default)
root = "/path/to/vault/_saku/memory"     # absolute path (Obsidian vault etc.)
root = "${SAKU_MEMORY_ROOT}"             # env var (portable across machines)
```

Env vars are expanded as `${VAR}`. Unset vars are left as-is.

Create the structure on first run:

```bash
uv run python -m saku.cli setup            # create at configured [memory] root
uv run python -m saku.cli setup /path/to/vault/_saku/memory   # explicit path
```

## 3. systemd Service (recommended)

A single-process unit for Web UI + daemon is bundled
(`packaging/saku.service`).

```bash
# Prereq: repo at /opt/saku with venv
cd /opt/saku
uv venv
uv pip install -e ".[mcp]"

# Edit the service file (WorkingDirectory / SAKU_MEMORY_ROOT / User etc.)
sudo cp packaging/saku.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now saku

# Check
journalctl -u saku -f
systemctl status saku
```

## 4. Dedicated VM

1. Install Python 3.11+ and uv on the VM (Ubuntu etc.)
2. Clone: `git clone ... saku && cd saku`
3. `uv venv && uv pip install -e ".[mcp]"`
4. `cp config.example.toml config.toml` and set `[llm]` `[memory] root`
5. `uv run python -m saku.cli setup` to create memory structure
6. Use the systemd unit above to run as a service
7. If needed, set `[ui] host = "0.0.0.0"` to expose on the network (secure it)

### Notes on Network Exposure

- Default is `127.0.0.1` (local only)
- For external exposure, use reverse proxy + TLS + auth (current Web UI has no auth)
- MCP server exposure (Phase C) will use token auth

## 5. Backup

Memory is all Markdown, so you can back up / sync with git or Obsidian Sync. `memory/state/` (logs/state) is machine-generated.
