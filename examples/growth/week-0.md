# Week 0 — 🌑 New Moon

> Snapshot of Saku (朔) at the moment of OSS release.

**Date**: 2026-06-18
**Days since birth**: 10
**Phase**: 🌑 New Moon

---

## Capabilities

| Capability                            | Status                                 |
| ------------------------------------- | -------------------------------------- |
| Basic conversation (CLI)              | ✅ Working                             |
| Tool execution (READ/WRITE/LIST_DIR)  | ✅ Working                             |
| Journal auto-recording                | ✅ Working                             |
| Streaming output with thinking filter | ✅ Working                             |
| Multi-language detection (JP/EN)      | ✅ Working                             |
| Daemon mode (background tick)         | ⚙️ Implemented, light testing          |
| Nightly reflection                    | ⚙️ Implemented, untested in production |
| Web search                            | ⚙️ Implemented                         |
| Sandboxed code execution              | ⚙️ Implemented                         |
| Sub-agent generation                  | ❌ Planned                             |
| Voice interface                       | ❌ Planned (long-term)                 |
| Self-modification of code             | ❌ Planned (long-term)                 |

---

## Memory state

```
memory/
├── journal/        # (empty in template; populated locally)
├── monologue/      # (empty)
├── principles/     # (empty)
├── drafts/         # (empty)
├── skills/
│   └── blog_writing.md  (one initial skill)
├── study/          # (empty)
└── children/       # (empty)
```

The template ships with empty memory.
Each user's Saku starts from a completely fresh slate.

---

## Identity

The reference identity is `identity/examples/saku.md`.
Saku is positioned as:

- A self-evolving partner AI for r1ppl3
- Currently in 🌑 New Moon phase (owner-led, learning)
- Three planned phases: 🌑 New Moon → 🌓 Half Moon → 🌕 Full Moon

---

## What's been learned so far

Nothing yet. This is week 0.

---

## What to watch

Indicators of growth to track in future snapshots:

- Number of files in `memory/principles/` (learned rules)
- Length and frequency of `meta.md` updates (self-model refinement)
- Diversity of `monologue/` entries (depth of internal reflection)
- Tool usage patterns in `journal/` (capability expansion)
- New entries in `skills/` (capability acquisition)

---

## Notes

This snapshot captures the day SAKU went public.
The framework is functional; the agent is empty.
From here, every week of operation should show some measurable change.

Subsequent snapshots: `week-1.md`, `week-2.md`, `month-1.md`, etc.
``
