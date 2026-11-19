"""Web UI server (stdlib only, zero dependencies).

Started with ``saku ui``; serves a single-page chat UI on localhost.
``POST /api/chat`` runs the agent loop and streams the result over SSE.

Mechanism:
- http.server.ThreadingHTTPServer runs a local server
- GET  /                    → single-page HTML (chat UI)
- POST /api/chat             → accepts {message} and runs run_agent_loop
- Response is text/event-stream; status, visible tokens, tool runs, and completion are sent as events
- GET  /api/health           → health check
- GET  /api/proactive        → fetches autonomous messages (proactive messages/alerts) written by the daemon

Session history is kept in memory (localhost single-user assumption, v1).
"""

import json
import os
import socket
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Repo root (where config.toml / saku/ / memory/ live)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from saku import auth
from saku import config as saku_config
from saku.agent_loop import run_agent_loop
from saku import core as agent

_cfg, _config_base = saku_config.load_config()
_MEMORY_ROOT = agent.MEMORY_ROOT
_CTX = agent.context_config
_UI_INBOX = _MEMORY_ROOT / "state" / "ui_inbox.json"

_UI = saku_config.load_ui_config(_cfg)

# Auth is resolved lazily: the token file is created by `saku bootstrap` (or on
# first start when a token is required), not at import time.
_AUTH: dict[str, str | None] = {"mode": None, "token": None}


def _auth_state() -> tuple[str, str]:
    if _AUTH["mode"] is None:
        mode = auth.auth_mode(_UI)
        token = auth.ensure_token(_UI) if mode == "token" else ""
        _AUTH["mode"], _AUTH["token"] = mode, token
    return str(_AUTH["mode"]), str(_AUTH["token"] or "")

# Session history (client_id -> messages). The system prompt is index 0.
_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()

