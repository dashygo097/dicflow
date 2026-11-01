"""Shared module-verification configuration and runtime plan."""

import json
import math
import os
import re
import sys
from pathlib import Path

from project_config import ConfigError, atomic_write, number, reject_unknown

RUNTIME_OPTIONS = ["seeds", "timeout_seconds"]


def configure(config, settings, root):
    reject_unknown(
        settings,
        {
            "backend",
            "testbench",
            "targets",
            "options",
            "seeds",
            "timeout_seconds",
            "sources",
            "generators",
            "technologies",
            "version",
            "parameters",
            "requires",
            "inputs",
            "_backend",
        },
        "flow.verify",
    )
    testbench = settings.get("testbench")
    if not isinstance(testbench, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", testbench
    ):
        raise ConfigError("flow.verify.testbench: expected a module identifier")
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
            "flow.verify.seeds: expected distinct positive 31-bit integers"
        )
    settings["timeout_seconds"] = number(
        settings.get("timeout_seconds", 120),
        "flow.verify.timeout_seconds",
        positive=True,
    )
    if not isinstance(settings.setdefault("sources", []), list):
        raise ConfigError("flow.verify.sources: expected Bender source groups")
    parameters = settings.setdefault("parameters", {})
    if not isinstance(parameters, dict) or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name)
        or not isinstance(value, (str, int, float, bool))
        or (isinstance(value, float) and not math.isfinite(value))
        for name, value in parameters.items()
    ):
        raise ConfigError(
            "flow.verify.parameters: expected scalar testbench parameter values"
        )
    settings["targets"] = list(dict.fromkeys(["verify", *settings["targets"]]))


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
