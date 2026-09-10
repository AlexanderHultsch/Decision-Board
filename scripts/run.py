#!/usr/bin/env python3
"""Launcher for the CLI, so it runs without installing the package.

    python scripts/run.py serve     (browser interface)
    python scripts/run.py board     (command line)

Run ``python scripts/setup.py`` once first: it writes the configuration
this needs.

Deliberately not named ``programmind.py``: a same-named script placed on
``sys.path`` alongside ``scripts/`` would shadow the real ``programmind``
package under ``src/`` - Python resolves the flat module first and every
subsequent ``import programmind...`` anywhere in the process reuses that
wrong, cached module.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from programmind.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
