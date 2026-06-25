"""Append content to a file within _saku/ (restricted paths only).

Supports optional heading parameter: when specified, content is inserted
directly after the matching ## heading line, before the next ## heading.
"""

import re
from pathlib import Path

ALLOWED_PREFIXES = [
    "blog/",
    "monologue/",
    "principles/",
    "skills/",
    "study/",
    "tools/",
    "meta.md",
    "chat.md",
    "request_list.md",
    "journal/",
]


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    if not path:
        return "[ERROR] path is empty"
    if not body.strip():
        return "[ERROR] content is empty"

    heading = kwargs.get("heading", "").strip()

    if not any(path.startswith(p) for p in ALLOWED_PREFIXES):
        return f"[DENY] cannot append to: {path}"

    target = (base / path).resolve()
    if not target.is_relative_to(base.resolve()):
        return "[DENY] scope outside memory directory"

    target.parent.mkdir(parents=True, exist_ok=True)

    exists = target.exists()

    if heading and exists and target.stat().st_size > 0:
        content = target.read_text(encoding="utf-8")
        pattern = re.compile(rf"^(##\s+{re.escape(heading)}.*)$", re.MULTILINE)
        m = pattern.search(content)
        if m:
            insert_pos = m.end()
            next_heading = re.search(r"^##\s+", content[insert_pos:], re.MULTILINE)
            if next_heading:
                insert_pos += next_heading.start()
            else:
                insert_pos = len(content)
            new_content = content[:insert_pos] + "\n" + body.strip() + "\n" + content[insert_pos:]
            target.write_text(new_content, encoding="utf-8")
            return f"[OK] appended to {path} under heading '## {heading}' ({len(body)} bytes)"
        else:
            return f"[ERROR] heading '## {heading}' not found in {path}"

    with target.open("a", encoding="utf-8") as f:
        if exists and target.stat().st_size > 0:
            f.write("\n")
        f.write(body.strip())

    return f"[OK] appended to {path} ({len(body)} bytes)"
