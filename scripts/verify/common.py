"""Shared target-based verification configuration and runtime plan.

[flow.verify.vcs] owns common settings. [flow.verify.vcs.verify_fifo] selects
Bender targets verify + verify_fifo, with optional per-target replacements.
TEST=verify_fifo selects that configured target across verification backends.
"""

import json
import math
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

import bender
from project_config import (
    ConfigError,
    atomic_write,
    number,
    reject_unknown,
    string_list,
)

RUNTIME_OPTIONS = ["seeds", "timeout_seconds"]

# These keys configure a backend; any other table names a Bender target.
COMMON_OPTIONS = {
    "targets",
    "options",
    "seeds",
    "timeout_seconds",
    "sources",
    "parameters",
    "inputs",
}
BACKEND_OPTIONS = COMMON_OPTIONS | {
    "backend",
    "generators",
    "technologies",
    "version",
    "requires",
    "_backend",
}


def normalize(settings, location, target=None):
    for key in ("options", "targets", "inputs"):
        string_list(settings.setdefault(key, []), f"{location}.{key}")
    seeds = settings.setdefault("seeds", [1])
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 1 <= seed <= 2147483647
            for seed in seeds
        )
        or len(set(seeds)) != len(seeds)
    ):
        raise ConfigError(
            f"{location}.seeds: expected distinct positive 31-bit integers"
        )
    settings["timeout_seconds"] = number(
        settings.get("timeout_seconds", 120),
        f"{location}.timeout_seconds",
        positive=True,
    )
    if not isinstance(settings.setdefault("sources", []), list):
        raise ConfigError(f"{location}.sources: expected Bender source groups")
    parameters = settings.setdefault("parameters", {})
    if not isinstance(parameters, dict) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*", name)
        or not isinstance(value, (str, int, float, bool))
        or (isinstance(value, float) and not math.isfinite(value))
        for name, value in parameters.items()
    ):
        raise ConfigError(f"{location}.parameters: expected scalar parameter values")
    settings["targets"] = list(
        dict.fromkeys(["verify", *([target] if target else []), *settings["targets"]])
    )


def configure(config, settings, root):
    """Flatten backend defaults into independent, target-selected compilations.

    Child values replace common values, including lists. Bender target selection
    owns the sources; Bender syntax trees identify the runnable testbench root.
    No module name is inferred from a target name or filename.
    """
    tasks = {
        key: value for key, value in settings.items() if key not in BACKEND_OPTIONS
    }
    for name, task in tasks.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name) or not isinstance(
            task, dict
        ):
            raise ConfigError(
                f"flow.verify.{name}: expected a Bender target table; "
                "put shared options on the backend table"
            )
        reject_unknown(task, COMMON_OPTIONS, f"flow.verify.{name}")
    defaults = {key: value for key, value in settings.items() if key not in tasks}
    normalize(defaults, "flow.verify")
    if not tasks:
        settings.update(defaults)
        return [settings]
    settings.clear()
    settings.update(defaults)
    settings["_variants"] = {}
    for name, task in tasks.items():
        resolved = deepcopy(defaults)
        resolved.update(deepcopy(task))
        normalize(resolved, f"flow.verify.{name}", name)
        settings["_variants"][name] = resolved
    return list(settings["_variants"].values())


def variants(config, settings):
    tasks = settings.get("_variants", {})
    selected = os.environ.get("TEST", "")
    if selected:
        available = {
            name
            for backend in config["flow"].get("verify", {}).values()
            for name in backend.get("_variants", {})
        }
        if selected not in available:
            raise ConfigError(
                f"Unknown verification target: {selected}; configured targets: "
                + ", ".join(sorted(available))
            )
        return {name: task for name, task in tasks.items() if name == selected}
    return tasks if tasks else None


def syntax_nodes(value):
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            # Macro definitions/comments in token trivia are not design instances.
            if key != "trivia":
                yield from syntax_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from syntax_nodes(child)


