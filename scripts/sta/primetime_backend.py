"""PrimeTime views over checksummed upstream artifacts; no RTL or file discovery.

Each phase declares its provider. Each technology view names library catalog
entries, checks and an RC view. MODE settings remain design-owned. Independent
processes keep libraries, modes and RC data isolated between timing views.
"""

import math
import os
from copy import deepcopy

from backends import tcl_value
from project_config import (
    IDENTIFIER,
    ConfigError,
    configuration_path,
    flow_reference,
    reject_unknown,
    string_list,
    unsigned_integer,
)

BACKEND = {
    "tool": "pt_shell",
    "version_args": ["-version"],
    "requires_top": True,
    "inherit_design": False,
    "script": "primetime.tcl",
    "environment": {"STAGE_CONFIG": "{inputs}/backend.tcl"},
    "commands": [["pt_shell", "{options}", "-f", "{script}"]],
    "outputs": {
        "summary": "{reports}/summary.rpt",
        "metrics": "{reports}/summary.json",
        "constraints_report": "{reports}/constraints.rpt",
        "coverage_report": "{reports}/coverage.rpt",
        "check_timing_report": "{reports}/check_timing.rpt",
        "clocks_report": "{reports}/clocks.rpt",
        "units_report": "{reports}/units.rpt",
    },
}

PHASES = {"prelayout", "postlayout"}
OPTIONS = {
    "views",
    "mode",
    "options",
    "clock_model",
    "interconnect_model",
    "report_paths",
    "metrics_limit",
    "fail_on_violations",
    "allowed_unannotated_nets",
}


def phases(settings):
    configured = PHASES & settings.keys()
    selected = os.environ.get("CASE")
    if selected:
        if selected not in configured:
            raise ConfigError(f"CASE={selected}: expected a configured STA phase")
        return [selected]
    return sorted(configured)


def prerequisites(settings):
    result = []
    for phase in phases(settings):
        case = settings[phase]
        if not isinstance(case, dict) or not case.get("requires"):
            raise ConfigError(f"flow.sta.primetime.{phase}.requires is required")
        flow_reference(case["requires"])
        result.append(case["requires"])
    return result


def identifiers(value, location):
    values = string_list(value, location)
    if (
        not values
        or len(values) != len(set(values))
        or any(not IDENTIFIER.fullmatch(item) for item in values)
    ):
        raise ConfigError(f"{location}: expected distinct names")
    return values


def configured_modes(settings):
    modes = settings.get("modes", {})
    if not isinstance(modes, dict):
        raise ConfigError("flow.sta.primetime.modes: expected named tables")
    for name, mode in modes.items():
        if not IDENTIFIER.fullmatch(name):
            raise ConfigError(f"Invalid STA mode: {name}")
        reject_unknown(mode, {"case_analysis", "setup_scripts"}, f"STA mode {name}")
        cases = mode.get("case_analysis", {})
        if not isinstance(cases, dict) or any(
            not name
            or not (
                type(value) is int
                and value in (0, 1)
                or isinstance(value, str)
                and value in ("rising", "falling")
            )
            for name, value in cases.items()
        ):
            raise ConfigError(
                f"STA mode {name}.case_analysis: expected 0/1/rising/falling values"
            )
        string_list(mode.get("setup_scripts", []), f"STA mode {name}.setup_scripts")
    return modes


def phase_settings(settings, phase, modes):
    common = {key: value for key, value in settings.items() if key in OPTIONS}
    case = settings[phase]
    reject_unknown(case, OPTIONS | {"requires"}, f"STA {phase}")
    values = {**common, **case}
    flow_reference(values["requires"])
    names = identifiers(values.get("views", []), f"STA {phase}.views")
    mode_name = values.get("mode", "functional")
    if not isinstance(mode_name, str) or mode_name not in modes:
        raise ConfigError(f"Unknown STA mode: {mode_name}")
    clock_model = values.setdefault(
        "clock_model", "ideal" if phase == "prelayout" else "propagated"
    )
    interconnect = values.setdefault(
        "interconnect_model", "wire_load" if phase == "prelayout" else "extracted"
    )
    if clock_model not in {"ideal", "propagated"}:
        raise ConfigError("clock_model: expected ideal or propagated")
    if interconnect not in {"wire_load", "extracted"}:
        raise ConfigError("interconnect_model: expected wire_load or extracted")
    if phase == "postlayout" and (
        clock_model != "propagated" or interconnect != "extracted"
    ):
        raise ConfigError(
            "postlayout requires propagated clocks and extracted parasitics"
        )
    if phase == "prelayout" and (clock_model != "ideal" or interconnect != "wire_load"):
        raise ConfigError("prelayout requires ideal clocks and a wire-load model")
    for key, default in (("report_paths", 50), ("metrics_limit", 1000000)):
        values[key] = unsigned_integer(
            values.get(key, default), key, positive=True, maximum=2000000
        )
    if not isinstance(values.setdefault("fail_on_violations", True), bool):
        raise ConfigError("fail_on_violations: expected boolean")
    string_list(
        values.setdefault("allowed_unannotated_nets", []),
        "allowed_unannotated_nets",
    )
    string_list(values.setdefault("options", []), "STA options")
    return values, names, mode_name


