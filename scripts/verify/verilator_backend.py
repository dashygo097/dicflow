"""Independent timed SystemVerilog module verification using Verilator."""

import json
import os

from backends import parameter_value
from project_config import ConfigError

from .common import RUNTIME_OPTIONS, collect
from .common import command_values as common_command_values
from .common import configure as configure_common
from .common import discover_top, run, variants

BACKEND = {
    "tool": "verilator",
    "version_args": ["--version"],
    "format": "verilator",
    "source": "sources.f",
    "summary": "log",
    "inherit_design": False,
    "run_after_build": True,
    "preserve_runs": True,
    "runtime_options": RUNTIME_OPTIONS,
    "run_outputs": ["{reports}/summary.json", "{reports}/junit.xml"],
    "tracked_environment": ["CC", "CXX", "CXXFLAGS", "VERILATOR_ROOT"],
    "commands": [
        [
            "verilator",
            "--binary",
            "--timing",
            "--assert",
            "--top-module",
            "{verification_top}",
            "--Mdir",
            "{work}/simulator",
            "-o",
            "simv",
            "--build-jobs",
            "{jobs}",
            "-f",
            "{inputs}/sources.f",
            "{verification_parameters}",
            "{options}",
        ]
    ],
    "outputs": ["{work}/simulator", "{reports}/summary.rpt"],
}


def configure(config, settings, root):
    for task in configure_common(config, settings, root):
        if any("." in name for name in task["parameters"]):
            raise ConfigError(
                "flow.verify.verilator.parameters: Verilator -G accepts top-level parameter names"
            )


def prepare(config, entry, root, directory, source):
    top = discover_top(config, entry, root, defines=["VERILATOR=1"])
    return {"verification.json": json.dumps({"top": top}, indent=2) + "\n"}


def command_values(config, entry, inputs):
    values = common_command_values(config, entry, inputs)
    values["verification_parameters"] = [
        f"-G{name}={parameter_value(value)}"
        for name, value in entry["settings"]["parameters"].items()
    ]
    return values


def tools(config, entry):
    return {
        "c++": [os.environ.get("CXX", "c++"), "--version"],
        "make": ["make", "--version"],
    }
