import urllib.request
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

class DDGLiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current_result = {}
        self.in_snippet_td = False
        self.snippet_depth = 0
        self.link_depth = 0
        self.current_href = ""
        self.temp_text = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        # Result title link is inside an 'a' tag with class 'result-link'
        if tag == "a" and "result-link" in attrs_dict.get("class", ""):
            # Parse actual destination URL from DDG redirection if necessary
            # (Lite version links are usually direct, but let's extract href)
            href = attrs_dict.get("href", "")
            if href.startswith("//"):
                href = "https:" + href
            self.current_href = href
            self.link_depth = 1
            self.temp_text = []
        elif tag == "td" and "result-snippet" in attrs_dict.get("class", ""):
            self.in_snippet_td = True
            self.snippet_depth = 1
            self.temp_text = []

    def handle_endtag(self, tag):
        if self.link_depth > 0:
            if tag == "a":
                self.link_depth = 0
                title = "".join(self.temp_text).strip()
                if title:
                    self.current_result["title"] = title
                    self.current_result["url"] = self.current_href
            else:
                self.link_depth += 1
        elif self.in_snippet_td:
            if tag == "td":
                self.snippet_depth -= 1
                if self.snippet_depth == 0:
                    self.in_snippet_td = False
                    snippet = "".join(self.temp_text).strip()
                    # Clean up double spacing and weird spaces
                    snippet = " ".join(snippet.split())
                    self.current_result["snippet"] = snippet
                    self.results.append(self.current_result)
                    self.current_result = {}
            else:
                self.snippet_depth += 1

    def handle_data(self, data):
        if self.link_depth > 0 or self.in_snippet_td:
            self.temp_text.append(data)

def run(base: Path, path: str, body: str = "") -> str:
    query = body.strip()
    if not query:
        return "[ERROR] Query is empty."

    print(f"[*] Web searching for: {query}")
    url = "https://lite.duckduckgo.com/lite/"
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8")
        
        parser = DDGLiteParser()
        parser.feed(html)
        
        if not parser.results:
            return "No results found."

        formatted_results = []
        for i, r in enumerate(parser.results[:5]):
            title = r.get("title", "No Title")
            link = r.get("url", "")
            snippet = r.get("snippet", "")
            formatted_results.append(
                f"[{i+1}] {title}\nURL: {link}\nSummary: {snippet}\n"
            )
        return "\n".join(formatted_results)

    except Exception as e:
        return f"[ERROR] Web search failed: {e}"
