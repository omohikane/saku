"""SAKU CLI entry point.

    saku chat     interactive mode
    saku daemon   background daemon
    saku ui       Web UI
    saku status   growth summary (read-only)
    saku stop     engage the kill switch (stop autonomous operation)
    saku resume   release the kill switch
    saku tidy     deterministic cleanup of MEMORY.md / wiki (dry-run default)
    saku sort     classify Clippings into existing reference folders (proposal first)
    saku do       run a known command from a natural-language request
    saku heartbeat  show/check the daemon liveness signal
    saku sync     commit/push the memory repository now ([git] sync)
    saku bootstrap  guided setup (memory / bind / auth / systemd or docker)
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

    if cmd == "bootstrap":
        from saku.bootstrap import main as bootstrap_main

        return bootstrap_main(args[1:])

    if cmd == "sync":
        from saku import vault_git

        result = vault_git.sync()
        print(f"[{result['action']}] {result['message']}")
        return 0 if result.get("ok") else 1

    if cmd == "status":
        from saku.status import main as status_main

        return status_main(args[1:])

    if cmd in ("stop", "resume", "killswitch"):
        from saku.killswitch import main as killswitch_main

        if cmd == "killswitch":
            sub = args[1:] or ["status"]
        else:
            sub = [{"stop": "engage", "resume": "release"}[cmd]] + args[1:]
        return killswitch_main(sub)

    if cmd == "tidy":
        from saku.tidy import main as tidy_main

        return tidy_main(args[1:])

    if cmd == "sort":
        from saku.sort import main as sort_main

        return sort_main(args[1:])

    if cmd in ("do", "route"):
        from saku.do import main as do_main

        return do_main(args[1:])

    if cmd == "heartbeat":
        from saku.heartbeat import main as heartbeat_main

        return heartbeat_main(args[1:])

    print("Usage: saku [chat|daemon|ui|status|tidy|sort|do|sync|setup|bootstrap|dream|mcp|stop|resume]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
