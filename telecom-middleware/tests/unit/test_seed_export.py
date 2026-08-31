"""The generated mongosh seed must stay identical to what the code produces.

There are two ways to load the demo dataset: the Python seeder and the mongosh script.
The script is generated from the seeder, and this asserts the committed copy is still
the one the current code produces - otherwise the two paths drift and a database built
one way stops matching a database built the other.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
GENERATED = REPOSITORY / "scripts" / "seed.mongodb.js"
GENERATOR = REPOSITORY / "scripts" / "export_seed.py"


@pytest.fixture(scope="module")
def generated() -> str:
    return GENERATED.read_text(encoding="utf-8")


def test_the_committed_script_is_what_the_generator_produces(tmp_path: Path) -> None:
    regenerated = tmp_path / "seed.mongodb.js"

    subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(GENERATOR), "--out", str(regenerated)],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )

    assert regenerated.read_text(encoding="utf-8") == GENERATED.read_text(encoding="utf-8"), (
        "scripts/seed.mongodb.js is out of date; run: uv run python scripts/export_seed.py"
    )


def test_every_declared_collection_is_created_by_the_script(generated: str) -> None:
    from telecom_middleware.repositories.schema import ALL_COLLECTIONS

    for spec in ALL_COLLECTIONS:
        assert f'db.createCollection("{spec.name}"' in generated, spec.name


def test_every_declared_index_is_created_by_the_script(generated: str) -> None:
    from telecom_middleware.repositories.schema import ALL_COLLECTIONS

    for spec in ALL_COLLECTIONS:
        for index in spec.indexes:
            assert f'name: "{index.document["name"]}"' in generated


def test_collection_validators_are_applied_so_the_script_is_not_the_weaker_path(
    generated: str,
) -> None:
    from telecom_middleware.repositories.schema import ALL_COLLECTIONS

    for spec in ALL_COLLECTIONS:
        if spec.validator is not None:
            assert f'collMod: "{spec.name}"' in generated


def test_money_is_written_as_a_64_bit_integer_never_a_double(generated: str) -> None:
    # A double here is a rounding bug waiting for a large invoice.
    assert "NumberLong(6300)" in generated
    assert not re.search(r'"(total|outstanding|monthly_price|amount)_minor":\s*\d+\.\d', generated)


def test_dates_are_written_as_dates_never_as_text(generated: str) -> None:
    # A date stored as a string sorts wrong and cannot be compared against a real date.
    assert 'ISODate("2026-08-30T12:00:00Z")' in generated
    assert not re.search(r'"(created_at|issued_on|placed_at)":\s*"2026', generated)


def test_the_passcode_is_a_hash_and_never_the_passcode_itself(generated: str) -> None:
    from telecom_middleware.services.seed import DEMO_PASSCODE

    assert "$argon2id$" in generated
    # The demo passcode is named in a comment on purpose, but never as a stored value.
    assert f'"{DEMO_PASSCODE}"' not in generated


def test_the_script_is_safe_to_run_twice(generated: str) -> None:
    # Every write is an upsert keyed the way the unique index is, so a second run
    # updates rather than failing on a duplicate key.
    assert "insertOne" not in generated
    assert generated.count("{ upsert: true }") == generated.count("replaceOne")


def test_the_generated_file_says_it_is_generated(generated: str) -> None:
    assert generated.startswith("// GENERATED FILE - do not edit by hand.")
    assert "scripts/export_seed.py" in generated
