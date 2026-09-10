#!/usr/bin/env python3
"""The old name of scripts/run.py, kept for one release. Use:

    python scripts/run.py serve
"""

from __future__ import annotations

import sys
from pathlib import Path

print("scripts/run_board.py is now scripts/run.py; running it for you.", file=sys.stderr)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from programmind.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
