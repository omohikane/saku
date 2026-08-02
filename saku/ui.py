"""Web UI server (stdlib only, zero dependencies).

Started with ``saku ui``; serves a single-page chat UI on localhost.
``POST /api/chat`` runs the agent loop and streams the result over SSE.

Mechanism:
- http.server.ThreadingHTTPServer runs a local server
- GET  /                    → single-page HTML (chat UI)
- POST /api/chat             → accepts {message} and runs run_agent_loop
- Response is text/event-stream; visible tokens, tool runs, and completion are sent as events
- GET  /api/health           → health check
- GET  /api/proactive        → fetches autonomous messages (proactive messages/alerts) written by the daemon

Session history is kept in memory (localhost single-user assumption, v1).
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Repo root (where config.toml / saku/ / memory/ live)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import config as saku_config
from saku.agent_loop import run_agent_loop

# Dependent core (saku_core lives in src/)
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
import saku_core as agent

_cfg, _config_base = saku_config.load_config()
_MEMORY_ROOT = agent.MEMORY_ROOT
_LLM = agent._current_llm
_CTX = agent.context_config
_UI_INBOX = _MEMORY_ROOT / "state" / "ui_inbox.json"

# Session history (client_id -> messages). The system prompt is index 0.
_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()

# ── Frontend (single HTML) ───────────────────────────────
PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>SAKU — chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#1a1b26; --panel:#23243a; --text:#d5d6e0; --accent:#7aa2f7; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:"Noto Sans JP","Hiragino Kaku Gothic ProN",sans-serif; }
  #app { max-width:820px; margin:0 auto; display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 16px; border-bottom:1px solid #333; display:flex; justify-content:space-between; }
  #messages { flex:1; overflow-y:auto; padding:16px; }
  .msg { margin-bottom:14px; white-space:pre-wrap; word-break:break-word; }
  .msg .name { font-weight:bold; font-size:0.85em; margin-bottom:2px; }
  .msg.user { text-align:right; }
  .msg.user .body { display:inline-block; background:var(--panel); padding:8px 12px; border-radius:10px; }
  .msg.saku .body { display:inline-block; background:var(--panel); padding:8px 12px; border-radius:10px; }
  .msg .name.user { color:#9ece6a; } .msg .name.saku { color:var(--accent); }
  .tool { color:#e0af68; font-size:0.85em; margin:4px 0; }
  .error { color:#f7768e; }
  #proactive { position:fixed; right:16px; bottom:80px; background:var(--panel);
               border:1px solid var(--accent); border-radius:8px; padding:10px 14px;
               max-width:280px; display:none; }
  #inputbar { display:flex; gap:8px; padding:12px 16px; border-top:1px solid #333; }
  #input { flex:1; background:var(--panel); border:none; color:var(--text);
           border-radius:8px; padding:10px 12px; font-size:1em; }
  #send { background:var(--accent); border:none; color:#111; border-radius:8px;
          padding:10px 18px; font-weight:bold; cursor:pointer; }
</style>
</head>
<body>
<div id="app">
  <header><strong>SAKU</strong><span id="status">local</span></header>
  <div id="messages"></div>
  <div id="proactive"></div>
  <div id="inputbar">
    <input id="input" type="text" placeholder="メッセージを入力">
    <button id="send">送信</button>
  </div>
</div>
<script>
const $ = s => document.querySelector(s);
const clientId = sessionStorage.getItem("sid") || (crypto.randomUUID ? crypto.randomUUID()
                : Date.now().toString(36));
sessionStorage.setItem("sid", clientId);
const msgBox = $("#messages"), input = $("#input");

function addMsg(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  const name = document.createElement("div");
  name.className = "name " + role;
  name.textContent = role === "user" ? "Owner" : "SAKU";
  const body = document.createElement("div");
  body.className = "body";
  body.textContent = text;
  el.append(name, body);
  msgBox.append(el);
  msgBox.scrollTop = msgBox.scrollHeight;
  return body;
}
function addTool(text) {
  const el = document.createElement("div");
  el.className = "tool";
  el.textContent = "[tool] " + text;
  msgBox.append(el);
  msgBox.scrollTop = msgBox.scrollHeight;
}

async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMsg("user", text);
  const sakuBody = addMsg("saku", "");
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text, client_id: clientId }),
  });
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\\n\\n")) >= 0) {
      const evt = buf.slice(0, idx); buf = buf.slice(idx + 2);
      if (!evt.startsWith("data:")) continue;
      try {
        const data = JSON.parse(evt.slice(5).trim());
        if (data.type === "visible") sakuBody.textContent += data.text;
        else if (data.type === "tool") addTool(data.text);
        else if (data.type === "error") { sakuBody.classList.add("error"); sakuBody.textContent = data.text; }
      } catch (e) {}
    }
  }
}

$("#send").onclick = send;
input.addEventListener("keydown", e => { if (e.key === "Enter") send(); });

async function pollProactive() {
  try {
    const res = await fetch("/api/proactive");
    const data = await res.json();
    if (data && data.message) {
      const box = $("#proactive");
      box.style.display = "block";
      box.textContent = "SAKUから: " + data.message;
      box.onclick = () => { box.style.display = "none"; };
    }
  } catch (e) {}
}
setInterval(pollProactive, 10000);
</script>
</body>
</html>
"""


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _load_proactive() -> dict:
    if not _UI_INBOX.exists():
        return {}
    try:
        data = json.loads(_UI_INBOX.read_text(encoding="utf-8"))
        _UI_INBOX.write_text("{}", encoding="utf-8")
        return data
    except Exception:
        return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # suppress default logging
        pass

    def _send_headers(self, status=200, ctype="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_headers()
            self.wfile.write(PAGE.encode("utf-8"))
            return
        if path == "/api/health":
            self._send_headers(ctype="application/json")
            self.wfile.write(b'{"status":"ok"}')
            return
        if path == "/api/proactive":
            self._send_headers(ctype="application/json")
            self.wfile.write(json.dumps(_load_proactive(), ensure_ascii=False).encode("utf-8"))
            return
        self._send_headers(404)
        self.wfile.write(b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/chat":
            self._send_headers(404)
            self.wfile.write(b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            message = str(payload.get("message", ""))
            client_id = str(payload.get("client_id", "default"))
        except Exception:
            message, client_id = "", "default"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        if not message:
            self.wfile.write(_sse({"type": "error", "text": "メッセージが空です"}).encode("utf-8"))
            return

        with _sessions_lock:
            history = _sessions.get(client_id)
            if history is None:
                history = [{"role": "system", "content": agent.build_system_prompt()}]
            history = list(history)
            history.append({"role": "user", "content": message})
            _sessions[client_id] = history

        try:
            result = run_agent_loop(
                history,
                _LLM,
                _CTX,
                _MEMORY_ROOT,
                agent.CODE_ROOT,
                on_visible=lambda t: self._emit({"type": "visible", "text": t}),
                on_tool_result=lambda t: self._emit({"type": "tool", "text": t}),
            )
            with _sessions_lock:
                _sessions[client_id] = result.history
            if result.last_raw.startswith("[ERROR]") and not result.visible:
                self._emit({"type": "error", "text": result.last_raw})
            elif result.visible:
                agent.save_journal(message, result.visible, thinking=result.thinking)
            self._emit({"type": "done"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._emit({"type": "error", "text": f"[ERROR] {e}"})
            except Exception:
                pass

    def _emit(self, event: dict) -> None:
        self.wfile.write(_sse(event).encode("utf-8"))
        self.wfile.flush()


def _start_daemon_thread() -> None:
    """Start the daemon autonomous loop (self-study, reflection, proactive messages) in the same process."""

    def _run() -> None:
        try:
            import daemon as daemon_mod  # lives in src/

            daemon_mod.main()
        except Exception as e:
            print(f"[!] daemon thread error: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def serve(host: str = "127.0.0.1", port: int = 8787, auto_daemon: bool = True) -> None:
    """Start the Web UI server (blocking).

    When auto_daemon=True, the autonomous loop (daemon) also starts as a thread in this process.
    """
    server = ThreadingHTTPServer((host, port), Handler)
    if auto_daemon:
        _start_daemon_thread()
    print(f"╭─ SAKU Web UI ─────────────────────────────")
    print(f"│  http://{host}:{port}")
    if auto_daemon:
        print(f"│  自動ループ（daemon）: 起動中")
    print(f"│  Ctrl+C で停止")
    print(f"╰────────────────────────────────────────────")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[-] Web UI stopped.")
        server.server_close()


def main() -> None:
    host = _cfg.get("ui", {}).get("host", "127.0.0.1")
    port = int(_cfg.get("ui", {}).get("port", 8787))
    auto_daemon = _cfg.get("ui", {}).get("auto_daemon", True)
    serve(host, port, auto_daemon=auto_daemon)


if __name__ == "__main__":
    main()
