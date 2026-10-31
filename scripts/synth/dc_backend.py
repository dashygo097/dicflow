"""Design Compiler integration. Synthesis policy is implemented in dc.tcl."""

from backends import configuration_tcl

BACKEND = {
    "tool": "dc_shell",
    "version_args": ["-version"],
    "format": "synopsys",
    "requires_top": True,
    "config_inputs": ["constraints"],
    "source": "sources.tcl",
    "script": "dc.tcl",
    "inputs": ["build/generated/constraints.sdc"],
    "environment": {
        "STAGE_CONFIG": "{inputs}/backend.tcl",
        "CONSTRAINTS_SDC": "{generated}/constraints.sdc",
    },
    "technology_inputs": [
        "dc.target_libraries",
        "dc.link_libraries",
        "dc.synthetic_libraries",
        "dc.library_pairs",
        "dc.setup_scripts",
    ],
    "commands": [["dc_shell", "{options}", "-f", "{script}"]],
    "outputs": [
        "{reports}/summary.rpt",
        "{results}/{top}.v",
        "{results}/{top}.ddc",
        "{results}/{top}.sdc",
        "{results}/{top}.svf",
        "{results}/synthesis.tcl",
    ],
    "defaults": {
        "compile": "ultra",
        "preserve_hierarchy": True,
        "retime": False,
        "clock_gating": False,
    },
}


def prepare(config, entry, root, directory, source):
    return {"backend.tcl": configuration_tcl(config, entry)}
