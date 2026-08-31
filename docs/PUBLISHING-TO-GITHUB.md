# Publishing to GitHub — 56 commands, in order

Everything below runs on your machine, against `github.com/arshad98333`. The commits
and branches already exist locally; nothing here rewrites history. Run the blocks in
order — later steps assume earlier ones ran.

Three repositories, each with the same four branches:

```
development ──PR──► staging ──PR──► production ──tag──► release
     │                                    │
     └──────────── work lands here        └── main tracks this, so a visitor
                                              sees what is actually running
```

`development` is where work lands. `staging` and `production` are where it has got to.
`main` is the default branch a stranger sees first, kept level with `production`.

Before you start: `gh auth status`. If it is not authenticated, `gh auth login` and
choose HTTPS. Everything below assumes `gh` is signed in as `arshad98333`.

---

## Part 1 — Confirm what you are about to publish (1–6)

Nothing is pushed yet. Look before you leap; a public repository is public immediately.

```bash
cd "C:\Users\HI\Desktop\ai agent"
```

**1.** Every branch, every repository, in one view.

```bash
for r in telecom-mcp telecom-middleware .; do echo "== $r"; git -C "$r" for-each-ref --format='   %(refname:short) %(objectname:short)' refs/heads/; done
```

**2.** The commits you are about to make public in the tool server.

```bash
git -C telecom-mcp log --oneline -6
```

**3.** The same for the middleware.

```bash
git -C telecom-middleware log --oneline -7
```

**4.** And the workspace.

```bash
git log --oneline -8
```

**5.** Prove no environment file is tracked anywhere. This must print nothing but
`none` three times.

```bash
for r in telecom-mcp telecom-middleware .; do echo "-- $r"; git -C "$r" ls-files | grep -iE '(^|/)\.env($|\.)|\.tfvars$|dev\.backend$' | grep -v '\.example$' || echo "  none"; done
```

**6.** Prove the working trees are clean, so what you push is what you tested.

```bash
for r in telecom-mcp telecom-middleware .; do echo "-- $r"; git -C "$r" status --short; done
```

---

## Part 2 — Create the three repositories (7–12)

Private first. You can flip any of them public later in one command; you cannot un-see
a repository that was public for ten minutes.

**7.** The tool server.

```bash
gh repo create arshad98333/telecom-mcp-tools --private --description "Security-enforcing MCP tool server for telecom customer support agents"
```

**8.** The backing API.

```bash
gh repo create arshad98333/telecom-middleware --private --description "Telecom customer data and approval service: the only writer to MongoDB"
```

**9.** The workspace that holds both, the infrastructure and the test harness.

```bash
gh repo create arshad98333/telecom-platform --private --description "Workspace: system design, Auth0 tenant, end-to-end suite and the TestSprite harness"
```

**10.** Point the tool server at its remote.

```bash
git -C telecom-mcp remote add origin https://github.com/arshad98333/telecom-mcp-tools.git
```

**11.** The middleware.

```bash
git -C telecom-middleware remote add origin https://github.com/arshad98333/telecom-middleware.git
```

**12.** The workspace.

```bash
git remote add origin https://github.com/arshad98333/telecom-platform.git
```

---

## Part 3 — Push every branch (13–25)

Push `development` first in each repository. It is the branch the others are
fast-forwards of, so pushing it first means every later push is trivially accepted.

**13.**

```bash
git -C telecom-mcp push -u origin development
```

**14.**

```bash
git -C telecom-mcp push origin staging
```

**15.**

```bash
git -C telecom-mcp push origin production
```

**16.**

```bash
git -C telecom-mcp push origin main
```

**17.**

```bash
git -C telecom-middleware push -u origin development
```

**18.**

```bash
git -C telecom-middleware push origin staging
```

**19.**

```bash
git -C telecom-middleware push origin production
```

**20.**

```bash
git -C telecom-middleware push origin main
```

**21.**

```bash
git push -u origin development
```

**22.**

```bash
git push origin staging
```

**23.**

```bash
git push origin production
```

**24.**

