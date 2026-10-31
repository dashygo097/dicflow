"""Shared command entry; owns project environments and output lifecycle."""

import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from project_config import ConfigError


def clean(root, *, entire):
    build = root / "build"
    if build.exists():
        for directory in build.iterdir():
            if directory.is_dir() and (directory / ".flow-stage").is_file():
                shutil.rmtree(directory)
        shutil.rmtree(build / "generated", ignore_errors=True)
    manifest = root / "Bender.yml"
    if manifest.is_file() and manifest.read_text().startswith(
        "# Generated from Project.toml;"
    ):
        manifest.unlink()
    if entire:
        shutil.rmtree(build, ignore_errors=True)
        (root / "Project.toml").unlink(missing_ok=True)
    print(
        "[DISTCLEAN] build/ and Project.toml removed"
        if entire
        else "[CLEAN] Flow outputs removed; environment and lock preserved"
    )


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
    with (root / "build/.project.lock").open("a") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        if (
            hashlib.sha256(lock.read_bytes()).hexdigest()
            != os.environ["PROJECT_BOOTSTRAP_LOCK"]
        ):
            raise ConfigError("Dependency lock changed after bootstrap")
        if action in {"clean", "distclean"}:
            clean(root, entire=action == "distclean")
            return
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
