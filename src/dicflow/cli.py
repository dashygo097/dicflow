"""Public CLI; builds use the locked framework unless explicitly overridden."""

import argparse
import os
import sys
from importlib.metadata import version
from pathlib import Path

from . import bootstrap, outputs
from .console import emit, relative_path
from .help import ArgumentParser


def common_options(parser):
    parser.add_argument(
        "--engine",
        type=Path,
        default=argparse.SUPPRESS,
        help="use a local dicflow checkout for development (does not change Bender.lock)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="project directory (defaults to searching upward from cwd)",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default=argparse.SUPPRESS,
        help="terminal color policy",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="stream full tool output",
    )


def parser_for_cli():
    parser = ArgumentParser(
        prog="dicflow",
        description="Run and inspect retargetable hardware workflows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  dicflow run synth\n  dicflow status\n"
        "  dicflow logs functional_hold\n  dicflow --root ../discv run sim",
    )
    common_options(parser)
    parser.add_argument(
        "--version", action="version", version=f"dicflow {version('dicflow')}"
    )
    commands = parser.add_subparsers(
        dest="command", metavar="COMMAND", title="Commands"
    )

    def command(name, help_text):
        child = commands.add_parser(name, help=help_text, description=help_text)
        common_options(child)
        return child

    for name, description in {
        "status": "Show recorded build outcomes",
        "artifacts": "Find generated artifacts and reports",
        "logs": "Find execution logs",
    }.items():
        child = command(name, description)
        child.add_argument(
            "selector",
            nargs="?",
            default=os.environ.get("SELECT", ""),
            help="substring of stage / technology / backend / variant",
        )
        child.add_argument(
            "--json", action="store_true", help="machine-readable records"
        )

    command("init", "Create Project.toml from the project's template")
    deps = command("deps", "Manage locked Bender dependencies")
    deps_commands = deps.add_subparsers(dest="dependency_action", required=True)
    common_options(deps_commands.add_parser("update", help="Update Bender.lock"))
    run = command("run", "Run a configured stage using the project's locked engine")
    run.add_argument("stage", help="stage name, e.g. synth, sta, verify, or sim-run")
    for name in ("lint", "synth", "sta", "sim", "verify", "formal"):
        command(name, f"Shortcut for 'dicflow run {name}'")
    for name, description in {
        "workflow": "Run the project's configured workflow",
        "config": "Prepare configuration and tool input files",
        "env": "Prepare the locked engine's project environment",
        "clean": "Remove generated flow outputs, preserving local configuration",
        "distclean": "Remove all build outputs and environments, preserving configuration",
    }.items():
        command(name, description)
    return parser


def project_root(start):
    directory = start.resolve()
    for candidate in (directory, *directory.parents):
        if (candidate / "Bender.yml").is_file():
            return candidate
    raise RuntimeError(
        f"No Bender.yml found above {directory}; select a project with --root"
    )


def dispatch(args):
    root = project_root(getattr(args, "root", Path.cwd()))
    os.environ["PROJECT_ROOT"] = str(root)
    if hasattr(args, "color"):
        os.environ["DICFLOW_COLOR"] = args.color
    if getattr(args, "verbose", False):
        os.environ["DICFLOW_VERBOSE"] = "1"
    if args.command in {"status", "artifacts", "logs"}:
        return outputs.show(root, args.command, args.selector, json_output=args.json)
    if args.command == "init":
        template = root / ".project.template.toml"
        if not template.is_file():
            raise RuntimeError(f"Project template is missing: {template}")
        content = template.read_bytes()
        with (root / "Project.toml").open("xb") as stream:
            stream.write(content)
        emit("INFO", "Created " + relative_path(root / "Project.toml", root))
        return 0
    action = args.stage if args.command == "run" else args.command
    if args.command == "deps":
        action = "deps-update"
    bootstrap.main([action], engine=getattr(args, "engine", None))
    return 0


def main(argv=None):
    # Help exits during parsing, before dispatch. Resolve its style policy first,
    # allowing --color before or after a command without altering parsed values.
    argv = sys.argv[1:] if argv is None else argv
    appearance = ArgumentParser(prog="dicflow", add_help=False)
    appearance.add_argument("--color", choices=["auto", "always", "never"])
    options, _ = appearance.parse_known_args(argv)
    if options.color:
        os.environ["DICFLOW_COLOR"] = options.color
    parser = parser_for_cli()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        emit("WARNING", "Interrupted", stream=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        emit("ERROR", str(error), stream=sys.stderr)
        return 1
