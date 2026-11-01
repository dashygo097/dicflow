"""Execute a module test/seed matrix and normalize checked simulator results.

The testbench must emit exactly one DICFLOW_VERIFY_PASS checks=<positive count>
using dicflow_verify_pkg::pass_test. Exit zero alone never certifies a test.
"""

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

_active = None


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def interrupted(signum, frame):
    stop_child(_active)
    raise KeyboardInterrupt


def inspect_log(path):
    markers, checks, errors = 0, 0, False
    tail = deque(maxlen=30)
    with path.open(errors="replace") as stream:
        for line in stream:
            tail.append(line.rstrip())
            match = re.fullmatch(r"DICFLOW_VERIFY_PASS checks=(\d+)\s*", line)
            if match:
                markers += 1
                checks = int(match.group(1))
            if re.search(r"\bUVM_(ERROR|FATAL)\b", line):
                if not re.fullmatch(r"\s*UVM_(ERROR|FATAL)\s*:\s*0\s*", line):
                    errors = True
            if re.match(r"\s*(?:%Error|Error[-:]|Fatal[-:]|FATAL:)", line):
                errors = True
    return markers == 1 and checks > 0 and not errors, checks, "\n".join(tail)[-12000:]


def run_case(executable, simulator, test, seed, timeout, work, logs):
    global _active
    identity = hashlib.sha256(f"{test}:{seed}".encode()).hexdigest()[:12]
    directory = work / identity
    directory.mkdir()
    log = logs / (identity + ".log")
    seed_argument = f"+verilator+seed+{seed}" if simulator == "verilator" else f"+ntb_random_seed={seed}"
    argv = [str(executable), f"+TEST={test}", f"+UVM_TESTNAME={test}", seed_argument]
    started = time.time()
    status, code, detail = "ERROR", None, ""
    try:
        with log.open("w") as output:
            _active = subprocess.Popen(argv, cwd=directory, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = _active.wait(timeout=timeout)
                status = "PASS" if code == 0 else "FAIL"
            except subprocess.TimeoutExpired:
                stop_child(_active)
                code, status = _active.returncode, "TIMEOUT"
                detail = f"Exceeded {timeout:g} seconds"
    except OSError as error:
        detail = str(error)
    finally:
        stop_child(_active)
        _active = None
    passed, checks, tail = inspect_log(log)
    if status == "PASS" and not passed:
        status, detail = "FAIL", "Missing or invalid completion marker, or simulator reported errors"
    result = {
        "test": test, "seed": seed, "status": status, "exit_code": code,
        "checks": checks, "seconds": time.time() - started,
        "command": argv, "cwd": str(directory), "log": str(log),
        "reproduce": "cd " + shlex.quote(str(directory)) + " && " + shlex.join(argv),
        "detail": detail, "tail": tail if status != "PASS" else "",
        "waveforms": [str(path) for path in directory.rglob("*")
                      if path.is_file() and path.suffix in (".vcd", ".fst", ".vpd", ".fsdb")],
    }
    print(f"[VERIFY] {test} seed={seed}: {status} ({result['seconds']:.3f}s)", flush=True)
    return result


def reports(results, directory):
    directory.mkdir(parents=True, exist_ok=True)
    counts = {status: sum(item["status"] == status for item in results)
              for status in ("PASS", "FAIL", "TIMEOUT", "ERROR")}
    summary = {"status": "PASS" if results and counts["PASS"] == len(results) else "FAIL",
               "counts": counts, "cases": results}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    suite = ET.Element("testsuite", name="dicflow.verify", tests=str(len(results)),
                       failures=str(counts["FAIL"]), errors=str(counts["TIMEOUT"] + counts["ERROR"]))
    for result in results:
        case = ET.SubElement(suite, "testcase", name=f"{result['test']}[seed={result['seed']}]",
                             time=str(result["seconds"]))
        if result["status"] != "PASS":
            tag = "failure" if result["status"] == "FAIL" else "error"
            failure = ET.SubElement(case, tag, type=result["status"], message=result["detail"])
            failure.text = result.get("tail", "")
    ET.ElementTree(suite).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
    (directory / "summary.rpt").write_text(
        "Verification " + summary["status"] + "\n"
        + "\n".join(f"{name}: {count}" for name, count in counts.items()) + "\n"
    )
    return 0 if summary["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", choices=("vcs", "verilator"), required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    matrix = [(test, seed) for test in plan["tests"] for seed in plan["seeds"]]
    if not matrix:
        raise ValueError("Verification matrix must contain at least one case")
    work = Path(os.environ["STAGE_WORK_DIR"]) / "cases"
    logs = Path(os.environ["STAGE_REPORT_DIR"]) / "cases"
    work.mkdir(parents=True)
    logs.mkdir(parents=True)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    results = []
    try:
        for test, seed in matrix:
            results.append(run_case(args.executable, args.simulator, test, seed,
                                    plan["timeout_seconds"], work, logs))
    except KeyboardInterrupt:
        for test, seed in matrix[len(results):]:
            results.append({"test": test, "seed": seed, "status": "ERROR",
                            "detail": "Interrupted", "seconds": 0})
        reports(results, Path(os.environ["STAGE_REPORT_DIR"]))
        return 130
    return reports(results, Path(os.environ["STAGE_REPORT_DIR"]))


if __name__ == "__main__":
    raise SystemExit(main())
