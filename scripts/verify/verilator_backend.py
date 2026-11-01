"""Independent timed SystemVerilog module verification using Verilator."""

import os

from backends import parameter_value

from .common import RUNTIME_OPTIONS, collect
from .common import configure as configure_common
from .common import run

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
            "{testbench}",
            "--Mdir",
            "{work}/simulator",
            "-o",
            "simv",
            "--build-jobs",
            "{jobs}",
            "-f",
            "{inputs}/sources.f",
            "{options}",
        ]
    ],
    "outputs": ["{work}/simulator", "{reports}/summary.rpt"],
}


def configure(config, settings, root):
    configure_common(config, settings, root)
    argv = list(BACKEND["commands"][0])
    argv.extend(
        f"-G{name}={parameter_value(value)}"
        for name, value in settings["parameters"].items()
    )
    settings["commands"] = [argv]


def tools(config, entry):
    return {
        "c++": [os.environ.get("CXX", "c++"), "--version"],
        "make": ["make", "--version"],
    }
