#!/usr/bin/env python
"""Resolve TARGET_URL to a literal for a TestSprite upload, and only for that.

The tests take their target from the environment:

    BASE_URL = os.environ.get("TARGET_URL", "http://127.0.0.1:9100").rstrip("/")

That is how they run on a laptop, in CI, and under validate_locally.py - set
TARGET_URL, or set nothing and get the local dev server. Nothing in that path rewrites
a source file.

TestSprite's V3 backend sandbox is the one exception. It validates an uploaded file
before it makes a single request, and rejects one whose base URL is not a literal - the
failure bundle says so in as many words. So for that upload, and no other purpose, this
resolves the expression above against the URL you give it and writes the result to
build/. The sources are never edited.

    python stamp_target_url.py https://<mcp-host> https://<middleware-host>

Upload from build/. Rotating a tunnel means re-running this and re-uploading; every
other runner just needs a different TARGET_URL.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"

#: Matches the environment-driven assignment the sources carry, and the plain literal a
#: previously stamped file carries, so re-stamping a build directory is idempotent.
PATTERN = re.compile(r"^BASE_URL = (?:os\.environ\.get\(.*\)|\".*\").*$", re.MULTILINE)


def stamp(source: pathlib.Path, target: pathlib.Path, url: str) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        stamped, n = PATTERN.subn(f'BASE_URL = "{url}"', text, count=1)
        if n != 1:
            print(f"  !! {path.name}: no BASE_URL line to resolve", file=sys.stderr)
            continue
        (target / path.name).write_text(stamped, encoding="utf-8")
        count += 1
    return count


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    mcp_url, middleware_url = (u.rstrip("/") for u in sys.argv[1:3])
    for url in (mcp_url, middleware_url):
        if not url.startswith("https://"):
            # The runner calls from the public internet. An http:// or localhost target
            # cannot work, and finding that out from a failed run costs credits.
            print(f"refusing to stamp a non-https target: {url}", file=sys.stderr)
            return 1

    if BUILD.exists():
        shutil.rmtree(BUILD)
    suites = (
        ("mcp", mcp_url, "tool-server endpoint"),
        ("mcp_integration", mcp_url, "tool-server integration"),
        ("middleware", middleware_url, "middleware endpoint"),
        ("middleware_integration", middleware_url, "middleware integration"),
    )
    for name, url, label in suites:
        count = stamp(HERE / "tests" / name, BUILD / name, url)
        print(f"resolved {count:>2} {label} tests -> {url}")
    print(f"\nupload from: {BUILD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
