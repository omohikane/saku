"""WIKI tool: create/update knowledge-base notes and regenerate the index.

Usage:
- Create/update a note: [[WIKI title="概念名" tags="タグ" links="[[他ノート]]"]]
  note content here
  [[END]]
- Regenerate the index: [[WIKI op="index"]]
  [[END]]
"""

from pathlib import Path


def run(base: Path, path: str = "", body: str = "", **kwargs) -> str:
    from saku.wiki import create_note, regenerate_index, update_link

    wiki_root = base / "wiki"
    op = kwargs.get("op", "")

    if op == "index":
        return regenerate_index(wiki_root)

    if op == "link":
        title = kwargs.get("title", "") or path
        link = kwargs.get("link", "")
        if not title or not link:
            return "[ERROR] 'title' and 'link' required for op=link"
        return update_link(wiki_root, title, link)

    title = kwargs.get("title", "") or path
    tags = kwargs.get("tags", "")
    links = kwargs.get("links", "")
    return create_note(wiki_root, title, body, tags=tags, links=links)
