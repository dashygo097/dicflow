"""Shared SDC configuration generator, independent of design and EDA backend."""

from project_config import (
    IDENTIFIER,
    ConfigError,
    config_digest,
    header,
    number,
    optional_number,
    reject_unknown,
    require_string,
    string_list,
)

GENERATOR = {}


def normalize_clock(clock, location, *, virtual):
    if not isinstance(clock, dict):
        raise ConfigError(f"{location}: expected a table")
    reject_unknown(
        clock,
        {
            "name",
            "period",
            "waveform",
            "setup_uncertainty",
            "hold_uncertainty",
            "transition",
        },
        location,
    )
    name = require_string(clock, "name", location)
    if not IDENTIFIER.match(name):
        raise ConfigError(f"{location}.name: expected an identifier")
    period = number(clock.get("period"), f"{location}.period", positive=True)
    waveform = clock.get("waveform", [0.0, period / 2.0])
    if not isinstance(waveform, list) or len(waveform) != 2:
        raise ConfigError(f"{location}.waveform: expected an array of two numbers")
    waveform = [
        number(value, f"{location}.waveform[{index}]")
        for index, value in enumerate(waveform)
    ]
    if not 0 <= waveform[0] < waveform[1] <= period:
        raise ConfigError(f"{location}.waveform: expected 0 <= rise < fall <= period")
    normalized = {
        "name": name,
        "period": period,
        "waveform": waveform,
        "virtual": virtual,
    }
    for key in ("setup_uncertainty", "hold_uncertainty", "transition"):
        value = optional_number(clock, key, location)
        if value is not None:
            normalized[key] = value
    return normalized


def normalize_port_constraints(entries, location, clock_names, value_keys):
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ConfigError(f"{location}: expected an array of tables")
    normalized = []
    for index, entry in enumerate(entries):
        entry_location = f"{location}[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{entry_location}: expected a table")
        allowed = {"ports"} | set(value_keys)
        reject_unknown(entry, allowed, entry_location)
        ports = string_list(entry.get("ports"), f"{entry_location}.ports")
        if not ports or any(not port for port in ports):
            raise ConfigError(f"{entry_location}.ports: expected non-empty patterns")
        item = {"ports": ports}
        if "clock" in value_keys:
            clock = require_string(entry, "clock", entry_location)
            if clock not in clock_names:
                raise ConfigError(f"{entry_location}.clock: undefined clock: {clock}")
            item["clock"] = clock
        value_count = 0
        for key in value_keys:
            if key == "clock":
                continue
            value = optional_number(entry, key, entry_location)
            if value is not None:
                item[key] = value
                value_count += 1
        if value_count == 0:
            names = ", ".join(key for key in value_keys if key != "clock")
            raise ConfigError(f"{entry_location}: expected at least one of: {names}")
        normalized.append(item)
    return normalized


def normalize_constraints(raw):
    constraints = raw.get("constraints", {})
    if not isinstance(constraints, dict):
        raise ConfigError("constraints: expected a table")
    reject_unknown(
        constraints,
        {
            "clocks",
            "virtual_clocks",
            "input_delays",
            "output_delays",
            "input_transitions",
            "output_loads",
            "design_rules",
        },
        "constraints",
    )

    clocks = []
    names = set()
    for key, virtual in (("clocks", False), ("virtual_clocks", True)):
        entries = constraints.get(key, [])
        if not isinstance(entries, list):
            raise ConfigError(f"constraints.{key}: expected an array of tables")
        for index, entry in enumerate(entries):
            location = f"constraints.{key}[{index}]"
            clock = normalize_clock(entry, location, virtual=virtual)
            if clock["name"] in names:
                raise ConfigError(f"{location}.name: duplicate clock: {clock['name']}")
            names.add(clock["name"])
            clocks.append(clock)

    design_rules = constraints.get("design_rules", {})
    if not isinstance(design_rules, dict):
        raise ConfigError("constraints.design_rules: expected a table")
    reject_unknown(
        design_rules,
        {"max_transition", "max_fanout", "max_capacitance"},
        "constraints.design_rules",
    )
    normalized_rules = {}
    for key in sorted(design_rules):
        normalized_rules[key] = number(
            design_rules[key], f"constraints.design_rules.{key}", positive=True
        )

    return {
        "clocks": clocks,
        "input_delays": normalize_port_constraints(
            constraints.get("input_delays"),
            "constraints.input_delays",
            names,
            ("clock", "max", "min"),
        ),
        "output_delays": normalize_port_constraints(
            constraints.get("output_delays"),
            "constraints.output_delays",
            names,
            ("clock", "max", "min"),
        ),
        "input_transitions": normalize_port_constraints(
            constraints.get("input_transitions"),
            "constraints.input_transitions",
            names,
            ("rise", "fall"),
        ),
        "output_loads": normalize_port_constraints(
            constraints.get("output_loads"),
            "constraints.output_loads",
            names,
            ("capacitance",),
        ),
        "design_rules": normalized_rules,
    }


