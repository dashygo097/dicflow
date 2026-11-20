"""Worker entry usable both as a module and by generated Make recipes."""

import sys
from pathlib import Path

if not __package__:
    # Select this locked checkout even when another dicflow is installed.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"execute", "execute-run"}:
        # The scheduler owns the project lock until every worker finishes.
        # Workers validate their prepared snapshots, not the bootstrap handoff;
        # reacquiring that lock here deadlocks against the waiting scheduler.
        from dicflow.engine.flow import main
    else:
        from dicflow.engine.entry import main

    raise SystemExit(main())
