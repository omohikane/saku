"""SAKU CLI エントリポイント。

    saku chat     対話モード（従来の saku_core.py）
    saku daemon   バックグラウンドデーモン（従来の daemon.py）
    saku ui       Web UI（Phase B 予定）
    saku mcp      MCP サーバ（Phase C 予定）

従来の src/ 配下モジュール（saku_core.py / daemon.py / reflect.py）へ委譲する。
将来、Phase B/C でそれらのコードをこのパッケージへ移設する。
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "chat"

    if cmd in ("chat", "interactive"):
        import saku_core

        saku_core.main()
        return 0

    if cmd == "daemon":
        import daemon

        daemon.main()
        return 0

    if cmd in ("ui", "serve"):
        from saku.ui import main as ui_main

        ui_main()
        return 0

    if cmd in ("mcp", "mcp-server"):
        print("[saku] 'mcp' は Phase C で実装予定です。")
        return 0

    print("Usage: saku [chat|daemon|ui|mcp]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
