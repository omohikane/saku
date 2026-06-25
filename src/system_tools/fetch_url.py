"""Fetch a URL and return its text content."""

import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self._text.append(text)

    def get_text(self):
        return "\n".join(self._text)


_ALLOWED_SCHEMES = ("http", "https")
_MAX_SIZE = 100 * 1024  # 100 KB
_TIMEOUT = 15


def _is_private(host: str) -> bool:
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    url = kwargs.get("url", "").strip() or body.strip()
    if not url:
        return "[ERROR] url is required (use url=\"...\" or put URL in body)"

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"[ERROR] unsupported scheme: {parsed.scheme}"
    if parsed.hostname and _is_private(parsed.hostname):
        return "[ERROR] private/localhost URLs are not allowed"

    print(f"[*] Fetching: {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SAKU/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_SIZE)
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type or url.endswith(".json"):
                text = raw.decode("utf-8", errors="replace")
            elif "text/plain" in content_type:
                text = raw.decode("utf-8", errors="replace")
            else:
                html = raw.decode("utf-8", errors="replace")
                parser = TextExtractor()
                parser.feed(html)
                text = parser.get_text()[:8000]

        if not text.strip():
            return "No readable content found."

        return text

    except urllib.error.HTTPError as e:
        return f"[ERROR] HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"[ERROR] {e.reason}"
    except Exception as e:
        return f"[ERROR] fetch failed: {e}"
