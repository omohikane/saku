# ADR: The agent owns its memory; Obsidian is a view

**Status:** Proposed
**Date:** 2026-11-15
**Related:** design principles 4 (file-based memory) and 8 (no hardcoding)

## Context

The intended deployment is an always-on agent on a dedicated VM. The Owner's
Obsidian terminal is a different machine, opened when the Owner feels like it.
Today, though, the memory layer is written as if it lived inside the Owner's
Obsidian vault:

- the memory root is usually a `_saku/` folder, and several tools treat the folder
  name itself as a layout signal (`base.name == "_saku"`);
- the inbox is `memory_root.parent / "00_Inbox"`;
- `SEARCH_NOTES` scans the *parent* of the memory root, i.e. the Owner's personal
  notes, not the agent's knowledge;
- the genome is looked up in a vault-shaped location (`memory_root.parent/identity`).

That works on one machine while the Owner is present. It does not survive a move
to a VM, and it makes the agent's memory a tenant of a directory the Owner
reorganizes whenever they like. Principle 4 asks for *Obsidian-compatible*
Markdown, not *Obsidian-hosted* memory — the distinction was never enforced.

## Problem

1. **Hidden layout contract.** Renaming `_saku/` or moving the memory root silently
   changes what `SEARCH_NOTES` / `READ_FILE` can see. The behavior is a function of
   a folder name, which is not a design principle.
2. **Ownership.** The agent's durable state lives in a directory that exists for the
   Owner's notes. A 24/7 agent must own its storage the way it owns its code.
3. **Growth is unverifiable.** Growth is currently accumulation: journals pile up,
   reflection runs nightly, `evals/longitudinal/` exists — but nothing runs it on a
   schedule, nothing compares snapshots, and nothing feeds the result back into the
   agent. "Self-evolving" is therefore a claim, not a measurement. This is the
   feeling that growth has stalled: there is no observable variable that could move.

## Decision

**The agent's memory is its own repository. Obsidian, the Web UI and MCP are
views over it.**

```
        ┌──────────────────────────────┐
        │  agent core (code, VM)       │
        └──────────────┬───────────────┘
                       │ MemoryBackend (path resolution, scope, git)
        ┌──────────────▼───────────────┐
        │  saku-memory repo (private)  │  canonical, agent-owned
        │  journal/ monologue/ wiki/   │  plain Markdown, [[links]]
        │  MEMORY.md principles/ ...   │
        └───────┬───────────┬──────────┘
                │ git push  │ optional read-only mount
        ┌───────▼──────┐ ┌──▼──────────────┐
        │ VM backup /  │ │ Obsidian vault   │
        │ evals        │ │ (opens the repo) │
        └──────────────┘ └─────────────────┘
```

Key consequences:

- **No layout heuristics.** Remove `base.name == "_saku"`. The readable scope is an
  explicit `[memory] scope_root` (default: the memory root itself). Whether the
  Owner's notes are visible becomes a configuration decision, not a folder name.
- **Obsidian integration becomes an adapter.** Either Obsidian simply opens the
  memory repository as a vault (zero code), or a one-way export copies
  `memory → <vault>/_saku` for people who want the agent's notes next to their own.
  Neither path may be required for the agent to run.
- **Ingest becomes generic.** `00_Inbox` is one adapter of `[ingest] sources`
  (folder, IMAP, RSS, MCP events). A VM has no human dropping files into `00_Inbox`.
- **Channels are explicit.** `chat.md` is a channel, not the interface. On a VM the
  Web UI and MCP are the primary ones; the daemon must run without any vault.

## Growth must be measured, not asserted

Ownership alone does not make the agent grow. The missing loop is:

```
snapshot -> compare -> delta -> reflection prompt -> intention -> next snapshot
```

Concretely:

1. Run `evals/longitudinal/snapshot.py` on a schedule (weekly) and keep snapshots in
   the memory repo.
2. `compare.py` produces a delta; write it to `principles/growth.md` and inject a
   short summary into the nightly reflection prompt.
3. Give each axis a threshold: knowledge (wiki notes and links), memory quality
   (score distribution), skills (tools created and *tested*), behaviour (principles
   referenced), self-model drift (`meta.md` diff). Flat for N weeks = a stall, and a
   stall is a reason to propose a goal in `request_list.md`.
4. Reflection must be able to act on the delta, otherwise it produces prose, not
   growth.

## Migration

| Phase | Change | Risk |
| --- | --- | --- |
| 1 | Create `saku-memory` repository; move the memory root out of the vault; Obsidian opens the repository as a vault (or a one-way export). | Low — data move, no code |
| 2 | Replace `_saku` heuristics with `[memory] scope_root`; make `SEARCH_NOTES` / `READ_FILE` / `LIST_DIR` use it; drop `path.startswith("saku/")` in favour of `[paths] code_root`. | Low — config + tests |
| 3 | Add `[ingest]` adapters over the folder-inbox; make `chat.md` optional in the daemon. | Medium |
| 4 | Schedule snapshots/compare; feed the delta into reflection; add stall detection. | Medium — prompt behaviour |

Phase 2 is the only one that touches existing behaviour, and it can be done behind
a config default that preserves today's semantics (`scope_root = memory root`).

## Open questions

- Does the Owner want the agent's notes visible inside the personal vault at all, or
  is a separate repository enough?
- Should `saku-memory` be a fork/child of the vault repository, or fully separate
  (separate history is simpler and avoids the 1287-commit divergence problem)?
- Is the VM the only writer? If the Owner also edits the memory repo, the same
  rebase-and-retry discipline as `docs/GIT_SYNC.md` applies.
- Which growth axes does the Owner actually care about? Measurement design should
  follow that answer, not precede it.
