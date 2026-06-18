# SETUP

Detailed setup guide to get SAKU up and running on your local machine.

## Prerequisites

- **Python 3.10+**
- **pip dependencies:**
  - `requests` (the only external dependency)
- **Local LLM Server** compatible with OpenAI's Chat Completions API.
  - Tested with: [llama.cpp](https://github.com/ggerganov/llama.cpp) (`llama-server`)
  - Recommended model: Qwen2.5-32B-Instruct or Qwen2.5-7B-Instruct (a model with strong reasoning capabilities is recommended).

---

## Step 1: Install Dependencies

Clone this repository and install the `requests` package:

```bash
git clone https://github.com/omohikane/saku
cd saku
pip install requests
```

---

## Step 2: Configure SAKU

1. Create a configuration file from the template:
   ```bash
   cp config.example.toml config.toml
   ```
2. Edit `config.toml`:
   - Set the `api_url` in the `[llm]` section to point to your LLM provider (local server, OpenAI, OpenRouter, etc.).
   - Configure `api_key` and `model` in the `[llm]` section if you are using a commercial API provider (e.g. OpenAI). If `api_key` is omitted, SAKU will check the `OPENAI_API_KEY` environment variable.
   - Adjust `memory.root` if you want to store SAKU's memory files in a custom directory (e.g. an Obsidian vault folder).

```toml
[llm]
api_url = "http://127.0.0.1:8080/v1/chat/completions"
# api_key = "your-api-key"  # Optional key (e.g. OpenAI/OpenRouter API key)
# model = "gpt-4o"          # Required if using multi-model endpoints

[memory]
root = "memory"  # Relative to the repo root, or an absolute path to Obsidian vault
```

---

## Step 3: Define personality and self-model (`genome.md` / `meta.md`)

Copy the templates and define your agent's identity, values, and style:

```bash
cp identity/genome.template.md identity/genome.md
cp memory/meta.template.md memory/meta.md
```

Edit `identity/genome.md`. This file contains the foundational constraints and values that define SAKU's persona. The agent will read this at startup and cannot overwrite it. `memory/meta.md` acts as the initial self-model which SAKU will autonomously update.

---

## Step 4: Run the LLM Server

For example, using `llama-server` from `llama.cpp`:

```bash
llama-server -m ~/models/your-model.gguf --host 127.0.0.1 --port 8080 -c 8192 --ngl 99
```

Make sure the server is reachable at the URL configured in `config.toml`.

---

## Step 5: Start SAKU

### Option A: Interactive Terminal Mode
Ideal for testing your agent, checking responses, and testing tools.

```bash
cd src
python saku_core.py
```

Type your message and press Enter. Type `/exit` to quit, `/clear` to reset the conversation, or `/reload` to reload the system prompt and genome definitions.

### Option B: Autonomous Background Mode (Daemon)
Fires up the autonomous loop. SAKU will monitor `chat.md`, write daily thoughts, study and run experiments periodically, and run night-time self-reflections.

```bash
cd src
nohup python daemon.py > ../saku.log 2>&1 &
```

Once running, SAKU will create `chat.md` in the memory directory. You can communicate with SAKU by writing messages ending with `>` in `chat.md`.
