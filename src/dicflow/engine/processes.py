"""Process-group cancellation and scheduler diagnostics."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile


def stop_group(child, sig=signal.SIGTERM, timeout=3):
    """Give the entire child group a bounded cleanup period, then reap it."""
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        child.wait()
        return
    try:
        child.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def run_scheduler(arguments, cwd):
    # A separate process group gives the CLI ownership of Ctrl-C. Make stderr
    # is spooled rather than piped so verbose diagnostics cannot block workers.
    with tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="replace"
    ) as errors:
        child = subprocess.Popen(
            arguments, cwd=cwd, stderr=errors, start_new_session=True
        )
        try:
            status = child.wait()
        except KeyboardInterrupt:
            stop_group(child, signal.SIGINT, timeout=5)
            raise
        if status in {-signal.SIGINT, 130}:
            raise KeyboardInterrupt
        errors.seek(0)
        shutil.copyfileobj(errors, sys.stderr)
        return status
