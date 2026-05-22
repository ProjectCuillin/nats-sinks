#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Manually dispatch the GitHub validation workflows for the current branch.
#
# Normal branch pushes are intentionally quiet. Maintainers run this helper only
# when a branch is ready for merge/release validation.

set -euo pipefail

REPO=""
REF=""

usage() {
  cat <<'USAGE'
Usage: scripts/run-release-validation.sh [--repo OWNER/REPO] [--ref BRANCH]

Dispatches the manual validation workflows used before merging a release,
feature, bugfix, or hotfix branch into main.

This script intentionally starts GitHub Actions. Use it only when the branch is
ready for merge/release validation, not after every small branch commit.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="${2:?--repo requires OWNER/REPO}"
      shift 2
      ;;
    --ref)
      REF="${2:?--ref requires a branch name}"
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

if [[ -z "$REF" ]]; then
  REF="$(git branch --show-current)"
fi

if [[ -z "$REF" ]]; then
  echo "Unable to determine the branch to validate." >&2
  exit 1
fi

if [[ "$REF" == "main" ]]; then
  echo "Refusing to run release-candidate validation against main." >&2
  echo "Validate the work branch before merging, then tag main for release." >&2
  exit 1
fi

echo "Dispatching release validation workflows for $REPO@$REF"
gh workflow run ci.yml --repo "$REPO" --ref "$REF"
gh workflow run docs.yml --repo "$REPO" --ref "$REF"
gh workflow run codeql.yml --repo "$REPO" --ref "$REF"

echo "Validation workflows dispatched. Use 'gh run list --repo $REPO --branch $REF' to watch them."
