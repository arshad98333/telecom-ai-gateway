#!/usr/bin/env python3
"""Prove a MongoDB deployment is reachable and usable, before anything else runs.

This is the first thing to run after filling in ``.env``. It depends on nothing but
``pymongo``, so it works in a bare ``python -m pip install pymongo`` environment as
well as inside the project's ``uv`` venv -- which matters, because the usual reason
you reach for it is that the project itself will not start.

    uv run --env-file .env python scripts/check_mongo.py     # Atlas, from .env
    python scripts/check_mongo.py --local                    # docker compose mongo
    python scripts/check_mongo.py --uri "mongodb+srv://..."  # anything else

It answers four questions in order, and stops at the first `no`:

1. Does the URI parse, and does its hostname resolve?
2. Does the server answer a ping within the timeout?
3. Is it a replica set? Standalone mongod has no transactions and no change streams,
   and this system uses both -- see docs/decisions/0001-mongodb-replica-set-required.md.
4. Can the credential actually read and write the target database?

Failures are reported as the thing that is wrong and the thing to do about it, not as
a driver traceback. The four errors that account for nearly every real failure -- a
password that was never substituted, an un-encoded character in a password, an IP that
is not on the Atlas allow-list, and a paused free cluster -- are each recognised by
name.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

LOCAL_URI = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=false"
ENV_URI = "TELECOM_MW_MONGODB_URI"
ENV_DB = "TELECOM_MW_MONGODB_DATABASE"
PROBE_COLLECTION = "_connection_probe"


def say(message: str = "") -> None:
    print(message)  # noqa: T201 - a script, and this is its whole output


def fail(headline: str, *remedies: str) -> int:
    """Report a diagnosis and what to do about it, then hand back an exit code."""
    say()
    say(f"  FAILED  {headline}")
    for remedy in remedies:
        say(f"          {remedy}")
    say()
    return 1


def redact(uri: str) -> str:
    """A URI safe to print: username kept, password replaced."""
    parts = urlsplit(uri)
    if parts.password is None:
        return uri
    return uri.replace(f":{parts.password}@", ":****@", 1)


def resolve_uri(args: argparse.Namespace) -> str | None:
    """Work out which URI to test, in the order a person would expect."""
    if args.local:
        return LOCAL_URI
    if args.uri:
        return args.uri
    return os.environ.get(ENV_URI)


def check_placeholder(uri: str) -> str | None:
    """Catch the connection string that was copied from Atlas but never finished."""
    if "<db_password>" not in uri and "<password>" not in uri:
        return None
    return "The connection string still contains the literal <db_password> placeholder."


def check_dns(uri: str, timeout: float) -> tuple[str | None, str]:
    """Resolve the host before the driver does, so DNS problems say so plainly.

    A ``mongodb+srv://`` hostname usually has no A record at all -- Atlas publishes
    only the SRV and TXT records -- so looking it up with ``getaddrinfo`` reports a
    working cluster as missing. The two schemes need two different lookups.

    Returns ``(problem, note)``; ``problem`` is None when the name resolves.
    """
    parts = urlsplit(uri)
    host = parts.hostname
    if host is None:
        return "The connection string has no hostname.", ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return None, f"hostname {host}"

    if not uri.startswith("mongodb+srv://"):
        socket.setdefaulttimeout(timeout)
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            return f"The hostname {host} does not resolve.", ""
        return None, f"hostname {host} resolves"

    record = f"_mongodb._tcp.{host}"
    try:
        import dns.resolver
    except ImportError:
        return None, "SRV lookup skipped (dnspython not installed)"
    try:
        answer = dns.resolver.resolve(record, "SRV", lifetime=max(timeout, 5.0))
    except Exception as error:  # noqa: BLE001 - any resolver failure means the same thing
        return (
            f"The SRV record {record} does not resolve ({type(error).__name__}).",
            "",
        )
    hosts = sorted(str(item.target).rstrip(".") for item in answer)
    return None, f"SRV record resolves to {len(hosts)} node(s): {', '.join(hosts)}"


def describe_topology(info: dict[str, Any]) -> tuple[str, bool]:
    """Name the deployment shape, and say whether it supports transactions."""
    if info.get("msg") == "isdbgrid":
        return "sharded cluster (mongos)", True
    replica_set = info.get("setName")
    if replica_set:
        role = "primary" if info.get("ismaster") or info.get("isWritablePrimary") else "secondary"
        return f"replica set {replica_set!r}, connected to the {role}", True
    return "standalone mongod -- no replica set", False


def probe_write(client: MongoClient[dict[str, Any]], database: str) -> str | None:
    """Insert and remove one document, because read permission is not write permission."""
    collection = client[database][PROBE_COLLECTION]
    try:
        result = collection.insert_one({"probe": True, "at": time.time()})
        collection.delete_one({"_id": result.inserted_id})
    except OperationFailure as error:
        return error.details.get("errmsg", str(error)) if error.details else str(error)
    return None


def diagnose_timeout(uri: str, error: ServerSelectionTimeoutError) -> int:
    """Server selection timed out. On Atlas this is almost always one of three things."""
    detail = str(error)
    if uri.startswith("mongodb+srv://"):
        return fail(
            "No server answered within the timeout.",
            "1. Network Access in Atlas may not list this machine's IP. Add it, or",
            "   add 0.0.0.0/0 for a development cluster, and wait about a minute.",
            "2. A free (M0) cluster pauses after 30 days idle. Resume it in Atlas.",
            "3. A corporate network or VPN may block outbound 27017.",
            f"   driver said: {detail[:200]}",
        )
    return fail(
        "No server answered within the timeout.",
        "Is MongoDB running? From the repo root:  docker compose up -d mongo",
        "Then give it a few seconds to elect a primary and try again.",
        f"driver said: {detail[:200]}",
    )


def diagnose_auth(error: OperationFailure) -> int:
    """Auth failures on Atlas blame the username even when the password is at fault."""
    return fail(
        "Authentication was refused.",
        "The username or password is wrong -- and note that an un-encoded special",
        "character in the password produces exactly this error while looking fine:",
        "   @ -> %40    : -> %3A    / -> %2F    ? -> %3F    # -> %23    % -> %25",
        'Encode one with:  python -c "from urllib.parse import quote_plus; '
        'print(quote_plus(input()))"',
        "Or, in Atlas: Database Access -> Edit -> Autogenerate Secure Password,",
        "which produces a password with no characters needing encoding.",
        f"server said: {error.details.get('errmsg', error) if error.details else error}",
    )


def run(args: argparse.Namespace) -> int:
    uri = resolve_uri(args)
    if not uri:
        return fail(
            f"No connection string. Set {ENV_URI}, or pass --uri, or use --local.",
            "Inside the project, the .env file supplies it:",
            "   uv run --env-file .env python scripts/check_mongo.py",
        )

    database = args.database or os.environ.get(ENV_DB, "telecom")

    say()
    say(f"  uri       {redact(uri)}")
    say(f"  database  {database}")
    say()

    placeholder = check_placeholder(uri)
    if placeholder:
        return fail(
            placeholder,
            "Replace <db_password>, angle brackets included, with the real password",
            "from Atlas: Database Access -> the user -> Edit -> Reset password.",
            "Percent-encode it first if it contains @ : / ? # % -- see --help.",
        )

    dns_problem, dns_note = check_dns(uri, args.timeout / 1000)
    if dns_problem:
        return fail(
            dns_problem,
            "Check the cluster hostname against Atlas: Clusters -> Connect -> Drivers.",
            "mongodb+srv:// needs DNS SRV lookups, which some VPNs and public",
            "resolvers drop. If so, use the non-SRV mongodb:// string Atlas also offers.",
        )
    say(f"  ok  {dns_note}")

    client: MongoClient[dict[str, Any]] = MongoClient(
        uri,
        serverSelectionTimeoutMS=args.timeout,
        connectTimeoutMS=args.timeout,
        appname="check_mongo",
    )

    try:
        started = time.perf_counter()
        info = client.admin.command("hello")
        elapsed_ms = (time.perf_counter() - started) * 1000
    except ServerSelectionTimeoutError as error:
        return diagnose_timeout(uri, error)
    except OperationFailure as error:
        return diagnose_auth(error)
    except ConfigurationError as error:
        return fail(f"The connection string is malformed: {error}")

    build = client.admin.command("buildInfo")
    say(f"  ok  ping answered in {elapsed_ms:.0f} ms")
    say(f"  ok  MongoDB {build['version']}")

    topology, supports_transactions = describe_topology(info)
    if supports_transactions:
        say(f"  ok  {topology}")
        say("  ok  transactions and change streams available")
    else:
        client.close()
        return fail(
            f"Connected, but this is a {topology}.",
            "The transactional outbox and the supervisor's live feed both need a",
            "replica set. A standalone appears to work until the first write that",
            "has to commit atomically -- see docs/decisions/0001.",
            "Locally:  docker compose up -d mongo   (it initiates rs0 for you)",
            "On Atlas: every cluster including M0 is already a replica set, so",
            "seeing this against Atlas means the URI points somewhere else.",
        )

    write_problem = probe_write(client, database)
    if write_problem:
        client.close()
        return fail(
            f"The credential cannot write to {database!r}.",
            "In Atlas: Database Access -> the user -> Edit -> Specific Privileges,",
            f"then grant readWrite on the database {database!r}.",
            f"server said: {write_problem}",
        )
    say(f"  ok  read and write on {database!r}")

    names = client[database].list_collection_names()
    if names:
        say()
        say(f"  {len(names)} collection(s) in {database!r}:")
        for name in sorted(names):
            count = client[database][name].estimated_document_count()
            say(f"    {name:<28} ~{count} document(s)")
    else:
        say()
        say(f"  {database!r} is empty. Create the schema and load the demo data:")
        say("    uv run --env-file .env telecom-middleware migrate")
        say("    uv run --env-file .env telecom-middleware seed")

    client.close()
    say()
    say("  Connected successfully.")
    say()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that MongoDB is reachable, is a replica set, and is writable.",
        epilog=(
            "To percent-encode a password: "
            'python -c "from urllib.parse import quote_plus; print(quote_plus(input()))"'
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--uri", help="connection string to test; overrides the environment")
    source.add_argument(
        "--local",
        action="store_true",
        help=f"test the docker compose deployment at {LOCAL_URI}",
    )
    parser.add_argument("--database", help=f"database to probe (default: ${ENV_DB} or 'telecom')")
    parser.add_argument(
        "--timeout",
        type=int,
        default=10000,
        help="server selection timeout in milliseconds (default: 10000)",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
