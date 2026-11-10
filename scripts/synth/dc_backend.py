"""Design Compiler integration. Synthesis policy is implemented in dc.tcl."""

from backends import configuration_tcl, technology_inputs

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
    "outputs": {
        "summary": "{reports}/summary.rpt",
        "netlist": "{results}/{top}.v",
        "ddc": "{results}/{top}.ddc",
        "constraints": "{results}/{top}.sdc",
        "guidance": "{results}/{top}.svf",
        "synthesis_contract": "{results}/synthesis.tcl",
    },
    "defaults": {
        "compile": "ultra",
        "preserve_hierarchy": True,
        "retime": False,
        "clock_gating": False,
    },
}


def prepare(config, entry, root, directory, source):
    technology = config["technology"][entry["technology"]]
    paths = set(technology_inputs(technology, BACKEND["technology_inputs"]))
    selected = {
        "dc": technology.get("dc", {}),
        "libraries": {
            alias: path
            for alias, path in technology.get("libraries", {}).items()
            if path in paths
        },
    }
    scoped = {**config, "technology": {entry["technology"]: selected}}
    return {"backend.tcl": configuration_tcl(scoped, entry)}
