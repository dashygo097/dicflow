"""Single project driver, called by Make. Bender owns HDL/package resolution."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import bender
import extensions
import generators
from backends import commands, context, declared_outputs
from backends import environment as backend_environment
from backends import invoke, technology_inputs
from project_config import (ConfigError, atomic_write, config_digest,
                            generator_names, load_config, project_lock)

ROOT = Path(os.environ.get("PROJECT_ROOT", os.getcwd())).resolve()
GENERATED = ROOT / "build/generated"


def sha256(path):
    if path.is_dir():
        return config_digest({
            str(file.relative_to(path)): sha256(file)
            for file in sorted(path.rglob("*")) if file.is_file()
        })
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def make_path(path, *, target=False):
    value = str(path)
    if "\n" in value or "\r" in value:
        raise ConfigError("Make paths must not contain newlines")
    for character, escaped in (
        ("\\", "\\\\"),
        ("$", "$$"),
        ("#", "\\#"),
        (" ", "\\ "),
        ("\t", "\\\t"),
        (":", "\\:"),
    ):
        value = value.replace(character, escaped)
    return value.replace("%", "\\%") if target else value


def make_command(argv):
    return shlex.join(argv).replace("$", "$$")


def check_framework(config):
    if os.environ.get("PROJECT_BOOTSTRAP_API") != "2":
        raise ConfigError("Use the bootstrap API 2 Make entry")
    expected = str(Path(__file__).resolve().parents[2])
    if os.environ.get("PROJECT_FRAMEWORK_ROOT") != expected:
        raise ConfigError("Framework checkout changed after bootstrap")


def synchronize_outputs(directory, contents):
    # Remove only files previously owned by this publisher, never arbitrary files.
    inventory = directory / ".outputs.json"
    previous = json.loads(inventory.read_text()) if inventory.exists() else []
    if not isinstance(previous, list) or any(
        not isinstance(name, str) for name in previous
    ):
        raise ConfigError(f"Invalid generated output inventory: {inventory}")
    if not contents and not previous:
        return
    for name in set(previous) | set(contents):
        path = directory / name
        if (
            name == inventory.name
            or path.resolve() == directory.resolve()
            or not path.resolve().is_relative_to(directory.resolve())
        ):
            raise ConfigError(f"Generated output resolves outside its owner: {name}")
    for name, content in contents.items():
        atomic_write(directory / name, content)
    for name in set(previous) - set(contents):
        (directory / name).unlink(missing_ok=True)
    write_json(inventory, sorted(contents))


def tool_identity(command, arguments):
    executable = shutil.which(command)
    if executable is None:
        return {"command": command, "available": False}
    try:
        result = subprocess.run(
            [executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "command": command,
            "available": False,
            "path": str(Path(executable).resolve()),
            "error": str(error),
        }
    return {
        "command": command,
        "available": result.returncode == 0,
        "path": str(Path(executable).resolve()),
        "executable_sha256": sha256(Path(executable)),
        "version": result.stdout.strip(),
        "version_exit_code": result.returncode,
        **(
            {
                "error": f"version query exited {result.returncode}: {result.stdout.strip()}"
            }
            if result.returncode
            else {}
        ),
    }


def build_entry(entry):
    runtime = entry["settings"]["_backend"].get("runtime_options", [])
    return {**entry, "settings": {
        key: value for key, value in entry["settings"].items() if key not in runtime
    }}


def stage_configuration(config, entry):
    settings = build_entry(entry)["settings"]
    backend = settings["_backend"]
    technology = config["technology"][entry["technology"]]
    sections = {key.split(".")[0] for key in backend.get("technology_inputs", [])}
    selected = {
        key: technology[key]
        for key in {"targets", "assets"} | sections
        if key in technology
    }
    consumed = {}
    for selector in backend.get("config_inputs", []):
        value = config
        for key in selector.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        consumed[selector] = value
    return {
        "design": config["design"] if backend.get("inherit_design", True) else {},
        "flow": settings,
        "technology": selected,
        "configuration": consumed,
        "reproducible": config["build"]["reproducible"],
        "required_commands": config["environment"]["required_commands"],
        "required_variables": config["environment"]["required_variables"],
    }


def entries(config):
    result = []
    for stage, tools in config["flow"].items():
        for tool, settings in tools.items():
            variants = invoke(settings, "variants", config, settings)
            if variants is None:
                variants = {None: settings}
            if not isinstance(variants, dict):
                raise ConfigError(f"{stage}.{tool}: variants must return a settings mapping")
            for name, variant in variants.items():
                if name is not None and (
                    not isinstance(name, str) or not name
                    or Path(name).name != name or name in {".", ".."}
                    or "\n" in name or "\r" in name
                ):
                    raise ConfigError(f"{stage}.{tool}: invalid execution name: {name}")
                if not isinstance(variant, dict) or "_backend" not in variant:
                    raise ConfigError(f"{stage}.{tool}: invalid execution settings")
                for technology in variant["technologies"]:
                    entry = {
                        "stage": stage,
                        "tool": tool,
                        "technology": technology,
                        "settings": variant,
                    }
                    if name is not None:
                        entry["variant"] = name
                    entry["id"] = "/".join([
                        stage, technology, tool, *([name] if name else []),
                        config_digest(stage_configuration(config, entry))[:12],
                    ])
                    result.append(entry)
    return result


def provider_for(config, entry):
    reference = entry["settings"].get("requires")
    if not reference:
        return None
    stage, tool = reference.split(".")
    candidates = [
        candidate
        for candidate in entries(config)
        if candidate["stage"] == stage
        and candidate["tool"] == tool
        and candidate["technology"] == entry["technology"]
    ]
    if len(candidates) != 1:
        raise ConfigError(f"Prerequisite {reference} must select exactly one execution")
    return candidates[0]


def targets_for(config, entry):
    provider = provider_for(config, entry)
    if provider and entry["settings"]["_backend"].get("inherit_targets", False):
        return targets_for(config, provider)
    return list(
        dict.fromkeys(
            config["technology"][entry["technology"]]["targets"]
            + entry["settings"]["targets"]
        )
    )


def inputs_for(config, entry, groups):
    files = set()
    for group in groups:
        files.update(Path(file) for file in group["files"])
        # Header inventory is for invalidation/provenance only. Bender exclusively
        # decides compilation sources, include scopes, defines and ordering.
        for directory in group["include_dirs"]:
            files.update(
                file
                for file in Path(directory).rglob("*")
                if file.is_file()
                and file.suffix.lower() in (".svh", ".vh", ".h", ".hh", ".hpp")
            )
    files.update(Path(__file__).parent.glob("*.py"))
    files.update(extensions.inputs(entry["settings"]["_backend"]["definition"]))
    if entry["settings"].get("script"):
        files.add(Path(entry["settings"]["script"]))
    backend = entry["settings"]["_backend"]
    for pattern in backend.get("inputs", []) + entry["settings"].get("inputs", []):
        matches = [path for path in ROOT.glob(pattern) if path.is_file()]
        if not matches:
            raise ConfigError("Declared input matched no files: " + pattern)
        files.update(matches)
    for section in entry["settings"]["_backend"].get("config_inputs", []):
        settings = config.get(section.split(".")[0], {})
        if isinstance(settings, dict) and "_generator" in settings:
            files.update(extensions.inputs(settings["_generator"]["definition"]))
    technology = config["technology"][entry["technology"]]
    selectors = entry["settings"]["_backend"].get("technology_inputs", [])
    for path in technology_inputs(technology, selectors):
        files.add(Path(path) if Path(path).is_absolute() else ROOT / path)
    files.update(Path(asset["path"]) for asset in technology.get("assets", {}).values())
    return {
        str(path): sha256(path) if path.is_file() else None for path in sorted(files)
    }


def stage_path(entry):
    return ROOT / "build" / entry["id"]


def artifact_paths(config, entry):
    snapshot = json.loads(
        (GENERATED / "flows" / entry["id"] / "inputs.json").read_text()
    )
    return declared_outputs(
        config,
        entry,
        ROOT,
        stage_path(entry),
        GENERATED / "flows" / entry["id"],
        snapshot["input_digest"],
    )


def check_cached_outputs(config, entry, input_digest):
    directory = stage_path(entry)
    stamp = directory / ".done"
    if not stamp.exists():
        return False
    run = directory / "run.json"
    try:
        record = json.loads(run.read_text())
        valid = (
            record["status"] == "passed"
            and record["inputs"]["input_digest"] == input_digest
            and stamp.read_text().strip() == input_digest
            and record["artifacts"]
            and set(record["artifacts"])
            == {str(path) for path in artifact_paths(config, entry)}
            and all(
                Path(path).exists() and sha256(Path(path)) == digest
                for path, digest in record["artifacts"].items()
            )
        )
    except (OSError, ValueError, KeyError):
        valid = False
    if not valid:
        stamp.unlink()
    return bool(valid)


def publish_views(config):
    configured = entries(config)
    for stage in config["flow"]:
        contents, producers = {}, {}
        for entry in (item for item in configured if item["stage"] == stage):
            views = entry["settings"]["_backend"].get("views", {})
            if not views:
                continue
            snapshot_path = GENERATED / "flows" / entry["id"] / "inputs.json"
            snapshot = (
                json.loads(snapshot_path.read_text())
                if snapshot_path.is_file()
                else None
            )
            valid = snapshot is not None and check_cached_outputs(
                config, entry, snapshot["input_digest"]
            )
            values = context(
                config,
                entry,
                ROOT,
                stage_path(entry),
                snapshot_path.parent,
                snapshot["input_digest"] if snapshot else "",
            )
            for name, pattern in views.items():
                destination = "/".join([
                    entry["technology"], entry["tool"],
                    *([entry["variant"]] if "variant" in entry else []), name,
                ])
                producers.setdefault(name, []).append(destination)
                if valid:
                    source = Path(pattern.format_map(values))
                    if source not in artifact_paths(config, entry):
                        raise ConfigError(
                            f"Published view is not a declared build output: {source}"
                        )
                    contents[destination] = source.read_text()
        for name, destinations in producers.items():
            # An automatic stage-wide view is meaningful only for one producer.
            if len(destinations) == 1 and destinations[0] in contents:
                contents[name] = contents[destinations[0]]
        synchronize_outputs(GENERATED / stage, contents)


def prepare(config, update=False, stages=None):
    resolved = entries(config)
    selected = set()
    active = []

    def select(entry):
        if entry["id"] in selected:
            return
        selected.add(entry["id"])
        provider = provider_for(config, entry)
        if provider:
            select(provider)
        active.append(entry)

    for entry in resolved:
        if stages is None or entry["stage"] in stages:
            select(entry)
    generated, declared_sources = generators.generate(config, ROOT)
    updates = {"generator:" + name: outputs for name, outputs in generated.items()}
    for entry in active:
        owner = "backend:" + entry["stage"] + ":" + entry["tool"]
        outputs = invoke(entry["settings"], "generate", config, entry["settings"], ROOT) or {}
        if owner in updates and updates[owner] != outputs:
            raise ConfigError("Technology-dependent shared generator outputs must agree: " + owner)
        updates[owner] = outputs
    generators.publish(ROOT, updates, config["_generation_owners"], complete=config["_complete"])
    # Backend-generated HDL must exist before Bender resolves/globs sources.
    provenance = bender.resolve(config, ROOT, update)
    package_state = bender.packages(ROOT, config["build"]["reproducible"])
    effective_environment = {
        name: os.environ.get(name)
        for name in set(config["environment"]["required_variables"])
        | {
            "PATH",
            "LC_ALL",
            "LANG",
            "TZ",
            "SOURCE_DATE_EPOCH",
        }
    }
    make_lines = [
        ".DELETE_ON_ERROR:",
        ".PHONY: " + " ".join([*config["flow"], "workflow"]),
        ".DEFAULT_GOAL := workflow",
    ]
    by_stage = {stage: [] for stage in config["flow"]}
    build_targets = {stage: [] for stage in config["flow"]}
    snapshots = {}
    for entry in active:
        stage_directory = ROOT / "build" / entry["stage"]
        atomic_write(stage_directory / ".flow-stage", entry["stage"] + "\n")
        backend = entry["settings"]["_backend"]
        directory = GENERATED / "flows" / entry["id"]
        directory.mkdir(parents=True, exist_ok=True)
        active_targets = targets_for(config, entry)
        wanted = generator_names(config, entry["settings"])
        source_groups = [
            bender.source(GENERATED, group)
            for name, groups in declared_sources.items() if name in wanted for group in groups
        ]
        source_groups.extend(
            bender.source(GENERATED, group)
            for group in backend.get("generated_sources", [])
        )
        project_sources = (
            config["design"]["sources"] if backend.get("inherit_design", True)
            else entry["settings"].get("sources", [])
        )
        compilation_root = (
            bender.compilation_root(config, ROOT, active_targets, source_groups, project_sources=project_sources)
            if backend["format"]
            else ROOT
        )
        groups = (
            bender.sources(compilation_root, active_targets)
            if backend["format"]
            else []
        )
        export = (
            bender.export(compilation_root, active_targets, backend["format"])
            if backend["format"]
            else ""
        )
        script = directory / backend["source"]
        exports = {backend["source"]: export} if backend["format"] else {}
        prepared = (
            invoke(
                entry["settings"], "prepare", config,
                {**entry, "source_root": str(compilation_root)}, ROOT, directory, script,
            )
            or {}
        )
        for name, content in prepared.items():
            if name in exports and exports[name] != content:
                raise ConfigError("Conflicting backend output: " + name)
            exports[name] = content
        for name, content in exports.items():
            if not (directory / name).resolve().is_relative_to(directory.resolve()):
                raise ConfigError(
                    "Backend output resolves outside its input directory: " + name
                )
            atomic_write(directory / name, content)
        tools = {
            backend["tool"]: tool_identity(backend["tool"], backend["version_args"])
        }
        tool_commands = {
            **backend.get("tools", {}),
            **(invoke(entry["settings"], "tools", config, entry) or {}),
        }
        for name, argv in tool_commands.items():
            tools[name] = tool_identity(argv[0], argv[1:])
        scope = stage_configuration(config, entry)
        files = inputs_for(config, entry, groups)
        used_packages = {
            name: {key: package[key] for key in ("path", "revision") if key in package}
            for name, package in package_state.items()
            if package["path"] != str(ROOT)
            and any(Path(path).is_relative_to(Path(package["path"])) for path in files)
        }
        provider = provider_for(config, entry)
        snapshot = {
            "entry": build_entry(entry),
            "configuration": scope,
            "configuration_digest": config_digest(scope),
            "bender": {
                key: provenance[key] for key in ("version", "executable_sha256")
            },
            "packages": used_packages,
            "dependency": {
                "id": provider["id"],
                "input_digest": snapshots[provider["id"]]["input_digest"],
            }
            if provider
            else None,
            "targets": active_targets,
            "sources": groups,
            "tools": tools,
            "version_pins": {
                name: config["environment"]["tool_versions"].get(name) for name in tools
            },
            "environment": {
                **effective_environment,
                **{
                    name: os.environ.get(name)
                    for name in backend.get("tracked_environment", [])
                },
            },
            "files": files,
            "exports": {
                str(path): sha256(path)
                for path in (directory / name for name in exports)
            },
            "export_contents": {
                str(path): path.read_text()
                for path in (directory / name for name in exports)
            },
        }
        snapshot["input_digest"] = config_digest(snapshot)
        snapshot_path = directory / "inputs.json"
        write_json(snapshot_path, snapshot)
        snapshots[entry["id"]] = snapshot
        stamp = stage_path(entry) / ".done"
        check_cached_outputs(config, entry, snapshot["input_digest"])
        dependency = ""
        provider = provider_for(config, entry)
        if provider:
            dependency = make_path(stage_path(provider) / ".done")
        # Dependencies are content inventories refreshed before Make is invoked.
        # This catches added/deleted files, restored mtimes and changed tools.
        make_lines.extend(
            [
                make_path(stamp, target=True)
                + ": "
                + make_path(snapshot_path)
                + " "
                + dependency,
                "\t@"
                + make_command(
                    [sys.executable, str(Path(__file__)), "execute", str(snapshot_path)]
                ),
            ]
        )
        build_targets[entry["stage"]].append(make_path(stamp))
        target = make_path(stamp)
        if backend.get("run_after_build"):
            target = make_path(stage_path(entry) / ".run", target=True)
            make_lines.extend([
                ".PHONY: " + target,
                target + ": " + make_path(stamp),
                "\t@" + make_command([sys.executable, str(Path(__file__)), "execute-run", str(snapshot_path)]),
            ])
        by_stage[entry["stage"]].append(target)
    for stage, stamps in by_stage.items():
        make_lines.extend([
            ".PHONY: " + stage + "-build",
            stage + "-build: " + " ".join(build_targets[stage]),
            stage + ": " + " ".join(stamps),
        ])
        if not stamps:
            make_lines.append(
                "\t@echo 'ERROR: no backend configured for " + stage + "' >&2; exit 2"
            )
    make_lines.append("workflow: " + " ".join(config["workflow"]["stages"]))
    for stage in config["flow"]:
        make_lines.append(f".PHONY: {stage}-run")
        make_lines.append(f"{stage}-run: {stage}")
        candidates = [entry for entry in active if entry["stage"] == stage]
        if candidates and all(item["settings"]["_backend"].get("run_after_build") for item in candidates):
            continue
        if len(candidates) == 1:
            runtime_input = GENERATED / "flows" / candidates[0]["id"] / "inputs.json"
            make_lines.append(
                "\t@"
                + make_command(
                    [
                        sys.executable,
                        str(Path(__file__)),
                        "execute-run",
                        str(runtime_input),
                    ]
                )
            )
        else:
            make_lines.append(
                f"\t@echo 'ERROR: {stage}-run requires exactly one configured backend and technology' >&2; exit 2"
            )
    atomic_write(GENERATED / "build.mk", "\n".join(make_lines) + "\n")
    write_json(
        GENERATED / "manifest.json",
        {
            "configuration": config,
            "configuration_digest": config_digest(config),
            "bender": provenance,
            "packages": package_state,
            "flows": {key: value["input_digest"] for key, value in snapshots.items()},
            "framework": {
                "package": "dicflow",
                "path": str(Path(__file__).resolve().parents[2]),
            },
        },
    )
    return active


def preflight(config, snapshot):
    if (
        snapshot["entry"]["settings"]["_backend"].get("requires_top")
        and not config["design"]["top"]
    ):
        raise ConfigError(
            f"{snapshot['entry']['stage']}.{snapshot['entry']['tool']} requires design.top"
        )
    for name in config["environment"]["required_commands"]:
        if shutil.which(name) is None:
            raise ConfigError("Required command not found: " + name)
    for name in config["environment"]["required_variables"]:
        if not os.environ.get(name):
            raise ConfigError("Required environment variable is unset: " + name)
    for name, identity in snapshot["tools"].items():
        if not identity["available"]:
            if identity.get("error"):
                raise ConfigError(
                    f"Required tool cannot be executed: {name} ({identity['path']}): "
                    f"{identity['error']}"
                )
            raise ConfigError("Required tool not found: " + name)
        executable = Path(identity["path"])
        if (
            not executable.is_file()
            or sha256(executable) != identity["executable_sha256"]
        ):
            raise ConfigError("Tool changed after preparation: " + name)
        pin = config["environment"]["tool_versions"].get(name)
        if pin and pin not in identity["version"]:
            raise ConfigError("Tool version mismatch: " + name + " requires " + pin)
    entry = snapshot["entry"]
    expected_version = entry["settings"].get("version")
    actual = snapshot["tools"][entry["settings"]["_backend"]["tool"]]["version"]
    if expected_version and expected_version not in actual:
        raise ConfigError(
            "EDA version mismatch: expected " + expected_version + ", got " + actual
        )
    for path, checksum in {
        **snapshot["files"],
        **snapshot["exports"],
        **snapshot.get("runtime_inputs", {}),
        **snapshot.get("dependency_inputs", {}),
        **snapshot.get("dependency_artifacts", {}),
    }.items():
        if checksum is None:
            raise ConfigError("Required input is missing: " + path)
        if not Path(path).exists() or sha256(Path(path)) != checksum:
            raise ConfigError("Input changed after preparation; rerun make: " + path)
    technology = config["technology"][entry["technology"]]
    assets = {asset["path"]: asset for asset in technology.get("assets", {}).values()}
    for path, asset in assets.items():
        if asset.get("sha256") and sha256(Path(path)) != asset["sha256"]:
            raise ConfigError("Technology asset checksum mismatch: " + path)
    if config["build"]["reproducible"]:
        if not expected_version:
            raise ConfigError("Reproducible builds require flow backend version pins")
        for name in snapshot["tools"]:
            if (
                name != entry["settings"]["_backend"]["tool"]
                and name not in config["environment"]["tool_versions"]
            ):
                raise ConfigError(
                    "Reproducible builds require environment.tool_versions." + name
                )
        for path in snapshot["files"]:
            if not Path(path).is_relative_to(ROOT) and path not in assets:
                # External IP sources are pinned by Bender, technology assets by checksums.
                if not any(
                    Path(path).is_relative_to(Path(package["path"]))
                    for package in snapshot["packages"].values()
                    if package["path"] != str(ROOT)
                ):
                    raise ConfigError("Unpinned external asset: " + path)
        for path, asset in assets.items():
            if not asset.get("sha256"):
                raise ConfigError("Reproducible builds require asset SHA256: " + path)
    invoke(entry["settings"], "preflight", config, entry, ROOT)


def stream_command(argv, cwd, environment, log):
    log.write("$ " + shlex.join(argv) + "\n")
    log.flush()
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        for line in child.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
        return child.wait()
    except BaseException:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait()
        raise


def execute(snapshot_path, run=False):
    snapshot = json.loads(snapshot_path.read_text())
    config = load_config(ROOT / "Project.toml", ROOT, action=snapshot["entry"]["stage"])
    check_framework(config)
    candidates = [item for item in entries(config) if build_entry(item) == snapshot["entry"]]
    if len(candidates) != 1:
        raise ConfigError("Stale or unrecognized stage descriptor; rerun make")
    entry = candidates[0]
    expected_input = GENERATED / "flows" / entry["id"] / "inputs.json"
    if snapshot_path.resolve() != expected_input.resolve():
        raise ConfigError("Stage inputs must come from the generated flow directory")
    directory = stage_path(entry)
    if run:
        directory = (
            directory / "runs" / (str(time.time_ns()) + "-" + uuid.uuid4().hex[:8])
            if entry["settings"]["_backend"].get("preserve_runs")
            else directory / "run"
        )
    if not directory.resolve().is_relative_to((ROOT / "build").resolve()):
        raise ConfigError("Stage output directory resolves outside build/")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = directory / ".done"
    if stamp.exists():
        stamp.unlink()
    for name in ("work", "reports", "logs", "results"):
        if (directory / name).is_dir():
            shutil.rmtree(directory / name)
        (directory / name).mkdir(exist_ok=True)
    environment = dict(
        os.environ,
        PROJECT_ROOT=str(ROOT),
        RTL_TOP=config["design"]["top"],
        BUILD_JOBS=str(config["build"]["jobs"]),
        BENDER_SCRIPT=str(
            snapshot_path.parent / entry["settings"]["_backend"]["source"]
        ),
        STAGE_WORK_DIR=str(directory / "work"),
        STAGE_REPORT_DIR=str(directory / "reports"),
        STAGE_RESULT_DIR=str(directory / "results"),
        DEPENDENCY_RESULTS=str(stage_path(provider_for(config, entry)) / "results")
        if provider_for(config, entry)
        else "",
    )
    environment.update(
        backend_environment(
            config,
            entry,
            ROOT,
            directory,
            snapshot_path.parent,
            snapshot["input_digest"],
        )
    )
    started = time.time()
    status = 1
    error = None
    result_commands = []
    artifacts = {}
    succeeded = False
    print(
        "["
        + entry["stage"].upper()
        + "::"
        + entry["technology"]
        + "::"
        + entry["tool"]
        + ("::" + entry["variant"] if "variant" in entry else "")
        + "] "
        + ("Running program" if run else "Executing stage"),
        flush=True,
    )
    with (directory / "logs/run.log").open("w") as log:
        try:
            if snapshot["configuration_digest"] != config_digest(
                stage_configuration(config, entry)
            ):
                raise ConfigError("Configuration changed after preparation; rerun make")
            if snapshot.get("dependency"):
                provider = provider_for(config, entry)
                if not provider or provider["id"] != snapshot["dependency"]["id"]:
                    raise ConfigError("Prerequisite configuration changed; rerun make")
                dependency = ROOT / "build" / snapshot["dependency"]["id"]
                record = json.loads((dependency / "run.json").read_text())
                if (
                    record["status"] != "passed"
                    or record["inputs"]["input_digest"]
                    != snapshot["dependency"]["input_digest"]
                    or (dependency / ".done").read_text().strip()
                    != snapshot["dependency"]["input_digest"]
                ):
                    raise ConfigError("Prerequisite build is not current")
                snapshot = dict(
                    snapshot,
                    dependency_inputs={
                        **record["inputs"].get("dependency_inputs", {}),
                        **record["inputs"]["files"],
                        **record["inputs"]["exports"],
                    },
                    dependency_artifacts={
                        **record["inputs"].get("dependency_artifacts", {}),
                        **record["artifacts"],
                    },
                )
            preflight(config, snapshot)
            if run:
                if not check_cached_outputs(config, entry, snapshot["input_digest"]):
                    raise ConfigError("Compiled artifacts changed; rebuild before running")
                runtime = invoke(
                    entry["settings"], "run", config,
                    {**entry, "run_directory": str(directory)}, stage_path(entry)
                )
                if not runtime:
                    raise ConfigError("This backend does not implement a run action")
                result_commands = runtime["commands"]
                snapshot = dict(
                    snapshot,
                    runtime_inputs={
                        str(path): sha256(Path(path))
                        for path in runtime.get("inputs", [])
                    },
                )
            else:
                result_commands = commands(
                    config,
                    entry,
                    ROOT,
                    directory,
                    snapshot_path.parent,
                    snapshot["input_digest"],
                )
            status = 0
            for argv in result_commands:
                status = stream_command(argv, directory / "work", environment, log)
                if status:
                    break
            if status == 0 and not run:
                invoke(entry["settings"], "collect", config, entry, directory, snapshot)
            summary = directory / "reports/summary.rpt"
            log.flush()
            use_log = run or entry["settings"]["_backend"].get("summary") == "log"
            if (use_log and not summary.exists()) or status:
                shutil.copyfile(directory / "logs/run.log", summary)
            if status == 0:
                # Never certify a run whose inputs were modified while EDA ran.
                preflight(config, snapshot)
                expected = (
                    [summary, *declared_outputs(config, entry, ROOT, directory,
                        snapshot_path.parent, snapshot["input_digest"], run=True)]
                    if run else artifact_paths(config, entry)
                )
                for path in expected:
                    if not path.exists() or (path.is_file() and not path.stat().st_size):
                        raise ConfigError(
                            "Tool did not create required artifact: " + str(path)
                        )
                artifacts = {str(path): sha256(path) for path in expected}
                succeeded = True
        except (Exception, KeyboardInterrupt, SystemExit) as exception:
            status = 130 if isinstance(exception, KeyboardInterrupt) else 1
            error = str(exception)
            print("ERROR: " + error, file=sys.stderr)
            log.write("ERROR: " + error + "\n")
        finally:
            if not succeeded and status == 0:
                status = 1
                error = (
                    error or "Stage interrupted before artifact validation completed"
                )
            log.flush()
            if status != 0:
                shutil.copyfile(
                    directory / "logs/run.log", directory / "reports/summary.rpt"
                )
            write_json(
                directory / "run.json",
                {
                    "schema_version": 2,
                    "status": "passed" if status == 0 else "failed",
                    "exit_code": status,
                    "started_epoch": started,
                    "finished_epoch": time.time(),
                    "commands": result_commands,
                    "inputs": snapshot,
                    "configuration": config,
                    "artifacts": artifacts,
                    "error": error,
                },
            )
            if status == 0 and not run:
                atomic_write(stamp, snapshot["input_digest"] + "\n")
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action")
    parser.add_argument("input", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        if args.action in {"execute", "execute-run"}:
            if args.input is None:
                parser.error(f"{args.action} requires a prepared input snapshot")
            return execute(args.input, args.action == "execute-run")
        (ROOT / "build").mkdir(exist_ok=True)
        with project_lock(ROOT):
            config = load_config(ROOT / "Project.toml", ROOT, action=args.action)
            check_framework(config)
            action = args.action.removesuffix("-build")
            stage = action.removesuffix("-run")
            if (
                action not in {"config", "deps-update", "workflow"}
                and stage not in config["flow"]
            ):
                raise ConfigError(f"No configured stage: {stage}")
            goals = config["workflow"]["stages"] if action == "workflow" else [args.action if args.action.endswith("-build") else action]
            if args.action == "config":
                stages = None
            elif args.action == "deps-update":
                stages = []
            else:
                stages = [goal.removesuffix("-build").removesuffix("-run") for goal in goals]
            updated = os.environ.get("PROJECT_BOOTSTRAP_LOCK")
            if updated and sha256(ROOT / "Bender.lock") != updated:
                raise ConfigError(
                    "Dependency lock changed after framework bootstrap; rerun make"
                )
            prepare(
                config,
                update=args.action == "deps-update" and not updated,
                stages=stages,
            )
            publish_views(config)
            if args.action in ("config", "deps-update"):
                print("[CONFIG] Bender inputs and project configuration synchronized")
                return 0
            if not goals:
                print("[WORKFLOW] No stages configured")
            for goal in goals:
                status = subprocess.call(
                    [
                        os.environ.get("PROJECT_MAKE", "make"),
                        "--no-print-directory",
                        "--keep-going",
                        "-f",
                        str(GENERATED / "build.mk"),
                        "-j",
                        str(config["build"]["jobs"]),
                        goal,
                    ],
                    cwd=ROOT,
                )
                if status:
                    return status
                publish_views(config)
            return 0
    except (ConfigError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
