# Reading the Owner's vault

SAKU's tools are confined to the memory root for **writes**. Reads were
implicitly confined to the same place, but the system prompt claimed the whole
vault was readable — so "look it up" failed with `[DENY]` while the model
believed otherwise.

`[access]` makes the read boundary explicit and configurable.

```toml
[access]
vault_root = ""                  # "" = the vault that owns the memory root
vault_read = ["01_Notes/", "02_Reference/"]
max_file_chars = 4000            # READ_FILE truncation
max_search_results = 10          # SEARCH_NOTES display cap
```

## What it does

- `READ_FILE`, `LIST_DIR` and `SEARCH_NOTES` can reach the listed directories,
  read-only. Everything else outside the memory root stays refused.
- An empty `vault_read` (the default) means **memory root only**, which is the
  previous effective behaviour.
- The prompt line "Read/List allowed" is generated from this config, so the
  model is told what is actually true.

## What it refuses

- absolute paths and `..` (a vault path is always vault-relative);
- a prefix that is not a path component (`01_NotesX/` is not `01_Notes/`);
- hidden files and directories;
- symlinks that resolve outside the vault;
- every write, move and delete (the path policy is unchanged).

## Relationship to the dedicated flows

`saku learn` (`[learning] dir`) and `saku sort` (`[sort] vault_root`) read the
vault independently of `[access]`: they are explicit, narrow integrations, not
general tool access. Granting `[access]` does not widen them, and vice versa.

## Choosing the scope

Start narrow. `01_Notes/` (daily notes) and `02_Reference/` (reference notes)
cover most "look something up" requests; add `05_Journal/` if the Owner wants
SAKU to read the personal journal. Keep directories with credentials or
private records out; the agent's own memory already excludes them.
