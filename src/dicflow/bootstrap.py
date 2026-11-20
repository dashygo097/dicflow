"""Resolve dependencies before loading dicflow; user workflows are optional."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .console import detail, emit, verbose
from .filesystem import project_lock

# Released in 4c7b2a5: workers reacquire the scheduler's project lock. Match the
# exact defective entry, rather than guessing compatibility from version names.
DEADLOCKING_WORKER_SHA256 = (
    "18657cddeefb60b59bf16af70183e6225c8d4452c9f6a4b79cfbe84924be7c1f"
)


def engine_entry(directory):
    worker = directory / "src/dicflow/engine/__main__.py"
    if worker.is_file():
        if hashlib.sha256(worker.read_bytes()).hexdigest() == DEADLOCKING_WORKER_SHA256:
            raise RuntimeError(
                f"Engine at {directory} contains the known worker-lock deadlock. "
                "Select a fixed revision with dicflow deps update after publishing it, "
                "or use --engine /path/to/fixed/dicflow for local development."
            )
        return worker
    legacy = directory / "scripts/config/entry.py"
    if legacy.is_file():
        return legacy
    raise RuntimeError(f"No compatible engine at {directory}; run dicflow deps update")


def remove_tree(path):
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def clean(root, *, entire):
    # Cleanup must work offline, without config, dependencies or a usable venv.
    build = root / "build"
    if build.is_symlink():
        raise RuntimeError("Refusing to clean a build/ symlink")
    with project_lock(root):
        if entire:
            remove_tree(build)
        else:
            for directory in build.iterdir() if build.exists() else ():
                if directory.is_dir() and (directory / ".flow-stage").is_file():
                    remove_tree(directory)
            remove_tree(build / "generated")
            (build / "overview.txt").unlink(missing_ok=True)
    if entire:
        emit("INFO", "build/ removed; configuration and package metadata preserved")
    else:
        emit(
            "INFO",
            "Flow outputs removed; configuration, environment and lock preserved",
        )


def bender(root, *arguments):
    if verbose():
        emit("INFO", "bender " + " ".join(arguments))
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("BENDER_")
    }
    result = subprocess.run(
        ["bender", "--no-progress", "-d", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def settings_for(root, *, engine_override=False):
    configuration = root / "Project.toml"
    if not configuration.is_file():
        raise RuntimeError(
            "Project.toml is required for this workflow; run dicflow init"
        )
    with configuration.open("rb") as stream:
        project = tomllib.load(stream)
    if engine_override and project.get("build", {}).get("reproducible", False):
        raise RuntimeError(
            "--engine is a development override and cannot be used with build.reproducible"
        )
    if "framework" in project:
        raise RuntimeError("Remove [framework]; declare dicflow as a Bender dependency")
    settings = project.get("bender", {})
    if not isinstance(settings, dict) or set(settings) - {"version"}:
        raise RuntimeError(
            "[bender] may only pin version; dependencies belong to Bender.yml "
            "and project sources to [design]"
        )
    if "version" in settings and (
        not isinstance(settings["version"], str) or not settings["version"]
    ):
        raise RuntimeError("bender.version must be a version string")
    return settings


def main(arguments=None, *, engine=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    action = arguments[0] if arguments else "help"
    root = Path(os.environ.get("PROJECT_ROOT", os.getcwd())).resolve()
    if action in {"clean", "distclean"}:
        clean(root, entire=action == "distclean")
        return
    if action == "help":
        print(
            "Use dicflow --help for commands; make <stage> remains a compatibility entry."
        )
        return
    if action in {"status", "artifacts", "logs"}:
        from .outputs import show

        return show(root, action, os.environ.get("SELECT", ""))
    updating = action == "deps-update"
    manifest = root / "Bender.yml"
    if not manifest.is_file():
        raise RuntimeError("Dependencies must be declared in the repository Bender.yml")
    if updating and engine is not None:
        raise RuntimeError(
            "--engine selects an execution engine; omit it for dependency updates"
        )
    settings = (
        {} if updating else settings_for(root, engine_override=engine is not None)
    )
    emit(
        "INFO", "Updating dependencies" if updating else "Resolving locked dependencies"
    )
    if (
        settings.get("version")
        and bender(root, "--version") != "bender " + settings["version"]
    ):
        raise RuntimeError("Bender version does not match the configured version")
    if (root / "Bender.local").exists():
        raise RuntimeError("Bender.local overrides are not allowed in locked workflows")
    lock = root / "Bender.lock"
    with project_lock(root):
        previous = lock.read_bytes() if lock.exists() else None
        previous_manifest = manifest.read_bytes() if manifest.exists() else None
        try:
            if not updating and previous is None:
                raise RuntimeError("Bender.lock is missing; run dicflow deps update")
            if updating:
                bender(root, "update", "--fetch")
            if json.loads(bender(root, "config")).get("overrides"):
                raise RuntimeError("Bender overrides bypass the dependency declaration")
            locked = lock.read_bytes()
            emit("INFO", "Checking out locked packages")
            bender(root, "checkout")
            if lock.read_bytes() != locked:
                raise RuntimeError("Ordinary checkout changed Bender.lock")
            if manifest.read_bytes() != previous_manifest:
                raise RuntimeError(
                    "Bender.yml changed during dependency resolution; rerun the command"
                )
            if not updating:
                locked_directory = Path(bender(root, "path", "dicflow")).resolve()
                directory = engine.resolve() if engine is not None else locked_directory
                emit(
                    "INFO",
                    "Using development engine override"
                    if engine is not None
                    else "Using locked engine",
                )
                detail("engine", directory)
                entry = engine_entry(directory)
        except BaseException:
            if previous is None:
                if lock.exists():
                    lock.unlink()
            else:
                lock.write_bytes(previous)
            raise
    if updating:
        emit(
            "INFO",
            "Bender dependencies updated; Bender.lock records resolved revisions",
        )
        return
    environment = dict(
        os.environ,
        PROJECT_ROOT=str(root),
        PROJECT_FRAMEWORK_ROOT=str(directory),
        PROJECT_FRAMEWORK_SOURCE="override" if engine is not None else "locked",
        PROJECT_LOCKED_FRAMEWORK_ROOT=str(locked_directory),
        PROJECT_BOOTSTRAP_API="2",
        PROJECT_BOOTSTRAP_LOCK=hashlib.sha256(locked).hexdigest(),
        PYTHONDONTWRITEBYTECODE="1",
    )
    os.execve(sys.executable, [sys.executable, str(entry), *arguments], environment)
