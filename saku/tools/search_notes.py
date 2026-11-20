"""Search memory files using keywords.

Markdown is the bulk of memory, but the mail channel's correspondence lives
in ``mail/*.log`` and talk transcripts in ``mail/talk.log``. Searching only
``*.md`` made "do you remember 小夜?" answer "no record" while the record sat
one directory away (#120 follow-up).
"""

import os
from pathlib import Path

def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    query = body.strip().lower()
    if not query:
        return "[ERROR] search query is empty"

    vault_root = base.parent if base.name == "_saku" else base
    searchable = (".md", ".log", ".jsonl")

    results = []
    for p in vault_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in searchable:
            continue
        # Skip hidden files and directories (like .git, .obsidian, etc.)
        if any(part.startswith('.') for part in p.relative_to(vault_root).parts):
            continue
            
        try:
            content = p.read_text(encoding="utf-8")
            if query in content.lower():
                # Get path relative to base (SAKU_ROOT) so Saku can read/write it directly
                rel_path = os.path.relpath(p, base)
                
                # Extract snippet
                lines = content.splitlines()
                matching_snippet = ""
                for line in lines:
                    if query in line.lower():
                        matching_snippet = line.strip()
                        if len(matching_snippet) > 80:
                            matching_snippet = matching_snippet[:80] + "..."
                        break
                
                results.append(f"- path: {rel_path}\n  snippet: \"{matching_snippet}\"")
        except Exception as e:
            continue

    if not results:
        return f"No matches found for query: {body.strip()}"

    # Return up to 10 results
    output = [f"Found {len(results)} match(es):"]
    output.extend(results[:10])
    return "\n\n".join(output)
