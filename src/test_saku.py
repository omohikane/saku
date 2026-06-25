#!/usr/bin/env python3
"""
Test suite for SAKU tools and dynamic loading engine.
"""

import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))

import saku_core as agent
from system_tools import read_file, list_dir, write_file, search_notes, append_file, delete_file, move_file, grep_code


def _setup_temp_saku() -> tuple[Path, Path]:
    """Create a temporary _saku/ structure for testing."""
    tmp = Path(tempfile.mkdtemp())
    saku = tmp / "_saku"
    saku.mkdir()
    (saku / "identity").mkdir()
    (saku / "blog").mkdir()
    (saku / "journal").mkdir()
    (saku / "monologue").mkdir()
    (saku / "principles").mkdir()
    (saku / "skills").mkdir()
    (saku / "study").mkdir()
    (saku / "tools").mkdir()
    (saku / "state").mkdir()
    (saku / "children").mkdir()
    (saku / "drafts").mkdir()
    (saku / "src").mkdir()
    return tmp, saku


def run_tests():
    print("[*] Running SAKU Integration Tests in New Structure...")

    tmp_root, SAKU_ROOT = _setup_temp_saku()

    # Create required template files
    (SAKU_ROOT / "genome.md").write_text("Mock Genome containing 朔", encoding="utf-8")
    (SAKU_ROOT / "identity" / "soul.md").write_text("# SAKU Core\n\nテスト用のsoulです。", encoding="utf-8")
    (SAKU_ROOT / "meta.md").write_text("## 最近の出来事\n\n- 初期状態\n", encoding="utf-8")

    try:
        # 1. Read identity/soul.md
        print("[1] Test reading identity/soul.md:")
        res = read_file.run(SAKU_ROOT, "identity/soul.md")
        assert "テスト用のsoul" in res, f"Expected soul content, got: {res[:50]}"
        print("    -> PASS")

        # 2. Read file outside vault denial test
        print("[2] Test reading file outside vault:")
        res = read_file.run(SAKU_ROOT, "../../some_file_outside.md")
        assert "[DENY]" in res, f"Expected DENY error, got: {res}"
        print("    -> PASS")

        # 3. List dir inside journal
        print("[3] Test listing journal/ directory:")
        journal_dir = SAKU_ROOT / "journal"
        mock_journal = journal_dir / "2026-06-18.md"
        mock_journal.write_text("Mock journal content", encoding="utf-8")
        res = list_dir.run(SAKU_ROOT, "journal")
        mock_journal.unlink()
        assert "f journal/" in res or "journal" in res, f"Expected journal files, got: {res}"
        print("    -> PASS")

        # 4. Search notes test
        print("[4] Test searching notes for keyword 'mock':")
        search_file = SAKU_ROOT / "blog" / "search_test.md"
        search_file.parent.mkdir(exist_ok=True)
        search_file.write_text("This is a mock draft for search testing.", encoding="utf-8")
        res = search_notes.run(SAKU_ROOT, body="mock")
        if search_file.exists():
            search_file.unlink()
        assert "Found" in res, f"Expected search results, got: {res}"
        print("    -> PASS")

        # 5. Write file to meta.md denial test
        print("[5] Test write_file denial on meta.md:")
        res_write = write_file.run(SAKU_ROOT, "meta.md", "clobbered content")
        assert "[DENY]" in res_write, f"Expected DENY for meta.md write, got: {res_write}"
        print("    -> PASS")

        # 5b. Append_file with heading under ## section
        print("[5b] Test append_file with heading inserts under ## section:")
        meta_content = "## 状態\n\n元の状態\n\n## 最近の出来事\n\n- 既存の出来事\n\n## 次にやりたいこと\n\n- やること1\n"
        meta_path = SAKU_ROOT / "meta.md"
        meta_path.write_text(meta_content, encoding="utf-8")
        res_heading = append_file.run(SAKU_ROOT, "meta.md", "- 新しい出来事", heading="最近の出来事")
        assert "[OK]" in res_heading, f"Expected OK for heading append, got: {res_heading}"
        updated = meta_path.read_text(encoding="utf-8")
        pos_recent = updated.index("## 最近の出来事")
        pos_new = updated.index("- 新しい出来事")
        pos_next = updated.index("## 次にやりたいこと")
        assert pos_recent < pos_new < pos_next, f"Entry not inserted under correct section"
        print("    -> PASS")

        # 5c. Append_file with missing heading returns error
        print("[5c] Test append_file with non-existent heading returns error:")
        res_missing = append_file.run(SAKU_ROOT, "meta.md", "- これは存在しない", heading="存在しない")
        assert "[ERROR]" in res_missing, f"Expected ERROR, got: {res_missing}"
        print("    -> PASS")

        # 5d. Append_file without heading still appends to end
        print("[5d] Test append_file without heading appends to end:")
        meta_path.write_text(meta_content, encoding="utf-8")
        res_nohead = append_file.run(SAKU_ROOT, "meta.md", "- 末尾に追加")
        assert "[OK]" in res_nohead, f"Expected OK, got: {res_nohead}"
        final_content = meta_path.read_text(encoding="utf-8")
        assert final_content.rstrip().endswith("- 末尾に追加"), f"Not appended to end"
        print("    -> PASS")

        # 6. Write file to blog/ (was drafts/)
        print("[6] Test writing to blog/ (replaces old drafts/):")
        res = write_file.run(SAKU_ROOT, "blog/test_blog.md", "# Test Blog\ncontent")
        assert "[OK]" in res, f"Expected OK writing blog/, got: {res}"
        assert (SAKU_ROOT / "blog" / "test_blog.md").exists(), "File not created"
        print("    -> PASS")

        # 7. Write file to tools/ (user tools, not system_tools/)
        print("[7] Test writing to tools/ (SAKU's user tools):")
        res = write_file.run(SAKU_ROOT, "tools/test_user_tool.py", "def run(base, path, body=''):\n    return 'ok'")
        assert "[OK]" in res, f"Expected OK writing tools/, got: {res}"
        assert (SAKU_ROOT / "tools" / "test_user_tool.py").exists(), "File not created"
        print("    -> PASS")

        # 8. Delete file test
        print("[8] Test delete_file:")
        del_file = SAKU_ROOT / "study" / "to_delete.py"
        del_file.write_text("delete me", encoding="utf-8")
        res = delete_file.run(SAKU_ROOT, "study/to_delete.py")
        assert "[OK]" in res, f"Expected OK for delete, got: {res}"
        assert not del_file.exists(), "File was not deleted"
        print("    -> PASS")

        # 9. Move file test
        print("[9] Test move_file:")
        src = SAKU_ROOT / "blog" / "move_src.md"
        dst = SAKU_ROOT / "blog" / "move_dst.md"
        src.write_text("move me", encoding="utf-8")
        res = move_file.run(SAKU_ROOT, "", body="", **{"from": "blog/move_src.md", "to": "blog/move_dst.md"})
        assert "[OK]" in res, f"Expected OK for move, got: {res}"
        assert not src.exists(), "Source still exists"
        assert dst.exists(), "Destination not created"
        dst.unlink()
        print("    -> PASS")

        # 10. Grep code test
        print("[10] Test grep_code in system_tools/ and tools/:")
        res = grep_code.run(SAKU_ROOT, body="def run")
        assert "Found" in res, f"Expected results, got: {res[:100]}"
        print("    -> PASS")

        # 11. Write denial for src/ (system_tools is off-limits)
        print("[11] Test write_file denial for src/system_tools/:")
        res = write_file.run(SAKU_ROOT, "src/system_tools/write_test.py", "test")
        assert "[DENY]" in res, f"Expected DENY for src/, got: {res}"
        print("    -> PASS")

        print("[*] All integration tests PASSED!")

    finally:
        import shutil
        shutil.rmtree(tmp_root)


if __name__ == "__main__":
    run_tests()
