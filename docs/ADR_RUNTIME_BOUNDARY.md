# ADR: Runtime boundary — a dedicated host, hardened by systemd

**Status:** Accepted (partially implemented)
**Date:** 2026-11-18
**Related:** issue #36, `docs/DEPLOY.md`, `packaging/generated/saku.service`

## Question

Where does SAKU's runtime boundary live, and is a dedicated machine the right
sandbox? `EXECUTE_CODE` runs a subprocess in `study/` with a timeout and a path
scope — no container, no VM, no egress control.

## Decision

**A dedicated host (physical or VM) is the runtime boundary. Inside it, the
systemd unit is the enforcement layer.**

This is deliberately the cheaper of the two options in issue #36: the isolation
comes from the unit's hardening rather than from per-execution containers. The
trade-off is accepted because the agent is single-user and the machine holds
nothing else of value.

Concretely, the unit now sets:

| Setting | Effect |
| --- | --- |
| `User=<owner>` | the agent runs as an unprivileged user |
| `NoNewPrivileges=true` | no setuid/escalation from tools or subprocesses |
| `ProtectSystem=strict` | `/usr`, `/etc`, `/boot` are read-only |
| `ProtectHome=read-only` | home directories are read-only except the listed paths |
| `PrivateTmp=true` | the tool sandbox cannot see the host's `/tmp` |
| `ReadWritePaths=<memory> <token-dir> <repo>` | the only writable locations |

The LLM stays external (`LlmConfig.api_url`, e.g. a LiteLLM gateway on another
host), so the model can be swapped or moved without touching the boundary. Memory
stays plain Markdown in a git repository, so the boundary contains no database
that would need its own backup story.

## What this answers from #36

- **Where the runtime lives**: one always-on host, started at boot by systemd,
  reachable over a VPN (NetBird/Tailscale) with token-authenticated Web UI.
- **LLM separation**: already true (`saku/llm.py` is network-based, no globals).
- **Memory separation**: true — git-tracked Markdown, pushed and pulled by the
  agent itself (`[git]` sync + `pull_interval_seconds`).
- **Why not per-run containers**: `EXECUTE_CODE` already has a timeout and a path
  scope; inside an already-hardened host, a container per run buys little for a
  single-user agent and costs an image pull in the hot path.

## What this does NOT answer

1. **`EXECUTE_CODE` is still same-privilege.** It runs as the service user inside
   `study/`, so it can read and write everything `ReadWritePaths` allows and can
   reach the network. A container or `bubblewrap` wrapper is still the right next
   step if untrusted code is expected.
2. **No egress policy.** A shell-capable agent needs a defined network boundary;
   `[ops]` (auto-allowed vs approval-required actions) is still only a config
   sketch.
3. **No resource limits.** A runaway subprocess is bounded by the 5s timeout in
   `EXECUTE_CODE`, but not by cgroups (no `MemoryMax`/`CPUQuota` in the unit yet).
4. **Approval boundary** (`request_list.md`) is a convention, not enforcement.

## Consequences

- Deployment is one `saku bootstrap` + `systemctl enable --now` (see
  `docs/DEPLOY.md`), not a container orchestrator.
- A compromise of the agent is bounded by the writable paths above — but it is
  not contained to a throwaway filesystem.
- Moving to a stronger boundary (container, bwrap, microVM) is a change to the
  unit/ExecStart, not to the agent code.

## Follow-up work

- Wrap `EXECUTE_CODE` in `bwrap`/`systemd-run --scope` with `MemoryMax` and no
  network, or run it in a disposable container.
- Implement `[ops]` so destructive actions are routed to the approval boundary.
- Add execution logging to a Phase E incident log.
