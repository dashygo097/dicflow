"""Backend contract. Tool policy lives beside the tool.

BACKEND declares tool/version_args, Bender format/source, commands, outputs,
input globs, technology_inputs, tracked_environment and environment templates.
views maps stable editor filenames to declared output templates. The driver
publishes these separately from each build's cache and artifact checksums.
generated_sources declares native Bender groups relative to build/generated;
these configuration packages precede handwritten RTL in the projected manifest.
summary="log" opts into using the execution log as the summary report.
inherit_targets=True uses the prerequisite's source targets (e.g. equivalence).

Optional hooks:
  configure(config, settings, root): validate/normalize backend-owned settings.
  generate(config, settings, root): shared generated files, relative to build/generated.
  prepare(config, entry, root, directory, source): per-entry generated files.
  tools(config, entry): extra tool identities as {name: [command, version_args...]}.
  preflight(config, entry, root): backend-specific execution requirements.
  collect(config, entry, directory, snapshot): publish native tool artifacts.
  run(config, entry, directory): {commands: [...], inputs: [file paths...]}.

Generation hooks return {relative_path: text}. Shared outputs must agree across
entries. Commands are argv arrays; arbitrary options remain backend-owned.
"""

from pathlib import Path

import extensions
from project_config import ConfigError, string_list


