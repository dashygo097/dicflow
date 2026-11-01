"""Independent SystemVerilog/UVM module verification using VCS.

Each completed test calls dicflow_verify_pkg::pass_test(check_count).
The testbench owns test selection. UVM tops set finish_on_completion=0
and report completion only after run_test returns,
so UVM check/report phases run before pass_test is called.
Type parameters are bound in the testbench; parameters overrides value parameters.
"""

from backends import parameter_value

from .common import RUNTIME_OPTIONS, collect
from .common import configure as configure_common
from .common import run

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
    "tracked_environment": ["VCS_HOME", "VCS_TARGET_ARCH"],
    "run_outputs": ["{reports}/summary.json", "{reports}/junit.xml"],
    "commands": [
        ["mkdir", "-p", "{work}/simulator"],
        [
            "vcs",
            "-sverilog",
            "-top",
            "{testbench}",
            "-Mdir={work}/simulator",
            "-o",
            "{work}/simulator/simv",
            "-file",
            "{inputs}/sources.f",
            "{options}",
        ],
    ],
    "outputs": ["{work}/simulator", "{reports}/summary.rpt"],
}


def configure(config, settings, root):
    configure_common(config, settings, root)
    # Use the explicitly requested mode for probing too; otherwise defer to VCS.
    mode_options = [option for option in settings["options"] if option == "-full64"]
    settings["_backend"]["version_args"] = [*mode_options, "-ID"]
    argv = list(BACKEND["commands"][-1])
    argv.extend(
        f"-pvalue+{settings['testbench']}.{name}={parameter_value(value)}"
        for name, value in settings["parameters"].items()
    )
    settings["commands"] = [*BACKEND["commands"][:-1], argv]