```bash
git push origin main
```

**25.** Confirm all twelve branches arrived.

```bash
for r in telecom-mcp-tools telecom-middleware telecom-platform; do echo "== $r"; gh api "repos/arshad98333/$r/branches" --jq '.[].name'; done
```

---

## Part 4 — Make the repositories behave like production repositories (26–37)

**26.** `main` is the default branch, so the first thing a reader sees is what is
running, not what is half-finished.

```bash
gh repo edit arshad98333/telecom-mcp-tools --default-branch main
```

**27.**

```bash
gh repo edit arshad98333/telecom-middleware --default-branch main
```

**28.**

```bash
gh repo edit arshad98333/telecom-platform --default-branch main
```

**29.** Merge commits off, squash off, rebase off — except the one you want. Linear
history is what makes `git log` on `production` a readable release record.

```bash
gh repo edit arshad98333/telecom-mcp-tools --enable-merge-commit=false --enable-squash-merge=true --enable-rebase-merge=false --delete-branch-on-merge
```

**30.**

```bash
gh repo edit arshad98333/telecom-middleware --enable-merge-commit=false --enable-squash-merge=true --enable-rebase-merge=false --delete-branch-on-merge
```

**31.**

```bash
gh repo edit arshad98333/telecom-platform --enable-merge-commit=false --enable-squash-merge=true --enable-rebase-merge=false --delete-branch-on-merge
```

**32.** Protect `production` on the tool server: no force pushes, no deletion, a review
required. This is the branch tags are cut from, so it is the one that matters most.

```bash
gh api -X PUT repos/arshad98333/telecom-mcp-tools/branches/production/protection --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": []},
  "enforce_admins": true,
  "required_pull_request_reviews": {"required_approving_review_count": 1},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON
```

**33.** Protect `staging` the same way, minus the review — staging exists to be
promoted into quickly.

```bash
gh api -X PUT repos/arshad98333/telecom-mcp-tools/branches/staging/protection --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": []},
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON
```

**34.** And `main`, which should only ever move by promotion.

```bash
gh api -X PUT repos/arshad98333/telecom-mcp-tools/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON
```

**35.** The same three protections on the middleware.

```bash
for b in production staging main; do gh api -X PUT "repos/arshad98333/telecom-middleware/branches/$b/protection" --input - <<'JSON'
{"required_status_checks":{"strict":true,"contexts":[]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false,"required_linear_history":true}
JSON
done
```

**36.** And on the workspace.

```bash
for b in production staging main; do gh api -X PUT "repos/arshad98333/telecom-platform/branches/$b/protection" --input - <<'JSON'
{"required_status_checks":{"strict":true,"contexts":[]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false,"required_linear_history":true}
JSON
done
```

**37.** Read the protections back. Trusting an API call you did not verify is how a
branch ends up unprotected for a month.

```bash
for b in main staging production; do echo "== $b"; gh api "repos/arshad98333/telecom-mcp-tools/branches/$b/protection" --jq '{force_push: .allow_force_pushes.enabled, deletion: .allow_deletions.enabled, linear: .required_linear_history.enabled}'; done
```

---

## Part 5 — The promotion path, exercised once (38–45)

The branches are already level, so these PRs will be empty. Open them anyway once, on
the workspace repository, to prove the path works before you need it at three in the
morning. Skip to Part 6 if you would rather wait for real work.

**38.** A throwaway change on `development`.

```bash
git switch development && echo "" >> README.md && git commit -aqm "docs: no-op, proving the promotion path" && git push origin development
```

**39.** Open the first promotion.

```bash
gh pr create --repo arshad98333/telecom-platform --base staging --head development --title "Promote development to staging" --body "Routine promotion. No behaviour change."
```

**40.** Watch the checks, if any are configured.

```bash
gh pr checks --repo arshad98333/telecom-platform --watch || true
```

**41.** Merge it.

```bash
gh pr merge --repo arshad98333/telecom-platform --squash --admin
```

**42.** Promote staging to production.

