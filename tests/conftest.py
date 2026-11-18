"""Keep the test suite away from the developer's real configuration.

Tests must not read the repository ``config.toml``: it can point at a live memory
root and enable git sync, which would make a test run pull/push a real vault.
``SAKU_CONFIG`` is set to the shipped example once per session; individual tests
may still override it and are expected to restore the previous value.
"""

import os
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"

os.environ["SAKU_CONFIG"] = str(EXAMPLE)
