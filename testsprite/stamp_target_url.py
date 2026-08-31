#!/usr/bin/env python
"""Write the real target URLs into the tests, ready to upload.

TestSprite's V3 backend runner does not inject a base URL - a test that reads one fails
automated validation before it makes a single request, and the failure bundle says so in
as many words. The target has to be a literal in the code.

That makes the URL part of the test rather than part of the run, so rotating a tunnel
means restamping and re-uploading all eighteen. This does that in one command, and it
writes to build/ rather than editing the sources, so the sources keep their local
defaults and stay runnable on a laptop.

    python stamp_target_url.py https://<mcp-host> https://<middleware-host>
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
BUILD = HERE / "build"
PATTERN = re.compile(r'^BASE_URL = ".*"$', re.MULTILINE)


def stamp(source: pathlib.Path, target: pathlib.Path, url: str) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        stamped, n = PATTERN.subn(f'BASE_URL = "{url}"', text, count=1)
        if n != 1:
            print(f"  !! {path.name}: no BASE_URL line to stamp", file=sys.stderr)
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
        print(f"stamped {count:>2} {label} tests -> {url}")
    print(f"\nupload from: {BUILD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
