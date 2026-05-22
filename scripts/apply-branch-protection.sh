#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Apply the repository's main-branch protection policy through the GitHub API.
#
# The policy is intentionally narrow and release oriented:
# - direct pushes to main are blocked by requiring pull requests,
# - CODEOWNER review is required before merge,
# - stale reviews are dismissed after new commits,
# - the CI matrix must pass before merge,
# - administrators are also covered so the policy protects emergency work from
#   accidentally bypassing the public release trail.
#
# The script uses gh and never handles secrets directly. Maintainers should
# authenticate gh with an account that has repository administration rights.

set -euo pipefail

REPO=""
BRANCH="main"

usage() {
  cat <<'USAGE'
Usage: scripts/apply-branch-protection.sh [--repo OWNER/REPO] [--branch BRANCH]

Applies branch protection for the selected branch. Defaults to the current
GitHub repository and the main branch.

Required GitHub CLI authentication:
  gh auth login --hostname github.com --web

The authenticated account must be allowed to administer branch protection.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:?--repo requires OWNER/REPO}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:?--branch requires a branch name}"
      shift 2
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

if [[ "$BRANCH" != "main" ]]; then
  echo "Refusing to apply release branch policy to '$BRANCH'." >&2
  echo "This repository protects main as the only release merge branch." >&2
  exit 1
fi

echo "Applying branch protection to $REPO:$BRANCH"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "/repos/$REPO/branches/$BRANCH/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "test (3.11)",
      "test (3.12)",
      "test (3.13)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

echo "Branch protection applied. Verify in GitHub settings before the next release."
