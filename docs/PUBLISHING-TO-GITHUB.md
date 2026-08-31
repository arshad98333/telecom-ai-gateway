# Publishing

One repository, one push. The two services are subtrees inside it with their history
intact, so `git log -- telecom-mcp` is still the real story of that service.

## The first push

```bash
git remote add origin https://github.com/arshad98333/telecom-ai-gateway.git
git push -u origin development
git push origin main staging production
```

`gh auth login` first if git has no credentials. If `main` on the remote already has a
commit (an auto-created README, say), add `--force-with-lease` to that second push —
this repository's `main` is the one that should survive.

## After that

Work lands on `development`. `staging` and `production` are where it has got to, and
`main` tracks `production` so a visitor sees what is actually running.

```bash
git push origin development                       # normal work
gh pr create --base staging --head development    # promote to staging
gh pr create --base production --head staging     # promote to production
git push origin production:main                   # keep main level with production
```

CI runs on all four branches. Protect `staging` and `production` and require the `ci`
checks before merging; the promotion rules are in `.github/workflows/ci.yml`.

## Pushing a service back to its own repository

The subtrees are still separable:

```bash
git subtree push --prefix telecom-mcp git@github.com:arshad98333/telecom-mcp-tools.git main
```

## Before you make it public

```bash
git ls-files | grep -iE '(^|/)\.env($|\.)|\.tfvars$' | grep -v '\.example$'   # must print nothing
git status --short                                                            # must be clean
```

The `security` job in CI scans the whole history for secrets on every push, but a
public repository is public immediately — check before, not after.
