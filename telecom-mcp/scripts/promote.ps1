# The same promotion, for a PowerShell terminal in VS Code on Windows.
#
#   ./scripts/promote.ps1 Arshad staging
#   ./scripts/promote.ps1 staging production
#
# See scripts/promote.sh for why a promotion has to be a fast-forward.
param(
  [Parameter(Mandatory = $true)][string]$From,
  [Parameter(Mandatory = $true)][string]$To
)

$ErrorActionPreference = 'Stop'

if (-not ("$From`:$To" -in @('Arshad:staging', 'staging:production'))) {
  throw "The only promotions are Arshad -> staging and staging -> production."
}

git fetch --prune origin $From $To

git merge-base --is-ancestor "origin/$To" "origin/$From"
if ($LASTEXITCODE -ne 0) {
  Write-Error @"
origin/$To has commits that origin/$From does not, so this promotion would need a
merge commit and $To would run a tree $From never ran.
Bring them back in line first:  git checkout $From; git merge --ff-only origin/$To
"@
  exit 1
}

$ahead = (git rev-list --count "origin/$To..origin/$From").Trim()
if ($ahead -eq '0') {
  Write-Host "$To is already up to date with $From; nothing to promote"
  exit 0
}

Write-Host "`n$ahead commit(s) to promote from $From to $To:`n"
git --no-pager log --oneline --no-decorate "origin/$To..origin/$From"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  Write-Host "`nThe GitHub CLI is not installed, so open the pull request yourself: $From -> $To"
  exit 0
}

$body = (git --no-pager log --pretty='- %s' "origin/$To..origin/$From") -join "`n"
gh pr create --base $To --head $From `
  --title "promote $From to $To ($ahead commit(s))" `
  --body "Fast-forward promotion.`n`n$body"

Write-Host "`nOpened. Merge with a fast-forward once the checks are green:"
Write-Host "  gh pr merge --rebase --delete-branch=false"
