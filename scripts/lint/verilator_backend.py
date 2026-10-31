"""Verilator lint integration."""

from backends import parameter_value

BACKEND = {
    "tool": "verilator",
    "version_args": ["--version"],
    "format": "verilator",
    "summary": "log",
    "tracked_environment": ["VERILATOR_ROOT"],
    "commands": [
        [
            "verilator",
            "--lint-only",
            "--Mdir",
            "{work}/verilator",
            "{options}",
            "-f",
            "{inputs}/sources.f",
        ]
    ],
    "outputs": ["{reports}/summary.rpt"],
}


def configure(config, settings, root):
    if "commands" not in settings:
        command = list(BACKEND["commands"][0])
        if config["design"]["top"]:
            command[2:2] = ["--top-module", "{top}"]
        command[2:2] = [
            f"-G{name}={parameter_value(value)}"
            for name, value in config["design"]["parameters"].items()
        ]
        settings["commands"] = [command]
