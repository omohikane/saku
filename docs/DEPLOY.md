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

## 1. Bootstrap (recommended)

`saku bootstrap` asks the questions that actually change the deployment and writes
the matching files. It works the same on Arch, Debian and Fedora — only the package
names differ.

```bash
# interactive
uv run python -m saku.cli bootstrap

# non-interactive (server on NetBird, own memory repo, systemd)
uv run python -m saku.cli bootstrap --yes \
  --mode systemd --memory git --memory-path ~/saku-memory \
  --bind netbird --auth token
```

It writes:

| Output | Path |
| --- | --- |
| config | `config.toml` (refuses to overwrite without `--force`) |
| memory | the chosen path, initialised with `saku setup` |
| token | `~/.config/saku/ui.token` (0600, stable across restarts) |
| unit / compose | `packaging/generated/saku.service` or `.../docker-compose.yml` |

Choices:

- **memory**: `local` (inside the checkout), `git` (its own repository — the server
  default), `vault` (an existing Obsidian vault, `<vault>/_saku/memory`).
- **bind**: `local`, `netbird`, `tailscale`, `any` (or `--host ADDR`).
- **auth**: `token` or `none`. `auto` (default) means: no auth on loopback, token
  as soon as a non-loopback address is bound. `--auth none` on a network address is
  refused unless `--insecure` (use it only behind an authenticating proxy).
- **mode**: `systemd`, `docker`, `manual`.

### Arch / EndeavourOS

```bash
sudo pacman -S --needed python git
# optional: docker (or podman + docker-compose)
sudo pacman -S --needed docker && sudo systemctl enable --now docker
```

The generated unit uses the interpreter that ran `bootstrap`, so a venv or a
system Python both work. `ProtectSystem=strict` + `ReadWritePaths` cover the
memory root, the token directory and the checkout — adjust if your memory lives
outside those paths.

### Authentication

- One persistent token, pasted once into the login page; the browser then carries
  an HttpOnly `saku_auth` cookie.
- API clients may send `Authorization: Bearer <token>`.
- `GET /api/health` stays public so a monitor can distinguish "alive" from "down".
- The token file is never committed (it is outside the repo by default) and is not
  printed by the server; the startup banner shows only its path.

## 2. Manual Run

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

## 3. Memory Configuration

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

## 4. systemd Service

The unit is the runtime boundary as well as the supervisor: `User`, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` and `ReadWritePaths` decide what the agent's tools can reach. The rationale and the remaining gaps (EXECUTE_CODE isolation, egress, resource limits) are in `docs/ADR_RUNTIME_BOUNDARY.md`.

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

## 5. Dedicated VM

1. Install Python 3.11+ and uv on the VM (Ubuntu etc.)
2. Clone: `git clone ... saku && cd saku`
3. `uv venv && uv pip install -e ".[mcp]"`
4. `cp config.example.toml config.toml` and set `[llm]` `[memory] root`
5. `uv run python -m saku.cli setup` to create memory structure
6. Use the systemd unit above to run as a service
7. If needed, set `[ui] host = "0.0.0.0"` to expose on the network (secure it)

### Notes on Network Exposure

- Default is `127.0.0.1` (local only)
- Binding a non-loopback address requires the token (see above); a reverse proxy
  that authenticates on its own can be combined with `SAKU_UI_ALLOW_INSECURE=1`
- MCP server exposure uses its own bearer token (`[mcp.server] token`)

## 6. Backup

Memory is all Markdown, so you can back up / sync with git or Obsidian Sync. `memory/state/` (logs/state) is machine-generated.
