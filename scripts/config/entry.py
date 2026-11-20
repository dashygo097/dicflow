"""Stable bootstrap API 2 entry for existing downstream Bender locks."""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit(
        "dicflow requires Python 3.11+; select it with BOOTSTRAP_PYTHON or use uv."
    )

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dicflow.engine.entry import main

if __name__ == "__main__":
    raise SystemExit(main())
