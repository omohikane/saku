"""SAKU CLI entry point.

    saku chat     interactive mode
    saku daemon   background daemon
    saku ui       Web UI
    saku status   growth summary (read-only)
    saku sync     commit/push the memory repository now ([git] sync)
    saku setup    initialize the memory/vault structure
    saku dream    run a dreaming cycle
    saku mcp      MCP server
"""

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "chat"

    if cmd in ("chat", "interactive"):
        from saku import core

        core.main()
        return 0

    if cmd == "daemon":
        from saku import daemon

        daemon.main()
        return 0

    if cmd in ("ui", "serve"):
        from saku import config as saku_config
        from saku.ui import serve

        _cfg_ui, _ = saku_config.load_config()
        host = _cfg_ui.get("ui", {}).get("host", "127.0.0.1")
        port = int(_cfg_ui.get("ui", {}).get("port", 8787))
        auto_daemon = not ("--no-daemon" in args)
        serve(host, port, auto_daemon=auto_daemon)
        return 0

    if cmd in ("mcp", "mcp-server"):
        from saku.mcp_server import main as mcp_main

        mcp_main()
        return 0

    if cmd == "setup":
        from saku.setup import main as setup_main

        return setup_main(args[1:])

    if cmd == "dream":
        from saku.dreaming import main as dream_main

        return dream_main(args[1:])

    if cmd == "sync":
        from saku import vault_git

        result = vault_git.sync()
        print(f"[{result['action']}] {result['message']}")
        return 0 if result.get("ok") else 1

    if cmd == "status":
        from saku.status import main as status_main

        return status_main(args[1:])

    print("Usage: saku [chat|daemon|ui|status|sync|setup|dream|mcp]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
