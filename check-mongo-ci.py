#!/usr/bin/env python
"""Run the MongoDB CI job locally and print a short report.

The `mongo` workflow needs a real replica set, so it cannot be reproduced anywhere that
has no MongoDB. This runs exactly what that job runs and prints about forty lines instead
of five thousand, so the result can be pasted back verbatim.

    python check-mongo-ci.py

Needs Docker and uv. Nothing else. It starts the compose `mongo` service, runs the two
steps the job runs, and stops the container again.

    --keep     leave MongoDB running afterwards
    --no-up    use a replica set that is already running
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIDDLEWARE = ROOT / "telecom-middleware"
URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=false"
FLOOR = 95.0

BAR = "=" * 72


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout + result.stderr


def say(text: str = "") -> None:
    print(text, flush=True)


def tail(output: str, lines: int = 25) -> str:
    kept = [line for line in output.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def summarise_pytest(output: str) -> str:
    """The counts line, plus every FAILED/ERROR name. Not the tracebacks."""
    interesting = [
        line
        for line in output.splitlines()
        if line.startswith(("FAILED", "ERROR "))
        or re.search(r"\d+ (passed|failed|error|deselected)", line)
    ]
    return "\n".join(interesting[-25:]) or tail(output, 10)


def coverage_gap(xml_path: Path) -> str:
    """Which files are short, and by how many statements, from coverage.xml."""
    if not xml_path.exists():
        return "  (no coverage.xml written)"

    tree = ET.parse(xml_path)  # noqa: S314 - our own build output
    rows: list[tuple[int, int, str, str]] = []
    covered_total = valid_total = 0

    for cls in tree.iter("class"):
        lines = [line for line in cls.iter("line") if line.get("hits") is not None]
        if not lines:
            continue
        valid = len(lines)
        missed_lines = [int(line.get("number", 0)) for line in lines if line.get("hits") == "0"]
        covered_total += valid - len(missed_lines)
        valid_total += valid
        if missed_lines:
            name = cls.get("filename", cls.get("name", "?"))
            ranges = compress(missed_lines)
            rows.append((len(missed_lines), valid, name, ranges))

    rows.sort(reverse=True)
    report = [f"  {'missed':>6}  {'stmts':>5}  file", f"  {'-' * 6}  {'-' * 5}  {'-' * 40}"]
    for missed, valid, name, ranges in rows[:12]:
        report.append(f"  {missed:>6}  {valid:>5}  {name}")
        report.append(f"          lines: {ranges}")

    if valid_total:
        percent = 100.0 * covered_total / valid_total
        need = max(0, int((FLOOR / 100.0) * valid_total) + 1 - covered_total)
        report.append("")
        report.append(
            "  (statements only; the gate also counts branches, so it reads lower)"
        )
        report.append(
            f"  statement coverage {percent:.2f}% "
            f"({covered_total}/{valid_total}); "
            f"{need} more covered statements would clear {FLOOR}%"
        )
    return "\n".join(report)


def compress(numbers: list[int]) -> str:
    numbers = sorted(numbers)
    out: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        out.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    out.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(out[:20])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="leave MongoDB running")
    parser.add_argument("--no-up", action="store_true", help="MongoDB is already running")
    args = parser.parse_args()

    for tool in ("docker", "uv"):
        if shutil.which(tool) is None:
            say(f"{tool} is not on PATH.")
            return 2

    say(BAR)
    say("MongoDB CI job, locally")
    say(BAR)

    if not args.no_up:
        say("\n[1/4] starting the replica set")
        code, output = run(
            ["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "240", "mongo"], ROOT
        )
        if code != 0:
            say(tail(output, 20))
            return 1
        say("      up")

    # The job sets exactly one variable. Anything more leaks into every unit test.
    env = {**os.environ, "TELECOM_MW_MONGODB_URI": URI}

    say("\n[2/4] installing")
    code, output = run(["uv", "sync", "--frozen", "--all-extras"], MIDDLEWARE, env)
    if code != 0:
        say(tail(output, 20))
        return 1
    say("      done")

    say("\n[3/4] the 43 deselected tests, on their own")
    code, output = run(
        ["uv", "run", "pytest", "tests", "-m", "mongo", "--strict-markers", "-p", "no:randomly"],
        MIDDLEWARE,
        env,
    )
    mongo_ok = code == 0
    say(f"      {'PASS' if mongo_ok else 'FAIL'}")
    say(summarise_pytest(output))

    say("\n[4/4] the whole suite, with the adapter measured (make cov-mongo)")
    code, output = run(
        [
            "uv", "run", "pytest", "-m", "mongo or not mongo", "--cov",
            "--cov-config=coverage-mongo.toml", "--cov-report=term-missing",
            "--cov-report=xml", "-p", "no:randomly",
        ],
        MIDDLEWARE,
        env,
    )
    cov_ok = code == 0
    say(f"      {'PASS' if cov_ok else 'FAIL'}")
    say(summarise_pytest(output))

    say("\n" + BAR)
    say("COVERAGE GAP (paste everything from here down)")
    say(BAR)
    say(coverage_gap(MIDDLEWARE / "coverage.xml"))
    say(BAR)

    if not args.keep and not args.no_up:
        run(["docker", "compose", "down", "-v"], ROOT)
        say("\nreplica set stopped")

    return 0 if (mongo_ok and cov_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
