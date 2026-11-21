"""Initialize an ordinary Project.toml from an explicitly selected TOML file."""

import os
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

from . import SCHEMA_VERSION
from .console import emit, relative_path
from .filesystem import project_lock


def read_template(path):
    # Keep the original bytes: comments, ordering and line endings are user-owned.
    content = path.read_bytes()
    try:
        configuration = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Invalid TOML template {path}: {error}") from error
    version = configuration.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(f"Template {path}: schema_version must be {SCHEMA_VERSION}")
    return content


def publish(path, content, *, force):
    descriptor, temporary = tempfile.mkstemp(prefix=".Project.toml-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if force and path.is_file() and not path.is_symlink():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content)
        if force:
            os.replace(temporary, path)
        else:
            # Publish complete bytes without overwriting even a concurrently
            # created file or a dangling symlink. Both paths share a filesystem.
            os.link(temporary, path)
    except FileExistsError as error:
        raise FileExistsError(
            f"{path.name} already exists; use --dry-run to preview or --force to replace it"
        ) from error
    finally:
        Path(temporary).unlink(missing_ok=True)


def initialize(root, template, *, dry_run=False, force=False):
    # Resolve from the caller's cwd, independently of --root. Paths inside the
    # configuration retain their normal project-relative meaning.
    content = read_template(template.resolve())
    if dry_run:
        sys.stdout.write(content.decode("utf-8"))
        return 0
    destination = root / "Project.toml"
    with project_lock(root):
        existed = destination.exists() or destination.is_symlink()
        publish(destination, content, force=force)
    emit(
        "INFO",
        ("Replaced " if existed else "Created ") + relative_path(destination, root),
    )
    return 0
