"""Load project extensions as isolated Python packages and inventory helpers."""

import hashlib
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

from .project_config import ConfigError

_modules = {}


@contextmanager
def import_context(path):
    """Support relative imports and legacy sibling imports without collisions."""
    from . import backends, project_config

    # Keep existing project extension imports working without global aliases.
    compatibility = {"backends": backends, "project_config": project_config}
    directory = path.parent
    names = {file.stem for file in directory.glob("*.py")}
    names.update(file.name for file in directory.iterdir() if file.is_dir())
    names.update(compatibility)
    previous = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".")[0] in names
    }
    for name in previous:
        del sys.modules[name]
    sys.modules.update(compatibility)
    search_path = sys.path[:]
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in names:
                del sys.modules[name]
        sys.modules.update(previous)
        sys.path[:] = search_path


def load(path):
    path = Path(path).resolve()
    if path in _modules:
        return _modules[path]
    namespace = (
        "_flow_extension_" + hashlib.sha256(str(path.parent).encode()).hexdigest()
    )
    if namespace not in sys.modules:
        package = types.ModuleType(namespace)
        package.__path__ = [str(path.parent)]
        sys.modules[namespace] = package
    name = namespace + ".entry_" + hashlib.sha256(str(path).encode()).hexdigest()
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ConfigError(f"Cannot load extension: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    try:
        with import_context(path):
            specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    _modules[path] = module
    return module


def invoke(path, hook, *arguments):
    path = Path(path).resolve()
    function = getattr(load(path), hook, None)
    if function is None:
        return None
    if not callable(function):
        raise ConfigError(f"{path}: {hook} must be callable")
    with import_context(path):
        return function(*arguments)


def inputs(path):
    # Inventory Python helpers, including imports performed lazily by hooks.
    # Generated outputs and package caches are not extension source directories.
    files = set()
    excluded = {"build", ".git", ".bender", ".venv", "__pycache__"}
    for directory, children, names in os.walk(Path(path).resolve().parent):
        children[:] = [name for name in children if name not in excluded]
        files.update(Path(directory) / name for name in names if name.endswith(".py"))
    return files
