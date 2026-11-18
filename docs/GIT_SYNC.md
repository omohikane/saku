# Git Sync — keeping the memory root in a repository

SAKU's memory is plain Markdown. That makes a git repository the natural durability
layer: the agent commits what it wrote, pushes, and every other machine (an
Obsidian vault on a laptop, a backup host) follows with a plain `git pull`.

This document describes the opt-in `[git]` sync implemented in `saku/vault_git.py`.

## Why it is opt-in

The default memory root is `memory/` inside the SAKU checkout. Enabling sync there
would commit agent journals into the code repository. `[git] enabled` therefore
defaults to `false`, and you enable it only when the memory root lives in its own
repository (for example `your-vault/99_System/saku/`).

## What a sync does

1. Resolve the repository root: `[git] root`, or `git rev-parse --show-toplevel`
   from the memory root.
2. Stage **only the configured pathspec** — by default the memory root itself.
   `git add -A` would sweep the Owner's in-progress Obsidian edits into the
   agent's commit.
3. If something is staged: commit with `saku: memory update <date>` and the file
   list in the body.
4. If `push = true`: `git pull --rebase --autostash`, then `git push`, retried up
   to three times. A rejected push (another machine pushed first) is resolved by
   the rebase, never by a force push.
5. Log the outcome to `.git/saku-git.log` (inside the git directory, so the log
   itself is never committed).

## Pulling human-side changes

A sync only pulls when the agent has something to commit. On a server that runs
for weeks, the human side keeps moving on its own: Obsidian is a sync system, so
notes written on another machine arrive as commits. The daemon therefore also
runs `pull_if_due` every `[git] pull_interval_seconds` (default 300s):

- `git fetch` first, then `rev-list --count HEAD..FETCH_HEAD` — a cheap
  up-to-date check that costs no rebase;
- `git pull --rebase --autostash` when behind, so the agent's uncommitted work is
  stashed and restored instead of blocking the pull;
- a failed rebase is aborted and logged; the next interval retries.

This is the "fetch on update, push on write" loop: the agent pulls what humans
changed, and pushes what it wrote. Both directions are debounced and share one
lock, so the chat loop never waits on git.

Sync is debounced by `debounce_seconds` (default 120): a burst of tool writes
becomes one commit. The agent loop queues the sync on a background thread, so a
push never delays a reply.

## Setup

`config.toml`:

```toml
[memory]
root = "../vault/99_System/saku"   # relative to config.toml, or absolute
inbox_dir = "../vault/00_Inbox"

[git]
enabled = true
root = "../vault"                  # repository root (the vault itself)
ssh_command = "ssh -F ~/.ssh/config"  # only if the system ssh config is broken
```

Then:

```bash
saku setup        # create the memory structure inside the vault
saku sync         # commit + push now (ignores the debounce window)
```

Add a `.gitignore` inside the memory root so runtime state does not churn the
history:

```gitignore
state/
study/
__pycache__/
```

## Running next to the Obsidian Git plugin

It is fine for both the plugin and SAKU to push, as long as pushes are retried
after a rebase — that is what this module does. Two rules keep it calm:

- **One writer per file.** SAKU only stages its own memory root; the plugin
  commits everything else. Do not point `[git] paths` at the whole vault.
- **Expect the plugin to win races.** It commits every 10 minutes by default. A
  commit it already made simply leaves SAKU with "nothing to commit", which is a
  successful no-op.

If the plugin's push interval causes frequent rejections, either raise the plugin's
interval or set `[git] push = false` and let the plugin own the push — commits
still accumulate locally.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Bad owner or permissions on /etc/ssh/ssh_config.d/...` | System ssh config is unreadable; set `[git] ssh_command = "ssh -F ~/.ssh/config"` instead of editing system files. |
| `another sync is running` | A previous sync holds `.git/saku-sync.lock`. The lock is treated as stale after `3 × timeout_seconds`. |
| `memory root ... is outside repository ...` | `[git] root` and `[memory] root` disagree; the memory root must be inside the repository. |
| `not a git repository` | The memory root is not under a repository and `[git] root` is unset. |
| Push keeps failing | Read `.git/saku-git.log`; it records each attempt with the git error message. |

## Security

The memory root is personal data. Use a **private** repository, keep secrets out
of the vault, and remember that a log line or a journal entry is pushed verbatim.
