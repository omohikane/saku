# Persona files — what SAKU is and how it speaks

SAKU's character lives in three editable Markdown files. They are deliberately
separate, because they answer different questions and change at different
speeds.

| File | Question | Who edits | Typical changes |
| --- | --- | --- | --- |
| `identity/soul.md` | What are the core values? | Owner | a few sentences; rare |
| `identity/genome.md` | Who is SAKU? | Owner | name, origin, self-model, goals |
| `identity/persona.md` | How does it speak? | Owner | tone, pronouns, habits, prohibitions |

The code also has a **style contract** (`saku/persona.py`) with hard rules
(start with the answer, no stock greetings, at most one question). It is not
configurable on purpose; it exists to keep every model on the same baseline.
The persona file layers on top of it, and the continuity block adds "what has
been happening" each turn. Order in the system prompt:

```
Core (soul) → Identity (genome) → Persona → Style contract → Continuity → Current time
```

## Configuring the paths

Defaults live under the memory root. All three accept a memory-relative path,
an absolute path, or `${ENV}`:

```toml
[identity]
# soul = "identity/soul.md"
# genome = "identity/genome.md"

[persona]
# file = "identity/persona.md"
# chars = 2200          # prompt budget for the persona block
```

`genome` keeps the old fallbacks when the configured file is absent
(`<vault>/_saku/identity/`, `<vault>/identity/`, then the repo sample), so an
existing installation keeps working.

## Editing

- **Web UI**: the ⚙ button opens the identity editor (soul / genome / persona),
  loads the current text, and saves it. Saving reloads the prompt cache, so the
  next message already uses the new text. A `.bak` of the previous content is
  written beside the file.
- **CLI**: `saku persona init` writes a starter persona document; `saku persona`
  shows the path, size and preview. `saku persona init --force` overwrites.
- **Files**: editing the Markdown directly is fully supported; the next turn
  picks it up.

## Why SAKU cannot edit these files itself

This is the point of the separation. The Owner may rewrite the persona; the
agent's own tools may not. `identity/soul.md`, `identity/genome.md` and
`identity/persona.md` are on the **write and delete deny lists**, so
`WRITE_FILE`, `APPEND_FILE`, `MOVE_FILE` and `DELETE_FILE` refuse them even if
`[paths] write_allowed` is widened by accident. The Web UI editor is behind the
Owner token.

Known gap: `EXECUTE_CODE` runs Python as the service user and is not confined to
the tool policy; the runtime boundary (`docs/ADR_RUNTIME_BOUNDARY.md`, #78) is
what closes that path. Until then, the tool-level denial is the guarantee, not
the sandbox.

## For OSS forks

- Keep the three files short and concrete; long prose in the prompt crowds out
  memory and costs tokens. `[persona] chars` bounds the persona block.
- Put stable traits in `genome`, speech habits in `persona`, values in `soul`.
  If two files disagree, the model follows the later section (persona), which
  is usually not what you want — keep them consistent.
- A model swap is easier with an explicit persona document than with traits
  inferred from chat history: the same text reaches every model.
