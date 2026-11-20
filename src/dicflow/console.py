"""Dependency-free terminal presentation; never write ANSI escapes to log files."""

import os
import re
import sys

COLORS = {
    "PASSED": "32",
    "FAILED": "31",
    "ERROR": "31",
    "RUNNING": "36",
    "CACHED": "90",
    "INFO": "36",
    "WARNING": "33",
    "INCOMPLETE": "33",
    "INTERRUPTED": "33",
}
TONES = {"accent": "36", "brand": "35", "muted": "90", "error": "31"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def plain(value):
    return ANSI.sub("", str(value))


def supports_style(stream):
    """One policy for colors and text attributes, including help and errors."""
    mode = os.environ.get("DICFLOW_COLOR", "auto")
    return mode == "always" or (
        mode == "auto"
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
        and stream.isatty()
    )


def style(value, *, tone=None, bold=False, dim=False, italic=False, stream=None):
    stream = stream or sys.stdout
    text = plain(value)
    if not supports_style(stream):
        return text
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if italic:
        codes.append("3")
    if tone:
        codes.append(TONES.get(tone, COLORS.get(tone, "39")))
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else text


def heading(title, subtitle="", *, stream=None):
    stream = stream or sys.stdout
    line = "  " + style(title, tone="brand", bold=True, stream=stream)
    if subtitle:
        line += "  " + style(subtitle, dim=True, stream=stream)
    print(line, file=stream, flush=True)


def note(message, *, stream=None):
    stream = stream or sys.stdout
    print("  " + style(message, dim=True, italic=True, stream=stream), file=stream)


def detail(label, value, *, stream=None):
    stream = stream or sys.stdout
    print(
        " " * 13
        + style(label + ":", dim=True, stream=stream)
        + " "
        + style(value, tone="accent", stream=stream),
        file=stream,
        flush=True,
    )


def emit(status, message, *, stream=None, suffix=""):
    stream = stream or sys.stdout
    label = style(status.title().rjust(10), tone=status, bold=True, stream=stream)
    line = f"  {label} {plain(message)}"
    if suffix:
        line += "  " + style(suffix, dim=True, stream=stream)
    # A single write keeps parallel worker status lines together.
    print(line, file=stream, flush=True)


def verbose():
    return os.environ.get("DICFLOW_VERBOSE", "").lower() in {"1", "true", "yes"}


def failure_tail(path, limit=12):
    # EDA logs can be gigabytes. Bound both memory and I/O, not just line count.
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - 65536))
        lines = source.read().decode("utf-8", errors="replace").splitlines()[-limit:]
    for line in lines:
        print(
            " " * 13
            + style("|", dim=True, stream=sys.stderr)
            + " "
            + plain(line.rstrip()),
            file=sys.stderr,
        )
