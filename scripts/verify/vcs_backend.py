"""Independent SystemVerilog/UVM module verification using VCS.

Each completed test calls df_verify_pkg::pass_test(check_count).
The testbench owns test selection. UVM tops set finish_on_completion=0
and report completion only after run_test returns,
so UVM check/report phases run before pass_test is called.
Type parameters are bound in the testbench. Value parameter overrides accept
root parameter names or explicit hierarchical names such as "tb_fifo.DEPTH".
"""

from backends import parameter_value

from .common import RUNTIME_OPTIONS, collect
from .common import configure as configure_common
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
            "{top}",
            "-Mdir={work}/simulator",
            "-o",
            "{work}/simulator/simv",
            "-file",
            "{inputs}/sources.f",
            "{_parameter_args}",
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
        task["_parameter_args"] = [
            f"-pvalue+{name if '.' in name else task['top'] + '.' + name}={parameter_value(value)}"
            for name, value in task["parameters"].items()
        ]


def run(config, entry, directory):
    # Keep runtime coverage outside the immutable compiled model. Each seed gets
    # its own database so concurrent or repeated tests cannot overwrite it.
    return run_common(
        config,
        entry,
        directory,
        case_arguments=["-cm_dir", "{case}/coverage.vdb", "-cm_name", "seed-{seed}"],
    )
