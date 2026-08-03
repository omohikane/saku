"""Wiki knowledge base helpers (Zettelkasten-style).

Notes live in memory/wiki/, one note per concept, with tags and [[links]].
The index (_index.md) is regenerated so the whole map is always reachable.

This lets SAKU build and maintain its own structured knowledge base by writing
plain Markdown notes, exactly like the rest of its memory.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def slugify(title: str) -> str:
    """Convert a title into a filesystem-safe note name."""
    s = re.sub(r"[^\w\s一-龠ぁ-んァ-ヶ]", "", title)
    s = re.sub(r"\s+", "-", s).strip().lower()
    return s or "note"


def note_path(wiki_root: Path, title: str) -> Path:
    return wiki_root / f"{slugify(title)}.md"


def create_note(wiki_root: Path, title: str, content: str, tags: str = "", links: str = "", date: str | None = None) -> str:
    """Create or update a wiki note. Returns a result string."""
    if not title.strip():
        return "[ERROR] title required"
    wiki_root.mkdir(parents=True, exist_ok=True)
    date = date or datetime.now().strftime("%Y-%m-%d")
    p = note_path(wiki_root, title)

    frontmatter = (
        f"# {title.strip()}\n\n"
        f"> tags: {tags}\n"
        f"> created: {date}\n"
        f"> updated: {date}\n"
    )
    if links:
        frontmatter += f"> links: {links}\n"
    p.write_text(frontmatter + "\n---\n\n" + content.strip() + "\n", encoding="utf-8")
    return f"[OK] wiki note: {p.name}"


def regenerate_index(wiki_root: Path) -> str:
    """Rebuild _index.md listing every note with its first heading."""
    if not wiki_root.is_dir():
        return "[ERROR] wiki directory not found"
    notes = sorted(p for p in wiki_root.glob("*.md") if p.name != "_index.md")
    lines = ["# Wiki Index", ""]
    for p in notes:
        first = p.read_text(encoding="utf-8").splitlines()
        heading = first[0].lstrip("# ").strip() if first else p.stem
        lines.append(f"- [[{p.stem}]] {heading}")
    index = wiki_root / "_index.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"[OK] wiki index regenerated ({len(notes)} notes)"


def update_link(wiki_root: Path, title: str, new_link: str) -> str:
    """Add a [[link]] to a note's links line. Returns a result string."""
    p = note_path(wiki_root, title)
    if not p.exists():
        return f"[ERROR] wiki note not found: {title}"
    content = p.read_text(encoding="utf-8")
    if new_link in content:
        return f"[OK] link already present"
    if "> links:" in content:
        # append to existing links line
        content = re.sub(r"(> links:[^\n]*)", lambda m: m.group(1) + f", {new_link}", content, count=1)
    else:
        # add a links line after updated
        content = re.sub(r"(> updated: [^\n]*\n)", lambda m: m.group(1) + f"> links: {new_link}\n", content, count=1)
    p.write_text(content, encoding="utf-8")
    return f"[OK] link added: {new_link}"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: saku wiki <wiki_root> <op> ...")
        return 1
    wiki_root = Path(args[0])
    op = args[1] if len(args) > 1 else "index"
    if op == "index":
        print(regenerate_index(wiki_root))
    return 0


if __name__ == "__main__":
    main()
