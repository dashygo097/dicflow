"""Rich argparse integration using the same terminal policy as build output."""

import argparse
import sys
from functools import partial

from rich_argparse import RawDescriptionRichHelpFormatter

from .console import emit, get_console, heading, note


class HelpFormatter(RawDescriptionRichHelpFormatter):
    styles = {
        **RawDescriptionRichHelpFormatter.styles,
        "argparse.prog": "bold cyan",
        "argparse.groups": "bold",
        "argparse.args": "bold cyan",
        "argparse.metavar": "dim",
        "argparse.text": "default",
    }
    help_markup = False
    text_markup = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("console", get_console())
        super().__init__(*args, **kwargs)


class ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        if sys.version_info >= (3, 14):
            kwargs["color"] = False
        kwargs.setdefault("allow_abbrev", False)
        kwargs.setdefault("formatter_class", HelpFormatter)
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        stream = file or sys.stdout
        if self.prog == "dicflow":
            from importlib.metadata import version

            get_console(stream).print()
            heading("dicflow", "v" + version("dicflow"), stream=stream)
            get_console(stream).print()
        # Match the destination's color policy, including redirected help.
        formatter = self.formatter_class
        self.formatter_class = partial(formatter, console=get_console(stream))
        try:
            super().print_help(file=stream)
        finally:
            self.formatter_class = formatter

    def error(self, message):
        emit("ERROR", message, stream=sys.stderr)
        note(f"Run '{self.prog} --help' for usage.", stream=sys.stderr)
        self.exit(2)
