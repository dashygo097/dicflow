"""Built-in configuration sections and explicit project generators.

A project table with a `generator` path owns its own schema. A generator may
normalize that table in configure(settings, root), and returns relative text
outputs from generate(config, settings, root). GENERATOR declares Bender source
groups and configuration dependencies. Standard sections select bundled
generators without exposing implementation paths in project configuration.
"""

from pathlib import Path

import extensions
from project_config import ConfigError, string_list

BUILTIN_GENERATORS = {
    "constraints": Path(__file__).resolve().parents[1] / "constraints/sdc.py",
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
    outputs, sources = {}, []

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
            if path in outputs:
                raise ConfigError(f"Multiple generators own output: {path}")
            if not isinstance(path, str) or not isinstance(content, str):
                raise ConfigError(
                    f"{name}: generated paths and contents must be strings"
                )
            outputs[path] = content
        sources.extend(description.get("sources", []))
        pending.remove(name)
        finished.add(name)

    for name in configured:
        visit(name)
    return outputs, sources
