"""Search memory files using keywords.

Markdown is the bulk of memory, but the mail channel's correspondence lives
in ``mail/*.log`` and talk transcripts in ``mail/talk.log``. Searching only
``*.md`` made "do you remember 小夜?" answer "no record" while the record sat
one directory away (#120 follow-up).
"""

import os
from pathlib import Path

from saku import access

def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    query = body.strip().lower()
    if not query:
        return "[ERROR] search query is empty"

    searchable = (".md", ".log", ".jsonl")
    roots = access.search_roots(base)
    vroot = access.vault_root(base)
    acfg = access.load_access_config(access.cached_cfg())

    results = []
    seen = set()
    for root in roots:
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in searchable:
                continue
            if any(part.startswith('.') for part in p.relative_to(root).parts):
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if query not in content.lower():
                continue
            # Memory paths stay memory-relative; vault paths are vault-relative
            # so READ_FILE can use them directly (the [access] scope).
            try:
                rel_path = p.relative_to(base).as_posix()
            except ValueError:
                try:
                    rel_path = p.relative_to(vroot).as_posix()
                except ValueError:
                    rel_path = p.as_posix()
            if rel_path in seen:
                continue
            seen.add(rel_path)
            snippet = ""
            for line in content.splitlines():
                if query in line.lower():
                    snippet = line.strip()
                    if len(snippet) > 80:
                        snippet = snippet[:80] + "..."
                    break
            results.append(f"- path: {rel_path}\n  snippet: \"{snippet}\"")

    if not results:
        return f"No matches found for query: {body.strip()}"

    output = [f"Found {len(results)} match(es):"]
    output.extend(results[: acfg.max_search_results])
    return "\n\n".join(output)
