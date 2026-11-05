"""Independent SystemVerilog/UVM module verification using VCS.

Each completed test calls df_verify_pkg::pass_test(check_count).
The testbench owns test selection. UVM tops set finish_on_completion=0
and report completion only after run_test returns,
so UVM check/report phases run before pass_test is called.
Type parameters are bound in the testbench. Value parameter overrides accept
root parameter names or explicit hierarchical names such as "tb_fifo.DEPTH".
"""

import json
import os
from pathlib import Path

from backends import parameter_value

from .common import RUNTIME_OPTIONS, collect
from .common import command_values as common_command_values
from .common import configure as configure_common
from .common import discover_top
from .common import run as run_common
from .common import variants

BACKEND = {
    "tool": "vcs",
    "version_args": ["-ID"],
    "format": "flist-plus",
    "source": "sources.f",
    "summary": "log",
    "inherit_design": False,
    "run_after_build": True,
    "preserve_runs": True,
    "runtime_options": RUNTIME_OPTIONS,
    "tracked_environment": ["VCS_HOME", "VCS_TARGET_ARCH", "VCS_UVM_HOME"],
    "run_outputs": ["{reports}/summary.json", "{reports}/junit.xml"],
    "commands": [
        ["mkdir", "-p", "{work}/simulator"],
        [
            "vcs",
            "-sverilog",
            "-top",
            "{verification_top}",
            "-Mdir={work}/simulator",
            "-o",
            "{work}/simulator/simv",
            "-file",
            "{inputs}/sources.f",
            "{verification_parameters}",
            "{options}",
        ],
    ],
    "outputs": ["{work}/simulator", "{reports}/summary.rpt"],
}


def configure(config, settings, root):
    for task in configure_common(config, settings, root):
        # Probe in the explicitly requested mode; otherwise defer to VCS.
        mode_options = [option for option in task["options"] if option == "-full64"]
        task["_backend"]["version_args"] = [*mode_options, "-ID"]


def prepare(config, entry, root, directory, source):
    includes = []
    options = entry["settings"]["options"]
    # VCS supplies the built-in UVM headers implicitly. Give Bender's parser the
    # same header directory without compiling the UVM implementation twice.
    if "-ntb_opts" in options:
        index = options.index("-ntb_opts")
        if index + 1 < len(options) and options[index + 1].startswith("uvm"):
            homes = []
            if os.environ.get("VCS_UVM_HOME"):
                homes.append(Path(os.environ["VCS_UVM_HOME"]))
            if os.environ.get("VCS_HOME"):
                homes.append(Path(os.environ["VCS_HOME"]) / "etc" / options[index + 1])
            for home in homes:
                for path in (home, home / "src"):
                    if (path / "uvm_macros.svh").is_file():
                        includes.append(path)
    top = discover_top(config, entry, root, includes=includes)
    return {"verification.json": json.dumps({"top": top}, indent=2) + "\n"}


def command_values(config, entry, inputs):
    values = common_command_values(config, entry, inputs)
    top = values["verification_top"]
    values["verification_parameters"] = [
        f"-pvalue+{name if '.' in name else top + '.' + name}={parameter_value(value)}"
        for name, value in entry["settings"]["parameters"].items()
    ]
    return values


def run(config, entry, directory):
    # Keep runtime coverage outside the immutable compiled model. Each seed gets
    # its own database so concurrent or repeated tests cannot overwrite it.
    return run_common(
        config,
        entry,
        directory,
        case_arguments=["-cm_dir", "{case}/coverage.vdb", "-cm_name", "seed-{seed}"],
    )
