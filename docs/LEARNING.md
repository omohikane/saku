# Learning from Obsidian daily notes (`saku learn`)

Dreaming distils SAKU's own records (`journal/`, `monologue/`) into durable
memory. The Owner's daily notes are the other half of the picture: what actually
happened, what was decided, what was noticed. `saku learn` reads them and
promotes the durable parts into `MEMORY.md`, optionally organising them into
concept notes under `wiki/`.

Issue: #68. Implementation: `saku/learning.py`, `[learning]`, `saku learn`.

## Rules

- **The notes are read-only.** Only SAKU's own memory root is written.
- **Propose first.** The default run prints what it would learn; `--apply`
  writes `MEMORY.md`, the wiki and `state/learning.json`.
- **Only changed notes are re-read.** A content hash is stored per note, so a
  run over 200 unchanged notes costs nothing and an edited note re-opens.
- **Contact details do not travel.** `[learning] redact` masks phone numbers and
  e-mail addresses before the text reaches the model and before anything is
  stored (dates are left alone: `2026-11-18` is not a phone number).

## Selecting notes

```
saku learn                  # newest unprocessed notes, [learning] max_notes_per_run, dry run
saku learn --apply
saku learn --limit 5
saku learn --all
saku learn --since 2026-10-01
saku learn --note 2026-11-18
saku learn --rebuild        # ignore state (append_items reinforces, never duplicates)
```

`dir` defaults to `<vault>/01_Notes/Daily_Note`, where the vault is the one that
owns the memory root (`<vault>/_saku/memory`). Notes below `min_chars` are
treated as template-only and recorded as skipped rather than re-read.

```toml
[learning]
# dir = ""                    # "" = <vault>/01_Notes/Daily_Note
# state_file = "state/learning.json"
# max_notes_per_run = 20
# min_chars = 200
# max_chars = 12000
# redact = true
# write_memory = true
# write_wiki = true
```

Extraction runs under the `learn` LLM role (`[llm] learn`), so note excerpts can
be kept on a local profile:

```toml
[llm]
learn = "litellm"
```

## Memory stage

`dreaming.extract_items_from_text` asks for the same `[MEMORY]` shape dreaming
uses, with a daily-note prompt: no work-log tables or ticket IDs, product names
generalised into preferences, one line per item (≤60 chars), and "skip when in
doubt". `dreaming.append_items` stores the items with `src:daily/<date>`, so a
promoted fact can be traced back to its note.

## Wiki stage

With `write_wiki`, a second tools-disabled call groups the day's items into
concept notes:

- **One note per concept, never per day.** Date-titled dumps are what
  `saku tidy --retire-dumps` archives; `saku learn` does not create them.
  Daily granularity belongs to `MEMORY.md` entries.
- **Reuse before create.** The prompt lists the existing note titles and tells
  the model to reuse one verbatim. On apply, a title that collides with an
  existing note — by filename *or* by `tidy.normalize_title` (spacing and
  full-width variants) — is appended to instead of creating a near-duplicate.
- **Never overwrite.** `wiki.create_note` overwrites silently, so an existing
  note is only ever extended: new sentences go under `## 追記`, `> updated:` is
  bumped, and `> created:` / `> source:` stay as first written (provenance is
  set once, like every other promotion).
- **Rejected titles**: empty, `MEMORY`, over 40 characters, or date-prefixed.
- **Contact details**: sentences containing phone numbers, e-mail addresses or
  long digit runs are dropped from concept bodies.
- **Index**: `_index.md` is regenerated once per run, and only when something
  was written. Notes live at the top level of `wiki/` — `regenerate_index`,
  `saku status` and recall all read that level, so subfolders would silently
  disappear from the map.
- A dry run does not call the concept pass, so proposing costs one call per
  note and applying costs two.

Tags are `auto, learn, <keywords>`; links always include `[[MEMORY]]`.

## Tests

`tests/test_learning.py` (selection, hashes, dry-run/apply, state, redaction)
and `tests/test_learn_wiki.py` (new note shape, append-not-overwrite, notation
variants, rejected titles, contact details, index). Both use temporary roots and
stub the LLM; the real vault is never touched.

## Not implemented yet

- Daemon integration. The issue asks for manual runs first; once the output has
  been observed over a few weeks, a scheduled pass can be added behind its own
  config switch.
- Batching several notes into one call. It would cut calls ~5× but changes
  extraction quality, so it waits for evidence that the per-note cost matters.
