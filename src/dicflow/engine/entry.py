"""Validate the bootstrap handoff and run in the caller's Python environment."""

import hashlib
import os
import sys
from pathlib import Path

from ..console import emit
from ..filesystem import project_lock
from .project_config import ConfigError


def run():
    root = Path(os.environ["PROJECT_ROOT"]).resolve()
    if os.environ.get("PROJECT_BOOTSTRAP_API") != "2":
        raise ConfigError("Use dicflow or the bootstrap API 2 compatibility entry")
    with project_lock(root):
        digest = hashlib.sha256((root / "Bender.lock").read_bytes()).hexdigest()
        if digest != os.environ.get("PROJECT_BOOTSTRAP_LOCK"):
            raise ConfigError("Dependency lock changed after bootstrap")
    if sys.argv[1] == "env":
        emit("INFO", "Python environment ready: " + sys.executable)
        return 0
    if sys.argv[1] in {"status", "artifacts", "logs"}:
        from ..outputs import show

        return show(root, sys.argv[1], os.environ.get("SELECT", ""))
    from .flow import main as run_flow

    return run_flow()


def main():
    try:
        return run()
    except KeyboardInterrupt:
        emit("WARNING", "Interrupted", stream=sys.stderr)
        return 130
    except (ConfigError, OSError, ValueError, RuntimeError) as error:
        emit("ERROR", str(error), stream=sys.stderr)
        return 1
