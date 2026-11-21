"""Human-facing build references and offline inspection, separate from cache authority."""

import json
import os
from pathlib import Path

from .console import detail, emit, heading, note, plain, relative_path
from .filesystem import atomic_write as write


def label(entry):
    return " / ".join(
        str(entry[key])
        for key in ("stage", "technology", "tool", "variant")
        if key in entry
    )


def reference(path, target):
    """Replace only our symlink, never a user's directory or file."""
    if path.exists() and not path.is_symlink():
        raise ValueError(f"Cannot publish build reference over existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{os.getpid()}")
    try:
        temporary.symlink_to(
            os.path.relpath(target, path.parent), target_is_directory=True
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def begin(root, entry, directory):
    base = root / "build" / entry["id"]
    # A build directory is mutable. Remove any success reference it will destroy,
    # including a retained runtime directory nested underneath that build.
    passed = base.parent / "passed"
    if passed.is_symlink() and passed.resolve().is_relative_to(directory.resolve()):
        passed.unlink()
    reference(base.parent / "latest", directory)
    reference(base.parent / "inputs", root / "build/generated/flows" / entry["id"])


def finish(root, entry, directory, succeeded):
    base = root / "build" / entry["id"]
    if succeeded:
        reference(base.parent / "passed", directory)


def record_paths(stage):
    """Prefer latest references; scan legacy layouts without entering tool outputs."""
    for directory, children, files in os.walk(stage, followlinks=False):
        directory = Path(directory)
        latest = directory / "latest"
        if latest.is_symlink():
            target = latest.resolve()
            if (
                target.is_relative_to(stage.resolve())
                and (target / "run.json").is_file()
            ):
                yield target / "run.json"
                children[:] = []
                continue
        children[:] = [
            name
            for name in children
            if name not in {"work", "reports", "results", "logs"}
            and not (directory / name).is_symlink()
        ]
        if "run.json" in files:
            yield directory / "run.json"


def records(root):
    build = root / "build"
    found = []
    if not build.is_dir():
        return found
    # Do not traverse aliases, environments or arbitrary project directories.
    for stage in sorted(build.iterdir()):
        if stage.is_symlink() or not (stage / ".flow-stage").is_file():
            continue
        for path in record_paths(stage):
            try:
                record = json.loads(path.read_text())
                entry = record["inputs"]["entry"]
                started = record.get("started_epoch", 0)
                finished = record.get("finished_epoch", started)
                item = {
                    "execution": label(entry),
                    "status": record["status"],
                    "finished_epoch": record.get("finished_epoch"),
                    "updated_epoch": finished,
                    "duration_seconds": max(0, finished - started),
                    "directory": str(path.parent.relative_to(root)),
                    "log": str((path.parent / "logs/run.log").relative_to(root)),
                    "artifacts": record.get("artifacts", {}),
                    "output_roles": record.get("output_roles", {}),
                    "error": record.get("error"),
                    "freshness": "not_checked",
                }
                found.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    latest = {}
    for item in sorted(
        found, key=lambda item: (item["updated_epoch"], item["directory"])
    ):
        latest[item["execution"]] = item
    return [latest[key] for key in sorted(latest)]


def overview(root):
    items = records(root)
    lines = [
        "dicflow build overview",
        "",
        "Recorded outcomes only; freshness against current inputs has not been checked.",
        "latest = last attempt; passed = last retained success (may be stale).",
        "inputs = prepared scripts and source lists; generated/ = generated HDL and flow inputs.",
        "Dependency execution uses checked manifests, never these references.",
        "",
    ]
    for item in items:
        lines += [
            f"{item['status'].upper():8} {item['execution']} ({item['duration_seconds']:.1f}s)",
            f"  Log: {item['log']}",
            f"  Outputs: {item['directory']}/",
        ]
        if item["error"]:
            lines.append("  Reason: " + plain(item["error"]))
    if not items:
        lines.append("No completed executions recorded.")
    write(root / "build/overview.txt", "\n".join(lines) + "\n")


def show(root, action="status", selector="", *, json_output=False):
    """Render recorded outcomes without invoking tools or validating the cache."""
    root = root.resolve()
    items = [item for item in records(root) if selector in item["execution"]]
    if json_output:
        print(json.dumps(items, indent=2, sort_keys=True))
        return 0
    if not items:
        heading(root.name, action)
        emit("INFO", "No matching executions recorded.")
        return 0
    noun = "execution" if len(items) == 1 else "executions"
    heading(root.name, f"{len(items)} recorded {noun}")
    note("Results are recorded; current inputs have not been checked.")
    print()
    for item in items:
        emit(
            item["status"].upper(),
            item["execution"],
            suffix="unfinished"
            if item["status"] == "incomplete"
            else f"{item['duration_seconds']:.1f}s",
        )
        if action == "artifacts":
            for role, path in sorted(item["output_roles"].items()):
                detail(role, relative_path(path, root))
            named = set(item["output_roles"].values())
            for path in sorted(set(item["artifacts"]) - named):
                detail("artifact", relative_path(path, root))
        elif action == "logs":
            detail("log", item["log"])
        else:
            detail("outputs", item["directory"])
            if item["error"]:
                detail("reason", item["error"])
    return 0
