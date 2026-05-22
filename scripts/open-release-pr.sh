#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Push the current release/work branch and open or update a pull request into
# main. The GitHub workflow also creates pull requests for pushed release
# branches, but this local helper is useful when a maintainer wants immediate
# feedback from their own GitHub identity and token.

set -euo pipefail

REPO=""
BASE="main"
DRAFT=true

usage() {
  cat <<'USAGE'
Usage: scripts/open-release-pr.sh [--repo OWNER/REPO] [--ready]

Run from a branch named release-*, feature-*, bugfix-*, or hotfix-*.
The script pushes the branch to origin and opens or updates a pull request
against main. It refuses to operate from main.

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

if [[ "$BRANCH" == "$BASE" ]]; then
  echo "Refusing to open a release pull request from main." >&2
  exit 1
fi

case "$BRANCH" in
  release-*|feature-*|bugfix-*|hotfix-*)
    ;;
  *)
    echo "Branch '$BRANCH' must start with release-, feature-, bugfix-, or hotfix-." >&2
    exit 1
    ;;
esac

title="Work branch: $BRANCH"
if [[ "$BRANCH" =~ ^release-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  title="Release ${BRANCH#release-v}"
fi

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

cat >"$body_file" <<'PR_BODY'
## Branch-First Release Workflow

This pull request is the release boundary for work that must not be committed
directly to `main`.

## Required Release Controls

- [ ] CI is green for all supported Python versions.
- [ ] CodeQL, dependency review, documentation, and sink checks are green when applicable.
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
