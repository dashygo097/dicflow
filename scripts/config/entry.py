"""Shared command entry; owns project environments and output lifecycle."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from project_config import ConfigError, project_lock


def main():
    root = Path(os.environ["PROJECT_ROOT"]).resolve()
    if os.environ.get("PROJECT_BOOTSTRAP_API") != "2":
        raise ConfigError("Use the bootstrap API 2 Make entry")
    lock = root / "Bender.lock"
    action = sys.argv[1]
    environment = dict(
        os.environ,
        PYTHON_BIN=sys.executable,
        PROJECT_VENV_DIR=str(root / "build/.venv"),
    )
    with project_lock(root):
        if (
            hashlib.sha256(lock.read_bytes()).hexdigest()
            != os.environ["PROJECT_BOOTSTRAP_LOCK"]
        ):
            raise ConfigError("Dependency lock changed after bootstrap")
        subprocess.run(
            ["bash", str(Path(__file__).with_name("setup_venv.sh"))],
            env=environment,
            check=True,
        )
    if action == "env":
        return
    executable = root / "build/.venv/bin/python"
    engine = Path(__file__).with_name("flow.py")
    os.execve(executable, [str(executable), str(engine), *sys.argv[1:]], environment)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
