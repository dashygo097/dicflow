"""Worker entry usable both as a module and by generated Make recipes."""

import sys
from pathlib import Path

if not __package__:
    # Select this locked checkout even when another dicflow is installed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dicflow.engine.entry import main

if __name__ == "__main__":
    raise SystemExit(main())
