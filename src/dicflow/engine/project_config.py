"""Golden configuration schema and shared validation utilities.

HDL collection, target filtering and dependency resolution belong to Bender.
"""

import hashlib
import json
import math
import os
import re
import tomllib
from pathlib import Path

from ..filesystem import atomic_write, project_lock


class ConfigError(Exception):
    pass


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def reject_unknown(table, allowed, location):
    if not isinstance(table, dict):
        raise ConfigError(f"{location}: expected a table")
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ConfigError(f"{location}: unknown key(s): {', '.join(unknown)}")


def require_string(table, key, location):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{location}.{key}: expected a non-empty string")
    return value


def string_list(value, location):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{location}: expected an array of strings")
    return value


def require_table(raw, key):
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key}: expected a table")
    return value


def require_bool(table, key, location):
    value = table.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key}: expected a boolean")
    return value


def number(value, location, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{location}: expected a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ConfigError(f"{location}: expected a finite number")
    if positive and normalized <= 0:
        raise ConfigError(f"{location}: expected a positive number")
    if not positive and normalized < 0:
        raise ConfigError(f"{location}: expected a non-negative number")
    return normalized


def optional_number(table, key, location, *, positive=False):
    if key not in table:
        return None
    return number(table[key], f"{location}.{key}", positive=positive)


def unsigned_integer(value, location, *, positive=False, maximum=0xFFFFFFFF):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{location}: expected an integer")
    minimum = 1 if positive else 0
    if value < minimum or value > maximum:
        qualifier = "positive " if positive else ""
        raise ConfigError(
            f"{location}: expected a {qualifier}integer no greater than {maximum}"
        )
    return value


def configuration_path(project_root, value, location, suffix=None):
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{location}: expected a non-empty path string")
    if "$" in value:
        raise ConfigError(
            f"{location}: environment-variable expansion is not supported"
        )
    path = Path(value)
    if path.is_absolute():
        normalized = Path(os.path.normpath(value))
    else:
        normalized = (project_root / path).resolve()
    if suffix is not None and normalized.suffix.lower() != suffix:
        raise ConfigError(f"{location}: expected a {suffix} file: {value}")
    return str(normalized)


def select_flows(raw, root, action):
    from .backends import invoke, load_backend

    flow = raw.get("flow", {})
    if not isinstance(flow, dict):
        raise ConfigError("flow: expected named stages")
    if action in (None, "config"):
        requested = list(flow)
    elif action == "workflow":
        workflow = raw.get("workflow", {})
        reject_unknown(workflow, {"stages"}, "workflow")
        requested = string_list(workflow.get("stages", []), "workflow.stages")
    else:
        requested = [action.removesuffix("-build").removesuffix("-run")]
    selected = {}

    def visit(stage, name):
        if name in selected.get(stage, {}):
            return
        try:
            settings = flow[stage][name]
        except (KeyError, TypeError):
            raise ConfigError(f"Unknown flow: {stage}.{name}")
        if not isinstance(settings, dict):
            raise ConfigError(f"{stage}.{name}: expected a backend table")
        settings = load_backend(root, stage, name, settings)
        # Insert before traversing. Actual execution-node cycles are checked after
        # backend normalization, so independent cases can depend on one another.
        selected.setdefault(stage, {})[name] = settings
        references = invoke(settings, "prerequisites", settings)
        if references is None:
            references = [settings["requires"]] if settings.get("requires") else []
        for reference in string_list(references, "backend prerequisites"):
            parent_stage, parent_name, _ = flow_reference(reference)
            visit(parent_stage, parent_name)

    for stage in requested:
        if stage not in flow or not isinstance(flow[stage], dict) or not flow[stage]:
            raise ConfigError("No configured stage: " + str(stage))
        for name in flow[stage]:
            visit(stage, name)
    return selected


def generator_names(config, settings):
    available = {
        name
        for name, value in config.items()
        if isinstance(value, dict) and "_generator" in value
    }
    roots = settings.get("generators")
    if roots is None:
        roots = (
            list(available) if settings["_backend"].get("inherit_design", True) else []
        )
    roots = set(roots) | {
        selector.split(".")[0]
        for selector in settings["_backend"].get("config_inputs", [])
        if selector.split(".")[0] in available
    }
    selected = set()

    def visit(name):
        if name not in available:
            raise ConfigError("Missing generator: " + name)
        if name not in selected:
            selected.add(name)
            for dependency in config[name]["_generator"]["requires"]:
                visit(dependency)

    for name in roots:
        visit(name)
    return selected


def load_config(config_path, project_root, *, action=None):
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    if raw.get("schema_version") != 7:
        raise ConfigError(
            "schema_version: expected 7; move top to [design] and declare project generators"
        )
    if "framework" in raw:
        raise ConfigError(
            "Remove [framework]; declare dicflow as a dependency in Bender.yml"
        )
    core = {
        "schema_version",
        "design",
        "bender",
        "build",
        "environment",
        "technology",
        "flow",
        "workflow",
    }
    all_extensions = {key: value for key, value in raw.items() if key not in core}
    selected = select_flows(raw, project_root, action)
    owners = ["generator:" + name for name in all_extensions]
    owners += [
        "backend:" + stage + ":" + name
        for stage, profiles in raw.get("flow", {}).items()
        if isinstance(profiles, dict)
        for name in profiles
    ]
    needed = set()
    inherit_design = action in (None, "config")
    for profiles in selected.values():
        for settings in profiles.values():
            inherits = settings["_backend"].get("inherit_design", True)
            inherit_design |= inherits or settings["_backend"].get(
                "requires_top", False
            )
            needed.update(
                settings.get("generators", list(all_extensions) if inherits else [])
            )
            needed.update(
                selector.split(".")[0]
                for selector in settings["_backend"].get("config_inputs", [])
                if selector.split(".")[0] in all_extensions
            )
    extensions = {
        name: value for name, value in all_extensions.items() if name in needed
    }
    missing = needed - set(extensions)
    if missing:
        raise ConfigError("Missing generator: " + ", ".join(sorted(missing)))
    if not inherit_design:
        raw["design"] = {}
    raw["flow"] = selected
    if action not in (None, "config", "workflow"):
        raw["workflow"] = {}
    for name, settings in extensions.items():
        if not IDENTIFIER.fullmatch(name) or not isinstance(settings, dict):
            raise ConfigError(f"{name}: expected a named configuration table")
    design = raw.get("design", {})
    reject_unknown(design, {"top", "parameters", "sources"}, "design")
    top = design.get("top", "")
    if not isinstance(top, str) or (top and not IDENTIFIER.fullmatch(top)):
        raise ConfigError("design.top: invalid module identifier")
    parameters = design.get("parameters", {})
    if not isinstance(parameters, dict) or any(
        not IDENTIFIER.fullmatch(name)
        or not isinstance(value, (str, int, bool, float))
        or (isinstance(value, float) and not math.isfinite(value))
        for name, value in parameters.items()
    ):
        raise ConfigError("design.parameters: expected named scalar values")
    sources = design.get("sources", [])
    if not isinstance(sources, list):
        raise ConfigError("design.sources: expected Bender source groups")
    design = {"top": top, "parameters": parameters, "sources": sources}
    if parameters and not top:
        raise ConfigError("design.parameters requires design.top")
    bender = raw.get("bender", {})
    if not isinstance(bender, dict):
        raise ConfigError("bender: expected a table")
    reject_unknown(bender, {"version"}, "bender")
    if "version" in bender:
        require_string(bender, "version", "bender")
    if not (project_root / "Bender.yml").is_file():
        raise ConfigError("Dependencies must be declared in the repository Bender.yml")
    build = raw.get("build", {})
    reject_unknown(build, {"jobs", "technology", "reproducible"}, "build")
    build = {
        "jobs": unsigned_integer(build.get("jobs", 1), "build.jobs", positive=True),
        "technology": build.get("technology", "generic"),
        "reproducible": require_bool(build, "reproducible", "build")
        if "reproducible" in build
        else False,
    }
    if build["reproducible"] and not bender.get("version"):
        raise ConfigError("Reproducible builds require bender.version")
    environment = raw.get("environment", {})
    reject_unknown(
        environment,
        {"required_commands", "required_variables", "tool_versions", "packages"},
        "environment",
    )
    environment = {
        **{
            key: string_list(environment.get(key, []), f"environment.{key}")
            for key in ("required_commands", "required_variables")
        },
        "tool_versions": environment.get("tool_versions", {}),
        "packages": environment.get("packages", {}),
    }
    for key in ("tool_versions", "packages"):
        if not isinstance(environment[key], dict) or any(
            not isinstance(value, str) or not value
            for value in environment[key].values()
        ):
            raise ConfigError(f"environment.{key}: expected version strings")
    technologies = raw.get("technology", {"generic": {}})
    if not isinstance(technologies, dict):
        raise ConfigError("technology: expected named tables")
    for name, tech in technologies.items():
        if not IDENTIFIER.fullmatch(name) or not isinstance(tech, dict):
            raise ConfigError("technology: expected named tables")
        tech.setdefault("targets", [])
        string_list(tech["targets"], f"technology.{name}.targets")
        if not isinstance(tech.get("assets", {}), dict):
            raise ConfigError(f"technology.{name}.assets: expected a table")
        for asset, desc in tech.get("assets", {}).items():
            if not isinstance(desc, dict):
                raise ConfigError(
                    "technology assets require {path, sha256} descriptors"
                )
            reject_unknown(desc, {"path", "sha256"}, f"technology asset {asset}")
            desc["path"] = configuration_path(project_root, desc.get("path"), asset)
            if "sha256" in desc and (
                not isinstance(desc["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", desc["sha256"])
            ):
                raise ConfigError(f"Invalid asset SHA256: {asset}")
    if (
        not isinstance(build["technology"], str)
        or build["technology"] not in technologies
    ):
        raise ConfigError("build.technology: unknown technology")
    from .backends import invoke

    flow = raw.get("flow", {})
    if not isinstance(flow, dict):
        raise ConfigError("flow: expected named stages")
    normalized_flow = {}
    reserved = {
        "env",
        "init",
        "config",
        "deps-update",
        "workflow",
        "clean",
        "distclean",
        "help",
        "status",
        "artifacts",
        "logs",
        "generated",
        "execute",
        "execute-run",
        "sim-run",
        "sim-build",
    }
    for stage, tools in flow.items():
        if (
            not IDENTIFIER.fullmatch(stage)
            or stage in reserved
            or not isinstance(tools, dict)
        ):
            raise ConfigError(f"flow.{stage}: invalid stage or backend tables")
        normalized_flow[stage] = {}
        for name, settings in tools.items():
            if not IDENTIFIER.fullmatch(name) or not isinstance(settings, dict):
                raise ConfigError(f"flow.{stage}: expected named backend tables")
            settings = selected[stage][name]
            settings["technologies"] = string_list(
                settings.get("technologies", [build["technology"]]), "technologies"
            )
            if not settings["technologies"] or len(
                set(settings["technologies"])
            ) != len(settings["technologies"]):
                raise ConfigError("technologies: expected distinct technology names")
            for technology in settings["technologies"]:
                if technology not in technologies:
                    raise ConfigError(f"Unknown technology: {technology}")
            normalized_flow[stage][name] = settings
    workflow = raw.get("workflow", {})
    reject_unknown(workflow, {"stages"}, "workflow")
    stages = string_list(workflow.get("stages", []), "workflow.stages")
    if len(stages) != len(set(stages)):
        raise ConfigError("workflow.stages: duplicate stages")
    for stage in stages:
        if stage not in normalized_flow or not normalized_flow[stage]:
            raise ConfigError(f"Workflow stage has no backend: {stage}")
    config = {
        "schema_version": 7,
        "design": design,
        "bender": bender,
        "build": build,
        "environment": environment,
        "technology": technologies,
        "flow": normalized_flow,
        "workflow": {"stages": stages},
    }
    from . import generators

    def load_generator(name):
        if name in config:
            return
        if name not in all_extensions or not isinstance(all_extensions[name], dict):
            raise ConfigError("Missing generator table: " + name)
        config[name] = generators.load(name, all_extensions[name], project_root)
        for dependency in config[name]["_generator"]["requires"]:
            load_generator(dependency)

    for name in extensions:
        load_generator(name)
    config["_generation_owners"] = owners
    config["_complete"] = action in (None, "config")
    for tools in normalized_flow.values():
        for settings in tools.values():
            invoke(settings, "configure", config, settings, project_root)
    return config


def flow_reference(reference):
    if not isinstance(reference, str):
        raise ConfigError("requires: expected stage.backend[.variant]")
    parts = reference.split(".")
    if (
        len(parts) not in (2, 3)
        or not all(parts)
        or not all(IDENTIFIER.fullmatch(part) for part in parts[:2])
        or (len(parts) == 3 and not re.fullmatch(r"[A-Za-z0-9_-]+", parts[2]))
    ):
        raise ConfigError("requires: expected stage.backend[.variant]")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def config_digest(config):
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def header(comment, digest):
    return (
        f"{comment} Generated by project_config.py; do not edit.\n"
        f"{comment} Configuration digest: {digest}\n"
    )
