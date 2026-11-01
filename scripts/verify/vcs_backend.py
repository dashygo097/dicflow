"""Independent SystemVerilog/UVM module verification using VCS.

Each completed test calls dicflow_verify_pkg::pass_test(check_count).
UVM projects supply native compile options and receive UVM_TESTNAME. Their top
sets finish_on_completion=0 and reports completion only after run_test returns,
so UVM check/report phases run before pass_test is called.
Type parameters are bound in the testbench; parameters overrides value parameters.
"""

from backends import parameter_value
from .common import RUNTIME_OPTIONS, configure as configure_common, collect, run

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
    "run_outputs": ["{reports}/summary.json", "{reports}/junit.xml"],
    "commands": [["vcs", "-full64", "-sverilog", "-top", "{testbench}",
                  "-Mdir={work}/simulator", "-o", "{work}/simulator/simv",
                  "-f", "{inputs}/sources.f", "{options}"]],
    "outputs": ["{work}/simulator", "{reports}/summary.rpt"],
}


def configure(config, settings, root):
    configure_common(config, settings, root)
    argv = list(BACKEND["commands"][0])
    argv.extend(
        f"-pvalue+{settings['testbench']}.{name}={parameter_value(value)}"
        for name, value in settings["parameters"].items()
    )
    settings["commands"] = [argv]
