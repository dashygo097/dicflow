"""Formality RTL-to-gate integration."""

from dicflow.engine.backends import configuration_tcl
from dicflow.engine.project_config import ConfigError

BACKEND = {
    "tool": "fm_shell",
    "version_args": ["-version"],
    "format": "formality",
    "requires_top": True,
    "source": "sources.tcl",
    "inherit_targets": True,
    "script": "formality.tcl",
    "environment": {"STAGE_CONFIG": "{inputs}/backend.tcl"},
    "commands": [["fm_shell", "{options}", "-f", "{script}"]],
    "outputs": ["{reports}/summary.rpt"],
}


def prepare(config, entry, root, directory, source):
    if not entry["settings"].get("requires"):
        raise ConfigError("Formality requires a synthesis provider in requires")
    return {"backend.tcl": configuration_tcl(config, entry)}


def preflight(config, entry, root):
    if config["design"]["parameters"]:
        raise ConfigError(
            "Formality requires a fixed-parameter RTL wrapper for parameterized designs"
        )
