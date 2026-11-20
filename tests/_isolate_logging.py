"""Test helper: keep the daemon's file logging out of the owner's real vault (#42).

``saku.daemon`` attaches a RotatingFileHandler to ``MEMORY_ROOT/state/saku.log``
at import time, so merely importing it from a test writes test noise
(``[tick-test]``, ``[inbox-test]``) into the Owner's real log.

Call ``isolate_daemon_logging(daemon_mod.logger)`` right after the import; records
still go somewhere real (a temp file), so the code paths under test keep exercising
the logger.
"""

import logging
import os
import tempfile
from pathlib import Path

_LOG_DIR = tempfile.mkdtemp(prefix="saku-test-log-")
# Direct `python tests/test_x.py` runs do not load conftest.py; keep the audit
# log out of the Owner's memory here too.
os.environ.setdefault("SAKU_AUDIT_FILE", str(Path(_LOG_DIR) / "audit.log"))
_HANDLERS: dict[int, logging.Handler] = {}


def isolate_daemon_logging(logger: logging.Logger) -> logging.Handler:
    """Replace ``logger``'s file handlers with one writing to a temp file."""
    handler = _HANDLERS.get(id(logger))
    if handler is not None:
        return handler

    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:
            pass

    handler = logging.FileHandler(Path(_LOG_DIR) / "saku.log", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    _HANDLERS[id(logger)] = handler
    return handler
