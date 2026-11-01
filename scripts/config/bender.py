"""Locked dependency resolution and disposable project compilation views."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from project_config import ConfigError, atomic_write


def command(root, *arguments):
    environment = dict(os.environ)
    # A caller's BENDER_DIR must never redirect the golden project.
    for key in list(environment):
        if key.startswith("BENDER_"):
            environment.pop(key)
    result = subprocess.run(
        ["bender", "--no-progress", "-d", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    if result.returncode:
        raise ConfigError(
            "bender " + " ".join(arguments) + ":\n" + result.stderr.strip()
        )
    return result.stdout


def path(root, value):
    if not isinstance(value, str):
        raise ConfigError("Bender paths must be strings")
    return str(root / value) if not Path(value).is_absolute() else value


def source(root, value):
    if isinstance(value, str):
        return path(root, value)
    if not isinstance(value, dict):
        raise ConfigError("invalid Bender source group")
    result = dict(value)
    if "files" in result:
        result["files"] = [source(root, item) for item in result["files"]]
    for key in ("include_dirs", "external_flists"):
        if key in result:
            result[key] = [
                dict(item, dir=path(root, item["dir"]))
                if isinstance(item, dict)
                else path(root, item)
                for item in result[key]
            ]
    for key in ("sv", "v", "vhd"):
        if key in result:
            result[key] = path(root, result[key])
    return result


def compilation_root(config, root, targets, generated_sources, *, project_sources=None):
    if project_sources is None:
        project_sources = config["design"]["sources"]
    if not project_sources and not generated_sources:
        return root
    arguments = [part for target in targets for part in ("-t", target)]
    dependencies = json.loads(command(root, "sources", "-f", *arguments))
    # These groups are already resolved by the root's locked Bender graph. The
    # compilation view has no dependencies and cannot resolve newer revisions.
    groups = [
        {key: group[key] for key in ("files", "include_dirs", "defines", "independent")}
        for group in dependencies
    ]
    identity = hashlib.sha256(json.dumps(sorted(set(targets))).encode()).hexdigest()[
        :12
    ]
    directory = root / "build/generated/bender" / identity
    manifest = {
        "package": {"name": "dicflow_build"},
        "frozen": True,
        "sources": [
            *groups,
            *generated_sources,
            *(source(root, group) for group in project_sources),
        ],
    }
    atomic_write(directory / "Bender.yml", json.dumps(manifest, indent=2) + "\n")
    atomic_write(directory / "Bender.lock", "packages: {}\n")
    return directory


def sources(root, targets):
    args = [part for target in targets for part in ("-t", target)]
    groups = json.loads(command(root, "sources", "-f", *args))
    if not groups or not any(group["files"] for group in groups):
        raise ConfigError(
            "Bender resolved no sources for targets: " + ", ".join(targets)
        )
    return groups


def export(root, targets, format_name):
    args = [part for target in targets for part in ("-t", target)]
    # Never let different export formats introduce different implicit target sets.
    content = command(root, "script", "--no-default-target", *args, format_name)
    if format_name == "verilator":
        # Verilator accepts quoted response-file arguments, including options.
        # Keep this tool-specific: quoting a complete +define/+incdir option
        # can make VCS interpret it as a source filename.
        lines = []
        for line in content.splitlines():
            if line and not line.lstrip().startswith(("//", "#")):
                line = '"' + line.replace("\\", "\\\\").replace('"', '\\"') + '"'
            lines.append(line)
        content = "\n".join(lines) + "\n"
    return content


def packages(root, reproducible):
    names = command(root, "packages", "-f").split()
    result = {}
    for name in names:
        directory = Path(command(root, "path", name).strip())
        if not directory.is_dir():
            raise ConfigError("Missing package checkout: " + name)
        git = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        state = {"path": str(directory)}
        # Path packages nested in a parent repo are not separate Git checkouts.
        if git.returncode == 0:
            state["git_root"] = git.stdout.strip()
            state["revision"] = subprocess.check_output(
                ["git", "-C", str(directory), "rev-parse", "HEAD"],
                universal_newlines=True,
            ).strip()
            state["dirty"] = bool(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(directory),
                        "status",
                        "--porcelain",
                        "--untracked-files=all",
                    ],
                    universal_newlines=True,
                ).strip()
            )
        if reproducible and (state.get("dirty") or "revision" not in state):
            raise ConfigError(
                "Reproducible build requires a clean revision-pinned package: " + name
            )
        result[name] = state
    return result


def resolve(config, root, update=False):
    if update:
        raise ConfigError("Dependency updates belong to the Make bootstrap entry")
    actual = subprocess.check_output(["bender", "--version"], text=True).strip()
    version = config["bender"].get("version")
    if version and actual != "bender " + version:
        raise ConfigError("Bender version does not match Project.toml")
    lock = root / "Bender.lock"
    if not lock.is_file():
        raise ConfigError("Bender.lock is missing; run make deps-update")
    before = lock.read_bytes()
    effective = json.loads(command(root, "config"))
    if effective.get("overrides"):
        raise ConfigError("Bender overrides bypass the locked dependency graph")
    command(root, "checkout")
    if lock.read_bytes() != before:
        atomic_write(lock, before.decode())
        raise ConfigError("Ordinary builds must preserve Bender.lock")
    executable = Path(shutil.which("bender"))
    return {
        "version": actual,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256(before).hexdigest(),
        "effective_config": effective,
    }
