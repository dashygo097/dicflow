"""Terminal help presentation over argparse's plain-text layout and wrapping."""

import argparse
import re
import sys

from .console import emit, heading, style, supports_style


class ArgumentParser(argparse.ArgumentParser):
    """Style only rendered help, so ANSI sequences never affect text wrapping."""

    def __init__(self, *args, **kwargs):
        # Python 3.14+ has its own help colors. Use our single terminal policy.
        if sys.version_info >= (3, 14):
            kwargs["color"] = False
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        stream = file or sys.stdout
        if self.prog == "dicflow":
            from importlib.metadata import version

            print(file=stream)
            heading("dicflow", "v" + version("dicflow"), stream=stream)
            print(file=stream)
        text = self.format_help()
        if supports_style(stream):
            # argparse already chose line breaks using unstyled character widths.
            lines = []
            for line in text.splitlines():
                if line.startswith("usage:"):
                    line = style("Usage:", bold=True, stream=stream) + line[6:]
                elif line and not line.startswith(" ") and line.endswith(":"):
                    line = style(line[0].upper() + line[1:], bold=True, stream=stream)
                elif re.match(r"^\s{2,}[\w-]+\s{2,}\S", line):
                    line = re.sub(
                        r"^(\s{2,})([\w-]+)",
                        lambda match: (
                            match[1]
                            + style(match[2], tone="accent", bold=True, stream=stream)
                        ),
                        line,
                        count=1,
                    )
                elif line.startswith("  -"):
                    line = re.sub(
                        r"(?<!\w)--?[\w-]+",
                        lambda match: style(
                            match[0], tone="accent", bold=True, stream=stream
                        ),
                        line,
                    )
                elif line.startswith("  dicflow "):
                    line = "  " + style("dicflow", bold=True, stream=stream) + line[9:]
                elif line and not line.startswith(" "):
                    line = style(line, dim=True, italic=True, stream=stream)
                lines.append(line)
            text = "\n".join(lines) + "\n"
        stream.write(text)

    def error(self, message):
        emit("ERROR", message, stream=sys.stderr)
        print(
            "\n  "
            + style(
                f"Run '{self.prog} --help' for usage.", dim=True, stream=sys.stderr
            ),
            file=sys.stderr,
        )
        self.exit(2)
