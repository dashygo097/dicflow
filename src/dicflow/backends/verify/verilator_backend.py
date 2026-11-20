"""Independent timed SystemVerilog module verification using Verilator."""

import os

from dicflow.engine.backends import parameter_value
from dicflow.engine.project_config import ConfigError

from .common import RUNTIME_OPTIONS, collect
from .common import configure as configure_common
from .common import run, variants

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
            "{top}",
            "--Mdir",
            "{work}/simulator",
            "-o",
            "simv",
            "--build-jobs",
            "{jobs}",
            "-f",
            "{inputs}/sources.f",
            "{_parameter_args}",
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
        task["_parameter_args"] = [
            f"-G{name}={parameter_value(value)}"
            for name, value in task["parameters"].items()
        ]


def tools(config, entry):
    return {
        "c++": [os.environ.get("CXX", "c++"), "--version"],
        "make": ["make", "--version"],
    }
