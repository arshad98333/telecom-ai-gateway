#!/usr/bin/env python
"""Run the MongoDB CI job locally and print a short report.

The `mongo` workflow needs a real replica set, so it cannot be reproduced anywhere that
has no MongoDB. This runs exactly what that job runs and prints about forty lines instead
of five thousand, so the result can be pasted back verbatim.

    python check-mongo-ci.py                     # starts MongoDB with docker compose
    python check-mongo-ci.py --uri "mongodb+srv://user:pass@cluster.mongodb.net/"

Docker is only needed for the first form. With --uri it runs against any replica set you
already have - Atlas, or a MongoDB service on this machine - and never touches Docker.

A standalone mongod will not do: these tests use transactions and change streams, and
both are replica-set features. Atlas M0 is a real three-node set, so it works.

    --uri URI  run against this connection string, and do not start anything
    --keep     leave MongoDB running afterwards
    --no-up    a replica set is already running on the default URI
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
DEFAULT_URI = "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=false"
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
    """The counts, the distinct reasons, and one example of each. Not every traceback.

    Fifty errors with the same cause are one fact. Printing the names without the
    reasons - which this did at first - is what turned a four-minute run into no
    information at all.
    """
    lines = output.splitlines()
    counts = [line for line in lines if re.search(r"\d+ (passed|failed|error|deselected)", line)]
    names = [line for line in lines if line.startswith(("FAILED", "ERROR "))]

    # pytest prefixes assertion and exception detail with "E   "
    reasons: dict[str, int] = {}
    for line in lines:
        if line.startswith("E   ") and line.strip() != "E":
            key = re.sub(r"0x[0-9a-f]+|\d{4,}", "N", line[4:].strip())[:160]
            reasons[key] = reasons.get(key, 0) + 1

    out: list[str] = []
    if counts:
        out.append("      " + counts[-1].strip())
    if reasons:
        out.append("      distinct reasons, most common first:")
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1])[:6]:
            out.append(f"        [{count:>3}x] {reason}")
    if names:
        out.append(f"      {len(names)} failing tests, first five:")
        out.extend("        " + name.split(" - ")[0] for name in names[:5])
    return "\n".join(out) or tail(output, 10)


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



PREFLIGHT = r"""
import os, sys, uuid
from pymongo import MongoClient

uri = os.environ["TELECOM_MW_MONGODB_URI"]
name = "telecom_preflight_" + uuid.uuid4().hex[:8]
ok = True

def check(label, fn):
    global ok
    try:
        fn()
        print("  ok    " + label)
    except Exception as exc:
        ok = False
        print("  FAIL  " + label)
        print("          " + type(exc).__name__ + ": " + str(exc)[:220])

client = MongoClient(uri, serverSelectionTimeoutMS=20000)
check("connects and answers a ping", lambda: client.admin.command("ping"))

info = {}
def topology():
    info.update(client.admin.command("hello"))
    if not info.get("setName"):
        raise RuntimeError("not a replica set; transactions and change streams need one")
check("is a replica set", topology)

db = client[name]
check("can create a collection in a NEW database", lambda: db.create_collection("probe"))
check("can write", lambda: db.probe.insert_one({"_id": 1}))

def transaction():
    with client.start_session() as session:
        with session.start_transaction():
            db.probe.insert_one({"_id": 2}, session=session)
check("can run a transaction", transaction)

def stream():
    with db.probe.watch(max_await_time_ms=2000):
        pass
check("can open a change stream", stream)

check("can drop the database again", lambda: client.drop_database(name))
client.close()
sys.exit(0 if ok else 1)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", help="connection string of a replica set to use as is")
    parser.add_argument("--keep", action="store_true", help="leave MongoDB running")
    parser.add_argument("--no-up", action="store_true", help="MongoDB is already running")
    args = parser.parse_args()

    uri = args.uri or DEFAULT_URI
    start_mongo = not args.no_up and not args.uri

    if shutil.which("uv") is None:
        say("uv is not on PATH. See https://docs.astral.sh/uv/getting-started/")
        return 2
    if start_mongo and shutil.which("docker") is None:
        say("docker is not on PATH, so there is nothing to start.")
        say("")
        say("Point this at a replica set you already have instead:")
        say('  python check-mongo-ci.py --uri "mongodb+srv://<user>:<password>@<cluster>/"')
        say("")
        say("Atlas works (M0 is a real three-node set). A standalone mongod does not:")
        say("these tests use transactions and change streams, and both need a set.")
        return 2

    say(BAR)
    say("MongoDB CI job, locally")
    say(BAR)

    if start_mongo:
        say("\n[1/5] starting the replica set")
        code, output = run(
            ["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "240", "mongo"], ROOT
        )
        if code != 0:
            say(tail(output, 20))
            return 1
        say("      up")

    # The job sets exactly one variable. Anything more leaks into every unit test.
    env = {**os.environ, "TELECOM_MW_MONGODB_URI": uri}

    say("\n[2/5] installing")
    code, output = run(["uv", "sync", "--frozen", "--all-extras"], MIDDLEWARE, env)
    if code != 0:
        say(tail(output, 20))
        return 1
    say("      done")

    say("\n[3/5] can this connection string do what the tests need?")
    code, output = run(["uv", "run", "python", "-c", PREFLIGHT], MIDDLEWARE, env)
    say(output.rstrip() or "(no output)")
    if code != 0:
        say("\n      The suite cannot pass until the above does. A credential scoped to one")
        say("      database cannot create the throwaway ones each test needs: in Atlas that")
        say("      is Database Access -> Edit -> Read and write to any database.")
        if start_mongo and not args.keep:
            run(["docker", "compose", "down", "-v"], ROOT)
        return 1

    say("\n[4/5] the 43 deselected tests, on their own")
    code, output = run(
        ["uv", "run", "pytest", "tests", "-m", "mongo", "--strict-markers", "-p", "no:randomly"],
        MIDDLEWARE,
        env,
    )
    mongo_ok = code == 0
    say(f"      {'PASS' if mongo_ok else 'FAIL'}")
    say(summarise_pytest(output))

    say("\n[5/5] the whole suite, with the adapter measured (make cov-mongo)")
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

    if start_mongo and not args.keep:
        run(["docker", "compose", "down", "-v"], ROOT)
        say("\nreplica set stopped")

    return 0 if (mongo_ok and cov_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