def load_backend(root, stage, name, settings):
    backend = settings.get("backend", name)
    if not isinstance(backend, str) or not backend:
        raise ConfigError("backend must name a backend or a Python script")
    if backend.endswith(".py"):
        definition = (root / backend).resolve()
    elif backend.isidentifier():
        definition = (
            Path(__file__).resolve().parents[1] / stage / f"{backend}_backend.py"
        )
    else:
        raise ConfigError("backend must name a backend or a Python script")
    if not definition.is_file():
        raise ConfigError("Backend description does not exist: " + str(definition))
    description = getattr(extensions.load(definition), "BACKEND", None)
    if not isinstance(description, dict):
        raise ConfigError(f"{definition}: expected a BACKEND table")
    description = dict(description)
    description.setdefault("format", "")
    for key in ("tool",):
        if not isinstance(description.get(key), str) or not description[key]:
            raise ConfigError(f"{definition}: missing backend {key}")
    if not isinstance(description["format"], str):
        raise ConfigError(f"{definition}: format must name a Bender export format")
    description.setdefault("version_args", ["--version"])
    string_list(description["version_args"], "backend version_args")
    for key in ("inputs", "config_inputs", "technology_inputs", "tracked_environment"):
        string_list(description.get(key, []), f"backend {key}")
    for key in ("requires_top", "inherit_targets"):
        if key in description and not isinstance(description[key], bool):
            raise ConfigError(f"{definition}: {key} must be boolean")
    description.setdefault("source", "sources.f")
    views = description.get("views", {})
    if not isinstance(views, dict) or any(
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or name in {".", "..", ".outputs.json"}
        or not isinstance(pattern, str)
        or not pattern
        for name, pattern in views.items()
    ):
        raise ConfigError(f"{definition}: views must map filenames to output templates")
    source = description["source"]
    if not isinstance(source, str) or not source or Path(source).name != source:
        raise ConfigError(f"{definition}: source must be a filename")
    defaults = description.pop("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(f"{definition}: defaults must be a table")
    if any(key.startswith("_") for key in settings):
        raise ConfigError("Backend keys starting with '_' are reserved")
    resolved = {**defaults, **settings}
    prerequisite = resolved.get("requires")
    if prerequisite is not None and (
        not isinstance(prerequisite, str) or not prerequisite
    ):
        raise ConfigError("requires: expected a stage.backend reference")
    for key in ("options", "targets"):
        resolved.setdefault(key, [])
        string_list(resolved[key], key)
    if "inputs" in resolved:
        string_list(resolved["inputs"], "inputs")
    if "version" in resolved and not isinstance(resolved["version"], str):
        raise ConfigError("version: expected a string")
    for key in ("commands", "outputs"):
        value = resolved.get(key, description.get(key))
        if key == "outputs":
            if not string_list(value, "backend outputs"):
                raise ConfigError("backend outputs: expected at least one artifact")
        elif not isinstance(value, list) or not value:
            raise ConfigError("backend commands: expected non-empty argv arrays")
        else:
            for argv in value:
                if not string_list(argv, "backend command"):
                    raise ConfigError("backend command: argv must not be empty")
    script = resolved.get("script")
    if script:
        resolved["script"] = str((root / script).resolve())
    elif description.get("script"):
        resolved["script"] = str(definition.parent / description["script"])
    description["definition"] = str(definition)
    resolved["_backend"] = description
    return resolved


def tcl_value(value):
    if isinstance(value, dict):
        return (
            "[dict create "
            + " ".join(
                tcl_value(key) + " " + tcl_value(item) for key, item in value.items()
            )
            + "]"
        )
    if isinstance(value, list):
        return "[list " + " ".join(tcl_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    # Quote data, never Tcl expressions. Backend settings may contain braces,
    # bus selectors, dollars or backslashes.
    escaped = str(value)
    for character, replacement in (
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("$", "\\$"),
        ("[", "\\["),
        ("\n", "\\n"),
        ("\r", "\\r"),
    ):
        escaped = escaped.replace(character, replacement)
    return '"' + escaped + '"'


def configuration_tcl(config, entry):
    # Arbitrary backend/technology fields pass through without a Python schema.
    return (
        "set PROJECT "
        + tcl_value(
            {
                "design": {
                    **config["design"],
                    "parameters": {
                        name: parameter_value(value)
                        for name, value in config["design"]["parameters"].items()
                    },
                },
                "build": config["build"],
                "flow": {
                    key: value
                    for key, value in entry["settings"].items()
                    if key != "_backend"
                },
                "technology": config["technology"][entry["technology"]],
            }
        )
        + "\n"
    )


def parameter_value(value):
    return str(int(value)) if isinstance(value, bool) else str(value)


def context(config, entry, root, stage_dir, inputs, input_digest):
    return {
        "root": str(root),
        "work": str(stage_dir / "work"),
        "results": str(stage_dir / "results"),
        "reports": str(stage_dir / "reports"),
        "model": str(stage_dir / "model" / input_digest),
        "inputs": str(inputs),
        "generated": str(root / "build/generated"),
        "top": config["design"]["top"],
        "jobs": str(config["build"]["jobs"]),
        "script": entry["settings"].get("script", ""),
        "input_digest": input_digest,
    }


def commands(config, entry, root, stage_dir, inputs, input_digest):
    values = {
        **entry["settings"],
        **context(config, entry, root, stage_dir, inputs, input_digest),
    }
    definitions = entry["settings"].get(
        "commands", entry["settings"]["_backend"].get("commands")
    )
    result = []
    for argv in definitions:
        command = []
        for argument in argv:
            value = (
                values.get(argument[1:-1])
                if argument.startswith("{") and argument.endswith("}")
                else None
            )
            if isinstance(value, list):
                command.extend(string_list(value, argument))
            else:
                command.append(expand(argument, values))
        result.append(command)
    return result


def declared_outputs(config, entry, root, stage_dir, inputs, input_digest):
    values = context(config, entry, root, stage_dir, inputs, input_digest)
    patterns = entry["settings"].get(
        "outputs", entry["settings"]["_backend"].get("outputs")
    )
    paths = [Path(expand(pattern, values)) for pattern in patterns]
    for path in paths:
        if not path.resolve().is_relative_to(stage_dir.resolve()):
            raise ConfigError(
                f"Backend output must belong to its stage directory: {path}"
            )
    if len(set(paths)) != len(paths):
        raise ConfigError("Backend outputs must be distinct")
    return paths


def technology_inputs(technology, selectors):
    def leaves(value):
        if isinstance(value, dict):
            return [path for item in value.values() for path in leaves(item)]
        if isinstance(value, list):
            return [path for item in value for path in leaves(item)]
        return [value] if isinstance(value, str) else []

    result = []
    for selector in selectors:
        value = technology
        for key in selector.split("."):
            value = value.get(key, {}) if isinstance(value, dict) else {}
        result.extend(leaves(value))
    return result


def environment(config, entry, root, stage_dir, inputs, input_digest):
    values = context(config, entry, root, stage_dir, inputs, input_digest)
    return {
        name: expand(value, values)
        for name, value in entry["settings"]["_backend"].get("environment", {}).items()
    }


def expand(template, values):
    try:
        return template.format_map(values)
    except (KeyError, ValueError) as error:
        raise ConfigError(f"Invalid backend template {template!r}: {error}") from error


def invoke(settings, hook, *arguments):
    return extensions.invoke(settings["_backend"]["definition"], hook, *arguments)
