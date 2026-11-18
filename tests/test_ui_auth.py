#!/usr/bin/env python3
"""Tests for Web UI authentication (persistent token, cookie login)."""

import http.client
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode

CODE_ROOT = Path(__file__).parent
if str(CODE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT.parent))

from saku import auth, ui
from saku.config import UiConfig


class Server:
    def __init__(self, token_file: Path):
        self._saved_ui = ui._UI
        self._saved_auth = dict(ui._AUTH)
        ui._UI = UiConfig(host="0.0.0.0", port=0, auth="token", token_file=str(token_file))
        ui._AUTH = {"mode": None, "token": None}
        self.server = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        result = (res.status, dict(res.getheaders()), data)
        conn.close()
        return result

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        ui._UI = self._saved_ui
        ui._AUTH = self._saved_auth


def _with_server(fn):
    with tempfile.TemporaryDirectory() as tmp:
        token_file = Path(tmp) / "ui.token"
        server = Server(token_file)
        try:
            fn(server, token_file)
        finally:
            server.close()


def test_page_requires_login():
    def check(server, _token_file):
        status, _headers, body = server.request("GET", "/")
        assert status == 200
        assert "ログイン".encode() in body

    _with_server(check)


def test_api_is_unauthorized_without_token():
    def check(server, _token_file):
        status, _headers, body = server.request("GET", "/api/proactive")
        assert status == 401
        assert b"unauthorized" in body
        status, _headers, _body = server.request(
            "POST", "/api/chat", body=b'{"message":"hi"}', headers={"Content-Type": "application/json"}
        )
        assert status == 401

    _with_server(check)


def test_login_rejects_wrong_token():
    def check(server, _token_file):
        body = urlencode({"token": "nope"}).encode()
        status, _headers, _data = server.request(
            "POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert status == 403

    _with_server(check)


def test_login_sets_cookie_and_page_opens():
    def check(server, token_file):
        token = auth.ensure_token(ui._UI)
        assert token_file.exists()
        body = urlencode({"token": token}).encode()
        status, headers, _data = server.request(
            "POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert status == 303
        cookie = headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie

        auth_header = {"Cookie": cookie.split(";")[0]}
        status, _headers, page = server.request("GET", "/", headers=auth_header)
        assert status == 200
        assert b"<title>SAKU" in page

        status, _headers, body = server.request("GET", "/api/requests", headers=auth_header)
        assert status == 200
        assert b"requests" in body

    _with_server(check)


def test_bearer_token_is_accepted():
    def check(server, _token_file):
        token = auth.ensure_token(ui._UI)
        status, _headers, body = server.request(
            "GET", "/api/requests", headers={"Authorization": f"Bearer {token}"}
        )
        assert status == 200
        assert b"requests" in body

    _with_server(check)


def test_health_is_public():
    def check(server, _token_file):
        status, _headers, body = server.request("GET", "/api/health")
        assert status == 200
        assert b'"ok"' in body

    _with_server(check)


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} UI auth tests PASSED!")


if __name__ == "__main__":
    run_tests()
