# DAEMON

The SAKU Daemon (`daemon.py`) is the engine that drives SAKU's autonomous capabilities in the background.

## Main Functionality

The daemon runs continuously, monitoring files, processing events, and scheduling tasks according to configured intervals.

| Frequency | Target Task | Description |
|---|---|---|
| **5 seconds** | Chat Monitor | Checks `chat.md` for user inputs ending with `>`. |
| **5 seconds** | Inactivity Archiver | Automatically archives and clears the chat if it has been inactive for too long. |
| **5 seconds** | Reflection Checker | Fires at 2:00 AM daily to run the nightly reflection module. |
| **5 seconds** | Autonomous Initiation | Initiates a conversation (greeting, check-in, or findings) if the user has been inactive. |
| **30 minutes** | Vault Inbox Scanner | Scans `00_Inbox` for new files to import into SAKU's knowledge. |
| **30 minutes** | Autonomous Action Tick | Autonomous study tick: SAKU writes a monologue, searches the web, and runs python scripts. |

---

## 1. Chat Interaction (`chat.md`)

Unlike standard CLI inputs, SAKU supports chat interaction via a Markdown file. 

- **Sending Messages**: Open `chat.md` in Obsidian or any editor. Write your message, end it with `>` and save.
- **Why the `>`?**: The `>` serves as a send trigger. Without it, automatic saving features in modern editors could cause SAKU to reply before you finish typing.
- **Reply Formatting**: The daemon detects the `>` trigger, extracts the difference since the last turn, wraps your message in a formatted block:
  ```markdown
  **Owner** (14:32)
  your message here
  ```
  And then appends SAKU's response in a matching block:
  ```markdown
  **SAKU** (14:32)
  SAKU's reply here
  ```

---

## 2. Inactivity and Archiving

To keep `chat.md` clean and lightweight, the daemon implements an automated archiving pipeline:
- **Triggers**:
  - Chat exceeds a turn limit (default: **10 turns**).
  - No messages have been sent for a duration (default: **30 minutes**).
- **Process**:
  1. The daemon loads the chat history.
  2. SAKU runs an LLM task to summarize the chat, save any new learned rules under `principles/`, and update the `meta.md` self-model under the `## 最近の出来事` (Recent Events) section.
  3. `chat.md` is reset to its default header template.

---

## 3. Obsidian Vault Inbox (`00_Inbox`)

If SAKU is configured to run inside an Obsidian Vault, it checks for a directory named `00_Inbox` located one level above SAKU's memory root folder.
- The daemon keeps track of processed files inside `processed_inbox.json`.
- When a new or updated Markdown file is found in `00_Inbox`, the daemon presents its contents to SAKU.
- SAKU extracts key information, files them under `principles/` or `skills/`, and writes the processed state back.

---

## 4. Nightly Reflection (`reflect.py`)

At **2:00 AM**, the daemon triggers a deep reflection process:
1. It reads the current day's `journal/` and `monologue/` files.
2. It runs an LLM loop to review what was learned, what succeeded, and what failed.
3. It consolidates lessons into new principles files or updates existing ones.
4. It updates the self-model `meta.md` (e.g. updating current status, things to do tomorrow).
5. Once completed, it writes a short message in `chat.md` to report that the reflection was successful.