# ── Login (token exchange for a cookie) ──────────────────
# The token is pasted once; afterwards the browser carries an HttpOnly cookie.
# Putting the token in a URL would leave it in history, Referer headers and logs.
LOGIN_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>SAKU — login</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{background:#1a1b26;color:#d5d6e0;font-family:"Noto Sans JP",sans-serif;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
 form{background:#23243a;padding:24px 28px;border-radius:10px;width:min(90vw,360px)}
 h1{font-size:1.1em;margin:0 0 4px} p{color:#888;font-size:.85em;margin:0 0 16px}
 input{width:100%;box-sizing:border-box;background:#1a1b26;border:1px solid #444;
       color:#d5d6e0;border-radius:6px;padding:10px}
 button{margin-top:12px;width:100%;background:#7aa2f7;border:0;color:#111;
        border-radius:6px;padding:10px;font-weight:bold;cursor:pointer}
 .err{color:#f7768e;font-size:.85em;margin-top:10px}
</style></head><body>
<form method="post" action="/login">
 <h1>SAKU</h1>
 <p>トークンを入力してください（<code>~/.config/saku/ui.token</code>）</p>
 <input name="token" type="password" autofocus autocomplete="current-password">
 <button type="submit">ログイン</button>
 __ERROR__
</form></body></html>"""

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
  .status { color:#7aa2f7; font-size:0.85em; margin:4px 0; font-style:italic;
            display:flex; align-items:center; gap:6px; }
  .status::before { content:""; width:7px; height:7px; border-radius:50%; background:#7aa2f7;
                    animation:pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity:.25; transform:scale(.8); } 50% { opacity:1; transform:scale(1); } }
  .error { color:#f7768e; }
  #proactive { position:fixed; right:16px; bottom:80px; background:var(--panel);
               border:1px solid var(--accent); border-radius:8px; padding:10px 14px;
               max-width:280px; display:none; }
  #requestbar { display:flex; align-items:center; gap:6px; }
  #requestbtn { background:var(--panel); color:#e0af68; border:1px solid #555;
                border-radius:8px; padding:4px 10px; font-size:0.85em; cursor:pointer;
                display:none; }
  #requestbadge { background:#e0af68; color:#111; border-radius:10px; padding:0 6px;
                  font-size:0.8em; font-weight:bold; }
  #requestpanel { position:fixed; top:48px; right:16px; background:var(--panel);
                  border:1px solid #e0af68; border-radius:8px; padding:12px 14px;
                  max-width:320px; box-shadow:0 4px 16px rgba(0,0,0,.4); display:none; }
  #requestpanel h4 { margin:0 0 6px; color:#e0af68; font-size:0.9em; }
  #requestpanel ul { margin:0; padding-left:18px; }
  #requestpanel li { margin:4px 0; font-size:0.88em; }
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
  <div id="requestbar">
    <button id="requestbtn">💡 依頼<span id="requestbadge">0</span></button>
    <div id="requestpanel"><h4>💡 Ownerへのお願いリスト</h4><ul></ul></div>
  </div>
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
  const t = text.length > 300 ? text.slice(0, 300) + "\\n... (省略)" : text;
  el.textContent = "[tool] " + t;
  msgBox.append(el);
  msgBox.scrollTop = msgBox.scrollHeight;
}
let statusEl = null;
function setStatus(text) {
  if (!statusEl) {
    statusEl = document.createElement("div");
    statusEl.className = "status";
    msgBox.append(statusEl);
  }
  statusEl.textContent = text;
  msgBox.scrollTop = msgBox.scrollHeight;
}
function clearStatus() {
  if (statusEl) { statusEl.remove(); statusEl = null; }
}
const STATUS_LABELS = { "thinking": "考えています", "compacting context": "文脈を整理しています" };
function statusLabel(text) {
  if (STATUS_LABELS[text]) return STATUS_LABELS[text];
  if (text.indexOf("tool: ") === 0) return "ツール実行: " + text.slice(6);
  return text;
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
        if (data.type === "visible") { clearStatus(); sakuBody.textContent += data.text; }
        else if (data.type === "tool") { clearStatus(); addTool(data.text); }
        else if (data.type === "status") setStatus(statusLabel(data.text));
        else if (data.type === "error") { clearStatus(); sakuBody.classList.add("error"); sakuBody.textContent = data.text; }
        else if (data.type === "done") clearStatus();
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

async function pollRequests() {
  try {
    const res = await fetch("/api/requests");
    const data = await res.json();
    const list = (data && data.requests) || [];
    const btn = $("#requestbtn");
    if (!list.length) {
      btn.style.display = "none";
      return;
    }
    btn.style.display = "inline-block";
    $("#requestbadge").textContent = list.length;
    const ul = $("#requestpanel").querySelector("ul");
    ul.textContent = "";
    list.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      ul.append(li);
    });
  } catch (e) {}
}
$("#requestbtn").onclick = () => {
  const p = $("#requestpanel");
  p.style.display = p.style.display === "block" ? "none" : "block";
};
pollRequests();
setInterval(pollRequests, 15000);
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


_REQ_FILE = _MEMORY_ROOT / "request_list.md"


def _load_requests() -> list[str]:
    """Read pending Owner-request items (``- [ ] ...``) from request_list.md."""
    if not _REQ_FILE.exists():
        return []
    try:
        pending = []
        for line in _REQ_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- [ ]"):
                desc = line.replace("- [ ]", "").strip()
                if desc:
                    pending.append(desc)
        return pending
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # suppress default logging
        pass

    def _send_headers(self, status=200, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    # ── auth helpers ────────────────────────────────────
    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return ""
        morsel = jar.get(_UI.cookie_name)
        return morsel.value if morsel else ""

    def _bearer_token(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    def _authenticated(self) -> bool:
        mode, token = _auth_state()
        if mode == "none":
            return True
        return auth.verify(self._cookie_token(), token) or auth.verify(self._bearer_token(), token)

    def _send_login(self, status=200, error: bool = False):
        body = LOGIN_PAGE.replace("__ERROR__", '<div class="err">トークンが違います</div>' if error else "")
        self._send_headers(status)
        self.wfile.write(body.encode("utf-8"))

    def _send_unauthorized(self):
        self._send_headers(401, ctype="application/json")
        self.wfile.write(b'{"error":"unauthorized"}')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            # Public by design: a monitor must be able to tell "alive" from "down"
            # without holding the token.
            self._send_headers(ctype="application/json")
            self.wfile.write(b'{"status":"ok"}')
            return
        if path == "/login":
            self._send_login()
            return
        if not self._authenticated():
            if path in ("/", "/index.html"):
                self._send_login()
            else:
                self._send_unauthorized()
            return
        if path in ("/", "/index.html"):
            self._send_headers()
            self.wfile.write(PAGE.encode("utf-8"))
            return
        if path == "/api/proactive":
            self._send_headers(ctype="application/json")
            self.wfile.write(json.dumps(_load_proactive(), ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/requests":
            self._send_headers(ctype="application/json")
            self.wfile.write(json.dumps({"requests": _load_requests()}, ensure_ascii=False).encode("utf-8"))
            return
        self._send_headers(404)
        self.wfile.write(b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if path == "/login":
            token = ""
            try:
                token = parse_qs(body.decode("utf-8", "replace")).get("token", [""])[0]
            except Exception:
                token = ""
            mode, expected = _auth_state()
            if mode == "none" or auth.verify(token, expected):
                self._send_headers(
                    303,
                    ctype="text/plain; charset=utf-8",
                    headers={
                        "Location": "/",
                        "Set-Cookie": (
                            f"{_UI.cookie_name}={expected}; HttpOnly; SameSite=Lax; "
                            "Path=/; Max-Age=31536000"
                        ),
                    },
                )
            else:
                self._send_login(status=403, error=True)
            return

        if path != "/api/chat":
            self._send_headers(404)
            self.wfile.write(b"not found")
            return

        if not self._authenticated():
            self._send_unauthorized()
            return
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
                history = [{"role": "system", "content": agent.build_light_system_prompt()}]
            history = list(history)
            recall_msg = agent.build_recall_message(message)
            if recall_msg:
                history.append(recall_msg)
            history.append({"role": "user", "content": message})
            _sessions[client_id] = history

        try:
            result = run_agent_loop(
                history,
                agent.llm_for_role("chat"),
                _CTX,
                _MEMORY_ROOT,
                agent.CODE_ROOT,
                on_visible=lambda t: self._emit({"type": "visible", "text": t}),
                on_tool_result=lambda t: self._emit({"type": "tool", "text": t}),
                on_status=lambda s: self._emit({"type": "status", "text": s}),
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


class _DualStackHTTPServer(ThreadingHTTPServer):
    """IPv6 any-address server that also accepts IPv4-mapped connections.

    cloudflared (and some VPN proxies) dial the origin as ``[::1]``; an IPv4-only
    bind answers nothing, which surfaces as a 502 at the edge. Binding ``::`` with
    ``IPV6_V6ONLY=0`` serves both families from one socket.
    """

    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()


def _server_class(host: str):
    """Pick the socket family for the configured bind address."""
    return _DualStackHTTPServer if ":" in host else ThreadingHTTPServer


def _start_daemon_thread() -> None:
    """Start the daemon autonomous loop (self-study, reflection, proactive messages) in the same process."""

    def _run() -> None:
        try:
            from saku import daemon as daemon_mod

            daemon_mod.main()
        except Exception as e:
            print(f"[!] daemon thread error: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def serve(host: str = "127.0.0.1", port: int = 8787, auto_daemon: bool = True) -> None:
    """Start the Web UI server (blocking).

    When auto_daemon=True, the autonomous loop (daemon) also starts as a thread in this process.
    """
    global _UI
    from dataclasses import replace as _replace

    _UI = _replace(_UI, host=host, port=int(port), auto_daemon=auto_daemon)
    _AUTH["mode"] = None  # the bind address decides whether a token is required
    mode, _token = _auth_state()

    if mode == "none" and not auth.is_loopback(host) and os.environ.get("SAKU_UI_ALLOW_INSECURE") != "1":
        raise SystemExit(
            f"[!] refusing to serve without auth on {host}:{port}.\n"
            "    Set [ui] auth = \"token\" (recommended) or, if a reverse proxy already\n"
            "    authenticates, re-run with SAKU_UI_ALLOW_INSECURE=1."
        )

    server = _server_class(host)((host, port), Handler)
    if auto_daemon:
        _start_daemon_thread()
    print(f"╭─ SAKU Web UI ─────────────────────────────")
    print(f"│  http://{host}:{port}")
    if mode == "token":
        print(f"│  auth: token  ({auth.token_file(_UI)})")
    elif not auth.is_loopback(host):
        print(f"│  auth: NONE (insecure; SAKU_UI_ALLOW_INSECURE=1)")
    else:
        print(f"│  auth: none (loopback only)")
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
    serve(_UI.host, _UI.port, auto_daemon=_UI.auto_daemon)


if __name__ == "__main__":
    main()