def tcl_braced(value):
    value = str(value)
    if "{" in value or "}" in value or "\n" in value or "\r" in value:
        raise ConfigError(
            f"value cannot be represented safely in generated Tcl: {value!r}"
        )
    return "{" + value + "}"


def tcl_list(values):
    return "[list " + " ".join(tcl_braced(value) for value in values) + "]"


def format_number(value):
    return format(value, ".15g")


def render_constraints_sdc(config, digest):
    constraints = config["constraints"]
    lines = [header("#", digest).rstrip()]
    lines.extend(
        [
            "proc project_require_ports {patterns context} {",
            "  set objects [get_ports -quiet $patterns]",
            "  if {[sizeof_collection $objects] == 0} {",
            '    error "constraint selector resolved to no ports: $context"',
            "  }",
            "  return $objects",
            "}",
        ]
    )

    for clock in constraints["clocks"]:
        name = clock["name"]
        period = format_number(clock["period"])
        waveform = " ".join(format_number(value) for value in clock["waveform"])
        if clock["virtual"]:
            lines.append(
                f"create_clock -name {tcl_braced(name)} -period {period} "
                f"-waveform {{{waveform}}}"
            )
        else:
            lines.extend(
                [
                    f"set project_clock_port [get_ports -quiet {tcl_list([name])}]",
                    "if {[sizeof_collection $project_clock_port] != 1} {",
                    f"  error {tcl_braced('physical clock must resolve to exactly one port: ' + name)}",
                    "}",
                    f"create_clock -name {tcl_braced(name)} -period {period} "
                    f"-waveform {{{waveform}}} $project_clock_port",
                    "unset project_clock_port",
                ]
            )
        clock_collection = f"[get_clocks {tcl_list([name])}]"
        if "setup_uncertainty" in clock:
            lines.append(
                "set_clock_uncertainty -setup "
                f"{format_number(clock['setup_uncertainty'])} {clock_collection}"
            )
        if "hold_uncertainty" in clock:
            lines.append(
                "set_clock_uncertainty -hold "
                f"{format_number(clock['hold_uncertainty'])} {clock_collection}"
            )
        if "transition" in clock:
            lines.append(
                f"set_clock_transition {format_number(clock['transition'])} "
                f"{clock_collection}"
            )

    for kind in ("input", "output"):
        for entry in constraints[f"{kind}_delays"]:
            ports = tcl_list(entry["ports"])
            context = f"{kind}_delays: {', '.join(entry['ports'])}"
            lines.append(
                f"set project_ports [project_require_ports {ports} {tcl_braced(context)}]"
            )
            for bound in ("max", "min"):
                if bound in entry:
                    lines.append(
                        f"set_{kind}_delay -{bound} {format_number(entry[bound])} "
                        f"-clock [get_clocks {tcl_list([entry['clock']])}] $project_ports"
                    )
            lines.append("unset project_ports")

    for entry in constraints["input_transitions"]:
        ports = tcl_list(entry["ports"])
        context = f"input_transitions: {', '.join(entry['ports'])}"
        lines.append(
            f"set project_ports [project_require_ports {ports} {tcl_braced(context)}]"
        )
        for edge in ("rise", "fall"):
            if edge in entry:
                lines.append(
                    f"set_input_transition -{edge} {format_number(entry[edge])} $project_ports"
                )
        lines.append("unset project_ports")

    for entry in constraints["output_loads"]:
        ports = tcl_list(entry["ports"])
        context = f"output_loads: {', '.join(entry['ports'])}"
        lines.extend(
            [
                f"set project_ports [project_require_ports {ports} {tcl_braced(context)}]",
                f"set_load {format_number(entry['capacitance'])} $project_ports",
                "unset project_ports",
            ]
        )

    rule_commands = {
        "max_transition": "set_max_transition",
        "max_fanout": "set_max_fanout",
        "max_capacitance": "set_max_capacitance",
    }
    for name, command in rule_commands.items():
        if name in constraints["design_rules"]:
            lines.append(
                f"{command} {format_number(constraints['design_rules'][name])} "
                "[current_design]"
            )
    lines.append("rename project_require_ports {}")
    return "\n".join(lines) + "\n"


def configure(settings, root):
    values = {key: value for key, value in settings.items() if key != "_generator"}
    settings.update(normalize_constraints({"constraints": values}))


def generate(config, settings, root):
    return {
        "constraints.sdc": render_constraints_sdc(
            {"constraints": settings}, config_digest(settings)
        )
    }
