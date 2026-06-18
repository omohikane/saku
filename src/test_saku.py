#!/usr/bin/env python3
"""
Test suite for SAKU tools and dynamic loading engine in the public repository structure.
"""

import sys
from pathlib import Path

# Ensure src/ is in sys.path
CODE_ROOT = Path(__file__).parent
sys.path.append(str(CODE_ROOT))

import saku_core as agent
from tools import read_file, list_dir, write_file, search_notes

def run_tests():
    print("[*] Running SAKU Integration Tests in Public Repository Structure...")
    
    # Resolve SAKU_ROOT from config
    SAKU_ROOT = agent.SAKU_ROOT
    print(f"    Memory root (SAKU_ROOT) resolved to: {SAKU_ROOT}")
    
    # Ensure SAKU_ROOT exists
    SAKU_ROOT.mkdir(parents=True, exist_ok=True)
    
    # Copy genome.md to SAKU_ROOT if not present for testing
    genome_path = SAKU_ROOT / "genome.md"
    temp_copied_genome = False
    if not genome_path.exists():
        src_genome = CODE_ROOT.parent / "identity" / "genome.md"
        if src_genome.exists():
            genome_path.write_text(src_genome.read_text(encoding="utf-8"), encoding="utf-8")
            temp_copied_genome = True
        else:
            # Create a mock genome.md for test purposes
            genome_path.write_text("Mock Genome containing 朔", encoding="utf-8")
            temp_copied_genome = True

    try:
        # 1. Read file test inside memory root
        print("[1] Test reading genome.md inside memory root:")
        res = read_file.run(SAKU_ROOT, "genome.md")
        assert "朔" in res or "Mock" in res, f"Expected '朔' or 'Mock' in genome.md, got: {res}"
        print("    -> PASS")

        # 2. Read file outside vault denial test
        print("[2] Test reading file outside vault:")
        res = read_file.run(SAKU_ROOT, "../../some_file_outside.md")
        assert "[DENY]" in res, f"Expected DENY error, got: {res}"
        print("    -> PASS")

        # 3. List dir inside journal
        print("[3] Test listing journal/ directory:")
        journal_dir = SAKU_ROOT / "journal"
        journal_dir.mkdir(exist_ok=True)
        # Create a mock file in journal/ to list
        mock_journal = journal_dir / "2026-06-18.md"
        mock_journal.write_text("Mock journal content", encoding="utf-8")
        res = list_dir.run(SAKU_ROOT, "journal")
        # Cleanup mock journal
        mock_journal.unlink()
        assert "f journal/" in res or "journal" in res, f"Expected journal files, got: {res}"
        print("    -> PASS")

        # 4. Search notes test
        print("[4] Test searching notes for keyword 'mock':")
        # Write temporary mock file for searching
        search_file = SAKU_ROOT / "drafts" / "search_test.md"
        search_file.parent.mkdir(exist_ok=True)
        search_file.write_text("This is a mock draft for search testing.", encoding="utf-8")
        
        res = search_notes.run(SAKU_ROOT, body="mock")
        # Cleanup search file
        if search_file.exists():
            search_file.unlink()
            
        assert "Found" in res, f"Expected search results, got: {res}"
        print("    -> PASS")

        # 5. Write file to meta.md test (should be allowed now)
        print("[5] Test writing to meta.md (should succeed):")
        meta_path = SAKU_ROOT / "meta.md"
        original_meta = ""
        if meta_path.exists():
            original_meta = meta_path.read_text(encoding="utf-8")
        
        test_content = original_meta + "\n<!-- test temp write -->"
        res = write_file.run(SAKU_ROOT, "meta.md", test_content)
        assert "[OK]" in res, f"Expected OK for meta.md write, got: {res}"
        
        # Verify write and restore
        restored_content = meta_path.read_text(encoding="utf-8")
        assert "<!-- test temp write -->" in restored_content, "Write did not modify file."
        if original_meta:
            meta_path.write_text(original_meta, encoding="utf-8")
        else:
            meta_path.unlink()
        print("    -> PASS")

        # 6. Write file to tools/ temp_test.py (dynamic loading test)
        print("[6] Test dynamic tool writing & loading:")
        tool_code = """
def run(base, path, body=""):
    return f"Dynamic test success: {body}"
"""
        res = write_file.run(SAKU_ROOT, "tools/temp_test.py", tool_code)
        assert "[OK]" in res, f"Expected OK writing tools/temp_test.py, got: {res}"
        
        # Try executing it via exec_tools in agent.py
        exec_res = agent.exec_tools('[[TEMP_TEST]]\nhello dynamic tool\n[[END]]')
        assert len(exec_res) == 1, f"Expected 1 tool output, got: {exec_res}"
        assert "Dynamic test success: hello dynamic tool" in exec_res[0], f"Dynamic execution failed: {exec_res[0]}"
        
        # Clean up temp_test.py
        temp_tool_path = CODE_ROOT / "tools" / "temp_test.py"
        if temp_tool_path.exists():
            temp_tool_path.unlink()
        print("    -> PASS")

        print("[*] All integration tests PASSED!")

    finally:
        # Cleanup genome copy if we made one
        if temp_copied_genome and genome_path.exists():
            genome_path.unlink()

if __name__ == "__main__":
    run_tests()