def discover_top(config, entry, root, *, includes=(), defines=()):
    """Use Bender's SV parser, never a filename convention or RTL text regex.

    A runnable module testbench has no external ports and is not instantiated
    by another selected design unit. Require one such root per target/run.
    Native simulator top options remain an escape hatch for unusual harnesses.
    """
    arguments = entry["settings"]["options"]
    for flag in ("-top", "--top-module"):
        if flag in arguments:
            index = arguments.index(flag)
            if index + 1 == len(arguments):
                raise ConfigError(f"{flag} requires a module name")
            return arguments[index + 1]
    parser_args = [
        item for target in entry["settings"]["targets"] for item in ("-t", target)
    ]
    parser_args.extend(item for path in includes for item in ("-I", str(path)))
    parser_args.extend(item for define in defines for item in ("-D", define))
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in ("-I", "-D"):
            if index + 1 == len(arguments):
                raise ConfigError(f"{argument} requires a value")
            parser_args.extend([argument, arguments[index + 1]])
            index += 1
        elif argument.startswith(("-I", "-D")):
            parser_args.extend([argument[:2], argument[2:]])
        elif argument.startswith(("+incdir+", "+define+")):
            prefix = "+incdir+" if argument.startswith("+incdir+") else "+define+"
            for value in argument[len(prefix) :].split("+"):
                parser_args.extend(["-I" if prefix == "+incdir+" else "-D", value])
        index += 1
    # Include technology targets as well as verification target flags.
    targets = config["technology"][entry["technology"]]["targets"]
    parser_args.extend(item for target in targets for item in ("-t", target))
    syntax = json.loads(
        bender.command(
            Path(entry["source_root"]),
            "pickle",
            "--ast-json",
            *parser_args,
        )
    )
    modules, instantiated = set(), set()
    for tree in syntax:
        for member in tree["root"].get("members", []):
            if member.get("kind") in {"ModuleDeclaration", "ProgramDeclaration"}:
                header = member["header"]
                if not header.get("ports", {}).get("ports"):
                    modules.add(header["name"]["text"])
        instantiated.update(
            node["type"]["text"]
            for node in syntax_nodes(tree)
            if node.get("kind") == "HierarchyInstantiation"
        )
    candidates = sorted(modules - instantiated)
    if len(candidates) != 1:
        raise ConfigError(
            "Verification target must select one uninstantiated testbench without "
            "external ports; found: "
            + (", ".join(candidates) or "none")
            + ". Refine the Bender target or use a native simulator top option."
        )
    return candidates[0]


def command_values(config, entry, inputs):
    metadata = json.loads((inputs / "verification.json").read_text())
    return {"verification_top": metadata["top"]}


def collect(config, entry, directory, snapshot):
    executable = directory / "work/simulator/simv"
    if (
        not executable.is_file()
        or not executable.stat().st_size
        or not os.access(executable, os.X_OK)
    ):
        raise ConfigError(
            "Compiler did not produce an executable verification simulator"
        )


def run(config, entry, directory, *, case_arguments=()):
    # Backend-owned argv templates are expanded per seed by the runner.
    # {case} names its isolated working directory; {seed} names the current seed.
    settings = entry["settings"]
    executable = directory / "work/simulator/simv"
    if not executable.is_file():
        raise ConfigError("Verification simulator is missing: " + str(executable))
    plan = Path(entry["run_directory"]) / "work/plan.json"
    atomic_write(
        plan,
        json.dumps(
            {
                "seeds": settings["seeds"],
                "arguments": list(case_arguments),
                "timeout_seconds": settings["timeout_seconds"],
            },
            indent=2,
        )
        + "\n",
    )
    return {
        "commands": [
            [
                sys.executable,
                str(Path(__file__).with_name("runner.py")),
                "--simulator",
                settings["_backend"]["tool"],
                "--executable",
                str(executable),
                "--plan",
                str(plan),
            ]
        ],
        "inputs": [str(plan), str(executable.parent)],
    }