```bash
gh pr create --repo arshad98333/telecom-platform --base production --head staging --title "Promote staging to production" --body "The commits staging ran, unchanged."
```

**43.** Merge that too.

```bash
gh pr merge --repo arshad98333/telecom-platform --squash --admin
```

**44.** Bring `main` level with production.

```bash
git fetch origin && git switch main && git merge --ff-only origin/production && git push origin main
```

**45.** Back to where work happens.

```bash
git switch development && git pull --ff-only
```

---

## Part 6 — Release telecom-mcp-tools 1.1.0 (46–52)

The tag already exists locally on `production`. Do the PyPI setup first — the workflow
fails at the last step without it, after doing everything else correctly.

**46.** Read the runbook and complete the pending-publisher forms on pypi.org and
test.pypi.org. This is the one step no command can do for you.

```bash
cat telecom-mcp/docs/RELEASING.md
```

**47.** Create the two deployment environments the workflow publishes from.

```bash
gh api -X PUT repos/arshad98333/telecom-mcp-tools/environments/testpypi && gh api -X PUT repos/arshad98333/telecom-mcp-tools/environments/pypi
```

**48.** Require your approval on the `pypi` environment. PyPI does not allow re-uploading
a version, ever, so this is the last point at which a mistake is still cheap.

```bash
gh api -X PUT repos/arshad98333/telecom-mcp-tools/environments/pypi --input - <<'JSON'
{"reviewers":[{"type":"User","id":0}],"deployment_branch_policy":null}
JSON
```

> Replace the `0` with your numeric user id, which `gh api user --jq .id` prints. The
> call is rejected with a validation error otherwise, which is the safe failure.

**49.** Confirm the tag is on `production` and matches the packaged version. The
workflow checks both and refuses otherwise; checking here costs nothing.

```bash
git -C telecom-mcp tag --points-at production && grep '^version' telecom-mcp/pyproject.toml
```

**50.** Push the tag. This is what starts the release.

```bash
git -C telecom-mcp push origin v1.1.0
```

**51.** Watch it.

```bash
gh run watch --repo arshad98333/telecom-mcp-tools
```

**52.** When it pauses for the `pypi` environment, approve it in the run's page — or
from here.

```bash
gh api repos/arshad98333/telecom-mcp-tools/actions/runs --jq '.workflow_runs[0].html_url'
```

---

## Part 7 — Make them public, once you are happy (53–56)

**53.** Confirm one more time that nothing secret is tracked. Yes, again. This is the
irreversible step.

```bash
for r in telecom-mcp telecom-middleware .; do git -C "$r" ls-files | grep -iE '(^|/)\.env($|\.)|\.tfvars$|dev\.backend$' | grep -v '\.example$'; done; echo "nothing above means clean"
```

**54.**

```bash
gh repo edit arshad98333/telecom-mcp-tools --visibility public --accept-visibility-change-consequences
```

**55.**

```bash
gh repo edit arshad98333/telecom-middleware --visibility public --accept-visibility-change-consequences
```

**56.**

```bash
gh repo edit arshad98333/telecom-platform --visibility public --accept-visibility-change-consequences
```

---

## From here on

Day-to-day work is four commands, not fifty-six:

```bash
git switch development
# ... work ...
git commit -am "..." && git push origin development
gh pr create --base staging --head development --fill
```

The tool server's own `scripts/promote.sh development staging` does the last step and
checks the promotion is a fast-forward first, which is the property that makes
`production` a record of what actually shipped rather than a branch that happens to
have the same name.

## If you need to undo something

- **Pushed to the wrong repository.** Delete it: `gh repo delete <name> --yes`. Nothing
  else you did depends on it.
- **A secret reached a public repository.** Rotate the credential first, before removing
  it from history. Assume it was scraped within the minute — that is the realistic
  assumption, not a pessimistic one. Then rewrite with `git filter-repo` and force-push,
  which requires temporarily lifting the branch protection you set in Part 4.
- **A bad version reached PyPI.** It cannot be replaced. Yank it and release a fix as a
  new version; `docs/RELEASING.md` has the detail.
