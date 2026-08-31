# The delivery lifecycle

Three branches, one artifact, two gates.

```
   work                     automatic                approval
    │                           │                        │
 ┌──▼───┐   PR, ff    ┌─────────▼─┐   PR, ff    ┌────────▼───┐
 │development│ ──────────► │  staging  │ ──────────► │ production │
 └──┬───┘             └─────┬─────┘             └──────┬─────┘
    │ ci                    │ ci + BUILD               │ ci + PROMOTE
    │ (checks)              │ image@sha256:…           │ same digest
    │                       ▼                          ▼
    │                  STAGING env                PRODUCTION env
    │                  verify + posture           verify + posture
    │                                             rollback on failure
    │                                             alert rules applied
    └── every commit lands here
```

## Why it is shaped like this

**One working branch.** Per-person branches earn their keep when the merge order
between people is the hard problem. Here it is not, and the cost is a promotion graph
nobody can read at three in the morning. `development` is where work lands; `staging` and
`production` are not workstreams, they are *where the work has got to*.

**The branch is the deployment.** There is no "deploy" button whose relationship to git
you have to reconstruct later. What is on `production` is what is in production, and
`git log production` is the deployment history.

**Built once, on `staging`.** `cd-production.yml` contains no build step. It looks the
image up by commit sha and deploys that digest. If a commit never went through staging
there is no image tagged for it, the lookup fails, and the deployment does not happen —
so pushing straight to `production` produces a failed workflow rather than an untested
release.

**Fast-forward only.** CI refuses a promotion that needs a merge commit. A merge on the
way up means production runs a tree staging never ran, and every claim the pipeline
makes about "the same artifact" quietly stops being true.

## What runs where

| Trigger | Workflow | What happens |
|---|---|---|
| push or PR to any of the three | `ci` | lint, types, tests, coverage gate, generated-asset check, security scan, clean install, container smoke |
| PR into `staging` or `production` | `ci` / promotion job | refuses a wrong-direction or non-fast-forward promotion |
| PR touching `infra/**` | `cd-staging` / validate-infra | the Bicep and the generated alert rules must compile |
| push to `staging` | `cd-staging` | build, push `sha-<commit>`, deploy staging, verify readiness and posture |
| push to `production` | `cd-production` | resolve the digest, wait for approval, deploy, verify, roll back on failure, apply alert rules |
| tag `v*.*.*` | `release` | tag must be on `production`; publish to TestPyPI, verify, publish the image and the package |
| Monday 06:17 UTC | `scheduled` | uncached build of `production` against a fresh index, to catch the outside world breaking us |

## The day-to-day loop

```bash
git switch development
# ... work ...
make check                       # exactly what CI runs
git commit && git push origin development

./scripts/promote.sh development staging       # opens the PR; merge when green
./scripts/promote.sh staging production   # opens the PR; merge, then approve
```

## The two gates

**Staging is not a gate.** It deploys automatically. Nobody waits for a person there,
because a staging environment that needs an approval is a staging environment that goes
stale.

**Production is a gate**, and it is a GitHub Environment reviewer rather than a process
document. What the reviewer approves is a specific digest that staging has been
running, which is a question a person can actually answer.

## When it goes wrong

The production deployment verifies readiness *and* posture, and returns traffic to the
previous revision if either fails. The rollback target is captured before the
deployment starts, because reading it afterwards means reading it from a system already
in the state you are trying to escape.

After a rollback: fix on `development`, promote normally. Do not deploy forward. See
`docs/runbook-rollback.md` for the manual path and `docs/runbook-alerts.md` for the
three alerts that page.
