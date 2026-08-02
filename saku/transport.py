"""ツール呼び出しの解析・実行（[[TOOL]] ブロックのディスパッチ）。

メモリルートとコードルートを引数で受け取る（グローバル非依存）。
Phase C では MCP クライアント/サーバとの変換層に拡張する。
"""

import importlib.util
import re
import sys
import traceback
from pathlib import Path


def _tool_candidates(name_lower: str, memory_root: Path, code_root: Path) -> list[Path]:
    return [
        memory_root / "tools" / f"{name_lower}.py",  # SAKU の自作ツール
        code_root / "system_tools" / f"{name_lower}.py",  # システムツール
    ]


def _load_tool(name_lower: str, tool_file: Path):
    """ツールモジュールを動的ロードして run 関数を返す。"""
    module_name = f"_saku_tool_{name_lower}"
    if module_name in sys.modules:
        del sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, tool_file)
    if spec is None:
        raise RuntimeError(f"failed to load tool: {tool_file.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def exec_tools(raw: str, memory_root: Path, code_root: Path) -> list[str]:
    """[[TOOL ...]] ブロックを解析して実行する。

    有効なツールの開始タグが正しく [[END]] で閉じられているかも検証する。
    ツール探索順: memory/tools/ → system_tools/
    """
    results: list[str] = []

    start_pattern = r"\[\[([A-Z_]+)\s*(.*?)\]\]"
    starts = list(re.finditer(start_pattern, raw))
    parsed_ranges = []

    pattern = r"\[\[(\w+)\s*(.*?)\]\]\s*\n(.*?)\n?\[\[END\]\]"
    for m in re.finditer(pattern, raw, re.DOTALL):
        name, args_str, body = m.group(1), m.group(2), m.group(3)
        start_idx, end_idx = m.start(), m.end()
        parsed_ranges.append((start_idx, end_idx))

        name_lower = name.lower()

        tool_file = next((p for p in _tool_candidates(name_lower, memory_root, code_root) if p.exists()), None)
        if tool_file is None:
            results.append(f"[ERROR] unknown tool: {name}")
            continue

        args = dict(re.findall(r'(\w+)="(.*?)"', args_str))
        path = args.get("path", "")

        try:
            module = _load_tool(name_lower, tool_file)
            extra_kwargs = {k: v for k, v in args.items() if k != "path"}
            result = module.run(memory_root, path, body.strip(), **extra_kwargs)
        except Exception as e:
            result = f"[ERROR] {e}\n{traceback.format_exc()}"

        results.append(f"[{name}] {result}")

    # 閉じられていない/不正なツール呼び出しの検証
    for start_match in starts:
        name = start_match.group(1)
        name_lower = name.lower()
        candidates = _tool_candidates(name_lower, memory_root, code_root)
        if not any(p.exists() for p in candidates):
            continue

        start_pos = start_match.start()
        inside_parsed = any(p_start <= start_pos < p_end for p_start, p_end in parsed_ranges)

        if not inside_parsed:
            has_end = "[[END]]" in raw[start_pos:]
            if not has_end:
                results.append(
                    f"[ERROR] Tool [[{name}]] was not closed with [[END]]. Every tool call block must end with [[END]] on its own line."
                )
            else:
                results.append(
                    f"[ERROR] Tool [[{name}]] has invalid syntax. Ensure a newline after the start tag and before [[END]]. Example:\n[[{name} path=\"...\"]]\ncontent\n[[END]]"
                )

    return results
