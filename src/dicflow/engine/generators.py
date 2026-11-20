"""Built-in configuration sections and explicit project generators.

A project table with a `generator` path owns its own schema. A generator may
normalize that table in configure(settings, root), and returns relative text
outputs from generate(config, settings, root). GENERATOR declares Bender source
groups and configuration dependencies. Standard sections select bundled
generators without exposing implementation paths in project configuration.
"""

import hashlib
import json
from pathlib import Path

from . import extensions
from .project_config import ConfigError, atomic_write, string_list

BUILTIN_GENERATORS = {
    "constraints": Path(__file__).resolve().parents[1] / "generators/sdc.py",
}


def load(name, settings, root):
    if name in BUILTIN_GENERATORS:
        if "generator" in settings:
            raise ConfigError(f"{name}: built-in section does not accept generator")
        path = BUILTIN_GENERATORS[name]
    else:
        script = settings.get("generator")
        if not isinstance(script, str) or not script:
            raise ConfigError(
                f"{name}: project-specific tables must declare their generator"
            )
        path = (root / script).resolve()
    if not path.is_file():
        raise ConfigError(f"{name}: generator does not exist: {path}")
    module = extensions.load(path)
    if not callable(getattr(module, "generate", None)):
        raise ConfigError(f"{path}: missing generate(config, settings, root)")
    description = getattr(module, "GENERATOR", {})
    if not isinstance(description, dict):
        raise ConfigError(f"{path}: GENERATOR must be a table")
    description = dict(description)
    if not isinstance(description.get("sources", []), list):
        raise ConfigError(f"{path}: sources must contain Bender source groups")
    if any(key.startswith("_") for key in settings):
        raise ConfigError(f"{name}: keys starting with '_' are reserved")
    description["definition"] = str(path)
    description["requires"] = string_list(description.get("requires", []), name)
    settings["_generator"] = description
    extensions.invoke(path, "configure", settings, root)
    return settings


def entries(config):
    return {
        name: settings
        for name, settings in config.items()
        if isinstance(settings, dict) and "_generator" in settings
    }


def generate(config, root):
    configured = entries(config)
    pending, finished = set(), set()
    outputs, sources = {}, {}

    def visit(name):
        if name in finished:
            return
        if name in pending:
            raise ConfigError(f"Cyclic generator dependency: {name}")
        if name not in configured:
            raise ConfigError(f"Missing generator dependency: {name}")
        pending.add(name)
        settings = configured[name]
        description = settings["_generator"]
        for dependency in description["requires"]:
            visit(dependency)
        generated = extensions.invoke(
            description["definition"], "generate", config, settings, root
        )
        if not isinstance(generated, dict):
            raise ConfigError(f"{name}: generate must return a path-to-text mapping")
        for path, content in generated.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise ConfigError(
                    f"{name}: generated paths and contents must be strings"
                )
        outputs[name] = generated
        sources[name] = description.get("sources", [])
        pending.remove(name)
        finished.add(name)

    for name in configured:
        visit(name)
    return outputs, sources


def publish(root, updates, declared, *, complete):
    directory = root / "build/generated"
    inventory = directory / ".generators.json"
    previous = json.loads(inventory.read_text()) if inventory.exists() else {}
    legacy = directory / ".outputs.json"
    if not previous and legacy.exists():
        previous["legacy"] = {name: None for name in json.loads(legacy.read_text())}
    owners = (
        {name: files for name, files in previous.items() if name in declared}
        if complete
        else dict(previous)
    )
    if not complete and "legacy" in previous:
        owners["legacy"] = previous["legacy"]
    for owner, outputs in updates.items():
        owners[owner] = {
            name: hashlib.sha256(content.encode()).hexdigest()
            for name, content in outputs.items()
        }
    claimed = {}
    for owner, files in owners.items():
        for name, digest in files.items():
            path = directory / name
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ConfigError(
                    "Generated output resolves outside build/generated: " + name
                )
            if owner == "legacy":
                continue
            if name in claimed and claimed[name] != digest:
                raise ConfigError("Conflicting generated output: " + name)
            claimed[name] = digest
    if "legacy" in owners:
        owners["legacy"] = {
            name: digest
            for name, digest in owners["legacy"].items()
            if name not in claimed
        }
    before = {name for files in previous.values() for name in files}
    after = {name for files in owners.values() for name in files}
    for name in before - after:
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ConfigError("Invalid generated inventory path: " + name)
        path.unlink(missing_ok=True)
    for outputs in updates.values():
        for name, content in outputs.items():
            atomic_write(directory / name, content)
    atomic_write(inventory, json.dumps(owners, indent=2, sort_keys=True) + "\n")
    if complete:
        legacy.unlink(missing_ok=True)
