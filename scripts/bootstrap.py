"""Compatibility entry for callers using this framework checkout directly."""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("dicflow requires Python 3.11+; use uv tool install .")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dicflow.bootstrap import main
from dicflow.console import emit

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        emit("ERROR", str(error), stream=sys.stderr)
        raise SystemExit(1)
