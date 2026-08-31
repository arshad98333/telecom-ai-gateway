#!/usr/bin/env bash
# Promote one branch to the next, as a fast-forward, through a pull request.
#
#   ./scripts/promote.sh Arshad staging
#   ./scripts/promote.sh staging production
#
# A fast-forward is the whole point. If the promotion needs a merge commit, production
# would run a tree that staging never ran, and every claim the pipeline makes about
# "the same artifact" quietly stops being true. This script refuses rather than
# resolving, and tells you what to do about it.
set -euo pipefail

from="${1:?usage: promote.sh <from-branch> <to-branch>}"
to="${2:?usage: promote.sh <from-branch> <to-branch>}"

case "$from:$to" in
  Arshad:staging|staging:production) ;;
  *)
    echo "error: the only promotions are Arshad -> staging and staging -> production" >&2
    exit 2
    ;;
esac

echo "fetching"
git fetch --prune origin "$from" "$to"

if git rev-parse --quiet --verify "origin/$to" > /dev/null && \
   ! git merge-base --is-ancestor "origin/$to" "origin/$from"; then
  echo "error: origin/$to has commits that origin/$from does not." >&2
  echo "       This promotion would need a merge commit, which means $to would run a" >&2
  echo "       tree $from never ran." >&2
  echo "       Bring them back in line first:  git checkout $from && git merge --ff-only origin/$to" >&2
  exit 1
fi

ahead="$(git rev-list --count "origin/$to..origin/$from")"
if [ "$ahead" -eq 0 ]; then
  echo "$to is already up to date with $from; nothing to promote"
  exit 0
fi

echo
echo "$ahead commit(s) to promote from $from to $to:"
git --no-pager log --oneline --no-decorate "origin/$to..origin/$from"
echo

if ! command -v gh > /dev/null; then
  echo "The GitHub CLI is not installed, so open the pull request yourself:"
  echo "  $from -> $to"
  exit 0
fi

body="$(git --no-pager log --pretty='- %s' "origin/$to..origin/$from")"
gh pr create \
  --base "$to" \
  --head "$from" \
  --title "promote $from to $to ($ahead commit(s))" \
  --body "$(printf 'Fast-forward promotion.\n\n%s\n' "$body")"

echo
echo "Opened. Merge it with a fast-forward once the checks are green:"
echo "  gh pr merge --rebase --delete-branch=false"
