# Releasing telecom-mcp-tools

The package is published to PyPI by `.github/workflows/release.yml`, triggered by a
`v*.*.*` tag. There is no API token anywhere in this repository, and there should never
be one: PyPI authenticates the workflow itself through OpenID Connect.

## One-time setup on PyPI

Do this once, before the first release. The project does not exist on the index yet, so
use the **pending publisher** form — it creates the project on first upload.

1. Sign in to <https://pypi.org> and open **Your projects → Publishing → Add a pending
   publisher** (<https://pypi.org/manage/account/publishing/>).
2. Fill it in exactly:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `telecom-mcp-tools` |
   | Owner | `arshad98333` |
   | Repository name | `telecom-mcp-tools` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. Repeat on <https://test.pypi.org> with the environment name `testpypi`. The workflow
   publishes there first and installs from it before it touches the real index.
4. In the GitHub repository, create both environments under **Settings → Environments**.
   Add a required reviewer to `pypi` only. That approval is the last human gate before
   a version becomes permanent — PyPI does not allow re-uploading a version, ever.

The environment names in the two forms and in the workflow must match character for
character. A mismatch fails at the upload step with a message about the trusted
publisher not being configured, which reads like a PyPI outage and is not.

## Cutting a release

```bash
# 1. The version, in one place. Everything else reads it from the metadata.
#    Edit pyproject.toml: version = "1.2.0"

# 2. The changelog must describe it. CI greps for the exact version string.
#    Move the Unreleased entries under a new ## [1.2.0] - YYYY-MM-DD heading.

# 3. Prove it locally before the tag exists.
make check
rm -rf dist && uv build
uvx twine check --strict dist/*

# 4. Merge to production. The workflow refuses a tag that does not point at a commit
#    on that branch, because a published version nobody is running is worse than none.

# 5. Tag and push.
git tag -a v1.2.0 -m "telecom-mcp-tools 1.2.0"
git push origin v1.2.0
```

Then watch the run. It goes: build → artifact safety gate → TestPyPI → install from
TestPyPI into an empty environment and run the console script → container image with
signed provenance → **wait for your approval** → PyPI.

## What the workflow checks that you cannot easily check by hand

- The tagged commit is an ancestor of `production`.
- The tag matches the packaged version.
- `CHANGELOG.md` contains that version.
- `make check` — ruff, mypy strict, the full suite, and the 95% coverage floor.
- The built artifacts contain no `.env` file and no private key. This runs against the
  archives themselves, not against the build configuration, because the configuration
  is what would be wrong.
- `twine check --strict` — the README renders on the index, and the metadata is valid
  for it.
- The wheel installs into an empty interpreter and `telecom-mcp check-config` runs.

## If something goes wrong

**A version is already on PyPI and is broken.** You cannot replace it. Yank it
(**Manage → Releases → Options → Yank**), which leaves it installable by exact pin for
anyone already depending on it but removes it from resolution, then release a fix as a
new version. Deleting a release frees nothing: the version number stays burnt.

**TestPyPI succeeded and PyPI failed.** The artifact is the same one; the difference is
almost always the trusted-publisher configuration on the real index, or the `pypi`
environment not existing. Re-running the job is safe — nothing was uploaded.

**The install-from-TestPyPI step cannot find the version.** The index takes a moment.
The step already retries five times; if it still fails, TestPyPI rejected the upload,
and the upload job's log says why.

## Version numbers

Semantic versioning, and the tool contract is the thing being versioned. A change to
what a tool accepts or returns is a major version and a `TOOL_CONTRACT_VERSION` bump —
agents are built against that shape and cannot negotiate. A change to an error path, a
new optional argument, or a new tool is a minor. Fixes are patches.
