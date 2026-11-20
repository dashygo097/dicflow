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
}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def plain(value):
    return ANSI.sub("", str(value))


def emit(status, message, *, stream=None):
    stream = stream or sys.stdout
    mode = os.environ.get("DICFLOW_COLOR", "auto")
    colored = mode == "always" or (
        mode == "auto"
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM") != "dumb"
        and stream.isatty()
    )
    label = f"[{status}]"
    if colored:
        label = f"\033[{COLORS.get(status, '36')}m{label}\033[0m"
    print(f"{label} {plain(message)}", file=stream, flush=True)


def verbose():
    return os.environ.get("DICFLOW_VERBOSE", "").lower() in {"1", "true", "yes"}


def failure_tail(path, limit=12):
    # EDA logs can be gigabytes. Bound both memory and I/O, not just line count.
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - 65536))
        lines = source.read().decode("utf-8", errors="replace").splitlines()[-limit:]
    for line in lines:
        print("  " + plain(line.rstrip()), file=sys.stderr)
