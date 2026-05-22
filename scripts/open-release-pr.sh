#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Push the current branch and open or update a pull request into the next
# branch in the hierarchy. Release branches target main, issue branches target
# the active release branch, and bug branches target their parent issue branch.
# This local helper is useful when a maintainer wants immediate feedback from
# their own GitHub identity and token while ordinary branch pushes stay quiet.

set -euo pipefail

REPO=""
BASE="${NATS_SINKS_PR_BASE:-}"
DRAFT=true

usage() {
  cat <<'USAGE'
Usage: scripts/open-release-pr.sh [--repo OWNER/REPO] [--base BRANCH] [--ready]

Run from a branch named release-*, issue-*, feature-*, bug-*, bugfix-*, or
hotfix-*. The script pushes the branch to origin and opens or updates a pull
request against the selected base branch. It refuses to operate from main.

Use --base release-vX.Y.Z for issue or feature branches, --base issue-N-name
for bug branches created during feature development, and --base main for the
final release pull request. If --base is omitted for a release-vX.Y.Z branch,
the script defaults to main. For non-release branches, provide --base or set
NATS_SINKS_PR_BASE.

By default the pull request is created as a draft so GitHub Actions stay quiet
while the branch is still receiving small commits. Pass --ready only when the
branch is ready for merge/release validation.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:?--repo requires OWNER/REPO}"
      shift 2
      ;;
    --base)
      BASE="${2:?--base requires a branch name}"
      shift 2
      ;;
    --ready)
      DRAFT=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI is required." >&2
  exit 1
fi

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
fi

BRANCH="$(git branch --show-current)"
if [[ -z "$BRANCH" ]]; then
  echo "Unable to determine the current branch." >&2
  exit 1
fi

if [[ "$BRANCH" == "main" ]]; then
  echo "Refusing to open a pull request from main." >&2
  exit 1
fi

case "$BRANCH" in
  release-*|issue-*|feature-*|bug-*|bugfix-*|hotfix-*)
    ;;
  *)
    echo "Branch '$BRANCH' must start with release-, issue-, feature-, bug-, bugfix-, or hotfix-." >&2
    exit 1
    ;;
esac

if [[ -z "$BASE" ]]; then
  if [[ "$BRANCH" =~ ^release-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    BASE="main"
  else
    echo "A pull request base is required for non-release branches." >&2
    echo "Use --base release-vX.Y.Z for issue branches or --base issue-N-name for bug branches." >&2
    exit 2
  fi
fi

if [[ "$BRANCH" == "$BASE" ]]; then
  echo "Refusing to open a pull request from a branch into itself: $BRANCH." >&2
  exit 1
fi

title="Work branch: $BRANCH"
if [[ "$BRANCH" =~ ^release-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  title="Release ${BRANCH#release-v}"
elif [[ "$BRANCH" =~ ^issue-[0-9]+- ]]; then
  title="Issue branch: $BRANCH"
elif [[ "$BRANCH" =~ ^feature-[0-9]+- ]]; then
  title="Feature branch: $BRANCH"
elif [[ "$BRANCH" =~ ^bug-[0-9]+- ]]; then
  title="Bug branch: $BRANCH"
fi

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

cat >"$body_file" <<'PR_BODY'
## Hierarchical Branch Workflow

This pull request moves work to the next branch in the release hierarchy. Bug
branches should target their parent issue or feature branch. Issue and feature
branches should target the active release branch. Release branches should
target `main` only when the maintainer has explicitly decided to release.

## Required Controls

- [ ] Local checks have been run for the scope of this branch.
- [ ] Manual CI, CodeQL, dependency review, documentation, and sink checks are green when this is release-bound validation.
- [ ] Changelog and documentation are updated for user-visible changes.
- [ ] Managed issues have implementation, test evidence, and close-out comments.
- [ ] No secrets, credentials, private endpoints, payload dumps, or local paths are included.
- [ ] Maintainer review has approved the merge.

Use `Related #123` for managed issues. Release automation closes those issues
only after the associated GitHub Release exists.
PR_BODY

git push -u origin "$BRANCH"

existing_pr="$(
  gh pr list \
    --repo "$REPO" \
    --head "$BRANCH" \
    --base "$BASE" \
    --state open \
    --json number \
    --jq '.[0].number // empty'
)"

if [[ -n "$existing_pr" ]]; then
  gh pr edit "$existing_pr" \
    --repo "$REPO" \
    --title "$title" \
    --body-file "$body_file"
  echo "Updated pull request #$existing_pr for $BRANCH."
else
  draft_args=()
  if [[ "$DRAFT" == "true" ]]; then
    draft_args+=(--draft)
  fi
  gh pr create \
    --repo "$REPO" \
    --base "$BASE" \
    --head "$BRANCH" \
    --title "$title" \
    --body-file "$body_file" \
    "${draft_args[@]}"
fi
