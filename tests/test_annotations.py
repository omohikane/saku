#!/usr/bin/env python3
"""Guard against annotations that only work on Python 3.14+ (PEP 649).

`requires-python` is 3.11, where function annotations are evaluated when the
function is defined. An annotation that names a class defined *later* in the
module therefore raises NameError at import time. `from __future__ import
annotations` makes them lazy and fixes that on every supported version.

This test walks the package AST and fails when such a forward reference exists
without the future import. It caught `dreaming.Extraction` (used at line ~295,
defined at ~343), which made dreaming/daemon/learning fail to import on 3.11–3.13.
"""

import ast
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).parent
_REPO_ROOT = CODE_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PACKAGE = _REPO_ROOT / "saku"


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _later_references(tree: ast.Module) -> list[tuple[str, str, int, int]]:
    """(function, annotation name, function line, class line) for forward refs."""
    defined: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.setdefault(node.name, node.lineno)
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for annotation in (node.returns, *(a.annotation for a in node.args.args)):
            if annotation is None:
                continue
            for sub in ast.walk(annotation):
                if isinstance(sub, ast.Name) and sub.id in defined:
                    if defined[sub.id] > node.lineno:
                        problems.append((node.name, sub.id, node.lineno, defined[sub.id]))
    return problems


def test_no_module_depends_on_lazy_annotations_by_accident():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        problems = _later_references(tree)
        if problems and not _has_future_annotations(tree):
            offenders.append((path.relative_to(_REPO_ROOT).as_posix(), problems[:3]))
    assert not offenders, (
        "annotations reference names defined later without "
        "`from __future__ import annotations`: " + repr(offenders)
    )


def test_dreaming_defines_extraction_before_it_is_annotated_or_is_lazy():
    """Regression: dreaming used `-> Extraction` before defining the class."""
    tree = ast.parse((PACKAGE / "dreaming.py").read_text(encoding="utf-8"))
    problems = _later_references(tree)
    assert not problems or _has_future_annotations(tree), problems


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"    -> PASS: {fn.__name__}")
    print(f"[*] All {len(tests)} annotation tests PASSED!")


if __name__ == "__main__":
    run_tests()