def configure(config, settings, root):
    reject_unknown(
        settings,
        OPTIONS
        | PHASES
        | {
            "backend",
            "technologies",
            "targets",
            "version",
            "generators",
            "script",
            "modes",
            "_backend",
        },
        "flow.sta.primetime",
    )
    if not config["design"]["top"]:
        raise ConfigError("STA requires design.top")
    if settings.get("targets") or settings.get("generators"):
        raise ConfigError(
            "STA consumes upstream artifacts; targets/generators belong to the producer"
        )
    if not phases(settings):
        raise ConfigError("STA requires a prelayout or postlayout table")
    modes = configured_modes(settings)
    tasks = {}
    for phase in phases(settings):
        values, names, mode_name = phase_settings(settings, phase, modes)
        interconnect = values["interconnect_model"]
        for technology in settings["technologies"]:
            tech = config["technology"][technology]
            catalog = tech.get("libraries", {})
            if not isinstance(catalog, dict) or any(
                not IDENTIFIER.fullmatch(alias) or not isinstance(path, str) or not path
                for alias, path in catalog.items()
            ):
                raise ConfigError(
                    f"technology.{technology}.libraries: expected named file paths"
                )
            native = tech.get("primetime", {})
            reject_unknown(
                native,
                {"wire_load_model", "wire_load_mode", "app_vars", "setup_scripts"},
                "technology primetime",
            )
            timing = tech.get("timing", {})
            reject_unknown(timing, {"views"}, "technology timing")
            view_tables = timing.get("views", {})
            if not isinstance(view_tables, dict):
                raise ConfigError("technology.timing.views: expected named tables")
            for name in names:
                view = view_tables.get(name)
                reject_unknown(
                    view,
                    {"libraries", "checks", "rc", "setup_scripts"},
                    f"timing view {name}",
                )
                libraries = identifiers(
                    view.get("libraries", []), f"timing view {name}.libraries"
                )
                if set(libraries) - catalog.keys():
                    raise ConfigError(
                        f"timing view {name}: unknown library aliases: {sorted(set(libraries) - catalog.keys())}"
                    )
                checks = string_list(
                    view.get("checks", ["setup", "hold"]), f"timing view {name}.checks"
                )
                if (
                    not checks
                    or len(set(checks)) != len(checks)
                    or set(checks) - {"setup", "hold"}
                ):
                    raise ConfigError(
                        f"timing view {name}: checks must select setup/hold"
                    )
                rc = view.get("rc", "")
                if (
                    not isinstance(rc, str)
                    or (rc and not IDENTIFIER.fullmatch(rc))
                    or (interconnect == "extracted" and not rc)
                ):
                    raise ConfigError(
                        f"timing view {name}: extracted analysis requires a named rc view"
                    )
                model = native.get("wire_load_model", "")
                if interconnect == "wire_load" and (
                    not isinstance(model, str) or not model
                ):
                    raise ConfigError(
                        "prelayout requires technology.primetime.wire_load_model"
                    )
                wire_mode = native.get("wire_load_mode", "top")
                if wire_mode not in {"top", "enclosed", "segmented"}:
                    raise ConfigError(
                        "wire_load_mode: expected top, enclosed or segmented"
                    )
                app_vars = native.get("app_vars", {})
                if not isinstance(app_vars, dict) or any(
                    not IDENTIFIER.fullmatch(key)
                    or not isinstance(value, (str, bool, int, float))
                    or isinstance(value, float)
                    and not math.isfinite(value)
                    for key, value in app_vars.items()
                ):
                    raise ConfigError(
                        "primetime.app_vars: expected named scalar values"
                    )
                scripts = []
                for location, table in (
                    ("primetime", native),
                    (f"view {name}", view),
                    (f"mode {mode_name}", modes[mode_name]),
                ):
                    scripts += string_list(
                        table.get("setup_scripts", []), f"{location}.setup_scripts"
                    )
                scripts = [
                    configuration_path(root, path, "STA setup_scripts")
                    for path in scripts
                ]
                task = {
                    **values,
                    "phase": phase,
                    "view": name,
                    "top": config["design"]["top"],
                    "mode": mode_name,
                    "checks": checks,
                    "rc": rc,
                    "case_analysis": modes[mode_name].get("case_analysis", {}),
                    "libraries": [
                        configuration_path(root, catalog[alias], f"library {alias}")
                        for alias in libraries
                    ],
                    "setup_scripts": scripts,
                    "app_vars": app_vars,
                    "script": settings["script"],
                    "wire_load_model": model,
                    "wire_load_mode": wire_mode,
                    "technologies": [technology],
                    "targets": [],
                    **(
                        {"version": settings["version"]}
                        if "version" in settings
                        else {}
                    ),
                    "_backend": deepcopy(settings["_backend"]),
                }
                task.pop("views", None)
                task["_backend"]["input_roles"] = {
                    "netlist": "netlist",
                    "constraints": "constraints",
                }
                if interconnect == "extracted":
                    task["_backend"]["input_roles"]["parasitics"] = f"parasitics.{rc}"
                    task["_backend"]["outputs"]["parasitics_report"] = (
                        "{reports}/parasitics.rpt"
                    )
                for check in checks:
                    task["_backend"]["outputs"][f"{check}_report"] = (
                        "{reports}/timing." + check + ".rpt"
                    )
                # Native inputs are already selected and normalized for this view.
                task["inputs"] = [
                    os.path.relpath(path, root) for path in task["libraries"] + scripts
                ]
                tasks[f"{phase}-{name}-{technology}"] = task
    settings["_tasks"] = tasks


def variants(config, settings):
    # One JSON-safe execution per view and technology.
    return settings["_tasks"]


def prepare(config, entry, root, directory, source):
    values = {
        key: value
        for key, value in entry["settings"].items()
        if key not in {"_backend", "inputs"}
    }
    return {"backend.tcl": "set STA " + tcl_value(values) + "\n"}
