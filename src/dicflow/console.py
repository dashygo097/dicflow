"""Rich presentation for humans; structured output and saved logs stay plain."""

import os
import re
import sys
from functools import lru_cache

from rich.console import Console, Group
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "brand": "bold cyan",
        "muted": "dim",
        "note": "dim italic",
        "PASSED": "bold green",
        "FAILED": "bold red",
        "ERROR": "bold red",
        "RUNNING": "bold cyan",
        "PREPARING": "dim italic",
        "DONE": "bold green",
        "CACHED": "dim",
        "INFO": "dim",
        "WARNING": "bold yellow",
        "INCOMPLETE": "yellow",
        "INTERRUPTED": "yellow",
    }
)
LABELS = {
    "PASSED": "PASS",
    "FAILED": "FAIL",
    "RUNNING": "RUN",
    "PREPARING": "PREP",
    "CACHED": "CACHE",
    "WARNING": "WARN",
    "INTERRUPTED": "STOP",
    "INCOMPLETE": "PARTIAL",
}
MARGIN = "  "
LABEL_WIDTH = 7
GAP = "  "
CONTENT_INDENT = MARGIN + " " * LABEL_WIDTH + GAP
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def plain(value):
    return ANSI.sub("", str(value))


def relative_path(path, root):
    """Format project-relative paths without resolving navigation symlinks."""
    value = os.fspath(path)
    if not os.path.isabs(value):
        return value
    try:
        return os.path.relpath(value, root)
    except ValueError:
        return value


def supports_style(stream):
    mode = os.environ.get("DICFLOW_COLOR", "auto")
    return mode == "always" or (
        mode == "auto"
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") not in {"dumb", "unknown"}
        and stream.isatty()
    )


@lru_cache(maxsize=8)
def _console(stream, styled):
    return Console(
        file=stream,
        theme=THEME,
        force_terminal=styled,
        force_jupyter=False,
        force_interactive=False,
        color_system="standard" if styled else None,
        no_color=not styled,
        markup=False,
        highlight=False,
        emoji=False,
        soft_wrap=True,
    )


def get_console(stream=None):
    stream = stream or sys.stdout
    return _console(stream, supports_style(stream))


def _row(label, value, *, label_style="", value_style="", suffix=""):
    """Use one label column; long extension labels get a line of their own."""
    label = Text(plain(label), style=label_style)
    line = Text(MARGIN)
    line.append_text(label)
    if label.cell_len > LABEL_WIDTH:
        line.append("\n" + CONTENT_INDENT)
    else:
        line.append(" " * (LABEL_WIDTH - label.cell_len) + GAP)
    line.append(plain(value), style=value_style)
    if suffix:
        line.append(GAP + plain(suffix), style="muted")
    return line


def heading(title, subtitle="", *, stream=None):
    get_console(stream).print(
        _row(title, subtitle, label_style="brand", value_style="muted")
    )


def note(message, *, stream=None):
    get_console(stream).print(_row("", message, value_style="note"))


def detail(label, value, *, stream=None):
    get_console(stream).print(
        _row(label, value, label_style="muted", value_style="muted")
    )


def _status(status, message, suffix=""):
    return _row(
        LABELS.get(status, status), message,
        label_style=status if status in THEME.styles else "INFO", suffix=suffix,
    )


def emit(status, message, *, stream=None, suffix=""):
    get_console(stream).print(_status(status, message, suffix))


def stage_start(message, log, *, kind="build"):
    # Keep status and path together, with explicit line endings under Make/CI.
    get_console().print(
        Group(
            Text(""),
            _status("RUNNING", message, kind),
            _row("log", log, label_style="muted", value_style="muted"),
        )
    )


def verbose():
    return os.environ.get("DICFLOW_VERBOSE", "").lower() in {"1", "true", "yes"}


def failure_tail(path, limit=12):
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - 65536))
        lines = source.read().decode("utf-8", errors="replace").splitlines()[-limit:]
    get_console(sys.stderr).print(
        Group(*(Text(CONTENT_INDENT + "| " + plain(line)) for line in lines))
    )
