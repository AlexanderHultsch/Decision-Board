#!/usr/bin/env python3
"""Setup wizard launcher, so it runs without installing the package.

    python scripts/setup.py            interactive
    python scripts/setup.py --yes --vault "C:/Users/you/OneDrive/Vault"

Checks Python, git and OpenCode, writes config/config.local.json with
everything preselected, lets you choose the knowledge source with the
folder dialog, and makes one real test call to the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from programmind.setup_wizard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
