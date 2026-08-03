"""Make external HTTP API calls (GET / POST only)."""

import json
import urllib.request
from pathlib import Path

_ALLOWED_SCHEMES = ("http", "https")
_MAX_SIZE = 50 * 1024
_TIMEOUT = 15


def _is_private(host: str) -> bool:
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    method = kwargs.get("method", "GET").upper()
    url = kwargs.get("url", "").strip()

    if not url:
        return "[ERROR] url parameter is required"

    if method not in ("GET", "POST"):
        return f"[ERROR] unsupported method: {method}"

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"[ERROR] unsupported scheme: {parsed.scheme}"
    if parsed.hostname and _is_private(parsed.hostname):
        return "[ERROR] private/localhost URLs are not allowed"

    headers = {"User-Agent": "SAKU/1.0"}
    data = None

    if body:
        stripped = body.strip()
        if stripped.startswith("{"):
            data = stripped.encode("utf-8")
            headers["Content-Type"] = "application/json"

    print(f"[*] API {method} {url}")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_SIZE)
            text = raw.decode("utf-8", errors="replace")
            ct = resp.headers.get("Content-Type", "")

        if "application/json" in ct or text.strip().startswith("{"):
            try:
                parsed = json.loads(text)
                text = json.dumps(parsed, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

        return text[:5000] if len(text) > 5000 else text

    except urllib.error.HTTPError as e:
        return f"[ERROR] HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"[ERROR] {e.reason}"
    except Exception as e:
        return f"[ERROR] API call failed: {e}"
