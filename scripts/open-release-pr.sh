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
AUTO_APPROVE_NON_MAIN="${NATS_SINKS_AUTO_APPROVE_NON_MAIN_PR:-true}"
AUTO_APPROVE_EXPLICIT=false
COPY_ISSUE_LABELS="${NATS_SINKS_COPY_ISSUE_LABELS_TO_PR:-true}"
PR_ISSUES=()

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

usage() {
  cat <<'USAGE'
Usage: scripts/open-release-pr.sh [--repo OWNER/REPO] [--base BRANCH] [--ready]
                                  [--issue NUMBER] [--copy-issue-labels-to-pr|--no-copy-issue-labels-to-pr]
                                  [--auto-approve-non-main|--no-auto-approve-non-main]

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

Ready issue, feature, and bug pull requests targeting a non-main branch are
auto-approved by default when they were raised by the current GitHub identity.
Use --no-auto-approve-non-main or NATS_SINKS_AUTO_APPROVE_NON_MAIN_PR=false to
disable that behavior. The helper refuses release pull requests that target
main.

By default the helper also copies labels from the managed source issue to the
pull request. It detects issue numbers from branch names such as issue-123-...
or bug-123-..., scans Related #123 references in the PR body, and accepts
--issue NUMBER for branches that intentionally cover one or more issues.
Use --no-copy-issue-labels-to-pr or NATS_SINKS_COPY_ISSUE_LABELS_TO_PR=false
to disable this behavior for an exceptional branch.
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
    --issue)
      PR_ISSUES+=("${2:?--issue requires an issue number}")
      shift 2
      ;;
    --copy-issue-labels-to-pr)
      COPY_ISSUE_LABELS=true
      shift
      ;;
    --no-copy-issue-labels-to-pr)
      COPY_ISSUE_LABELS=false
      shift
      ;;
    --auto-approve-non-main)
      AUTO_APPROVE_NON_MAIN=true
      AUTO_APPROVE_EXPLICIT=true
      shift
      ;;
    --no-auto-approve-non-main)
      AUTO_APPROVE_NON_MAIN=false
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

if [[ ${#PR_ISSUES[@]} -eq 0 && -n "${NATS_SINKS_PR_ISSUES:-}" ]]; then
  normalized_issue_list="${NATS_SINKS_PR_ISSUES//,/ }"
  # shellcheck disable=SC2206
  PR_ISSUES=($normalized_issue_list)
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

if [[ "$AUTO_APPROVE_EXPLICIT" == "true" && "$AUTO_APPROVE_NON_MAIN" == "true" && "$BASE" == "main" ]]; then
  echo "Refusing to auto-approve a pull request targeting main." >&2
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

Use a `Related #<issue-number>` line for managed issues. Release automation
closes those issues only after the associated GitHub Release exists.
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
  if ! gh pr edit "$existing_pr" \
    --repo "$REPO" \
    --title "$title" \
    --body-file "$body_file"; then
    echo "Unable to refresh pull request title/body through gh pr edit." >&2
    echo "Continuing because the existing pull request can still be reviewed." >&2
  fi
  if [[ "$DRAFT" == "false" ]]; then
    gh pr ready "$existing_pr" --repo "$REPO" >/dev/null 2>&1 || true
  fi
  echo "Updated pull request #$existing_pr for $BRANCH."
  pr_number="$existing_pr"
else
  create_args=(
    "pr" "create"
    "--repo" "$REPO"
    "--base" "$BASE"
    "--head" "$BRANCH"
    "--title" "$title"
    "--body-file" "$body_file"
  )
  if [[ "$DRAFT" == "true" ]]; then
    create_args+=("--draft")
  fi
  gh "${create_args[@]}"
  pr_number="$(
    gh pr list \
      --repo "$REPO" \
      --head "$BRANCH" \
      --base "$BASE" \
      --state open \
      --json number \
      --jq '.[0].number // empty'
  )"
fi

if [[ "$COPY_ISSUE_LABELS" == "true" ]]; then
  if [[ -z "${pr_number:-}" ]]; then
    echo "Unable to determine pull request number for label sync." >&2
    exit 1
  fi
  label_command=(
    "python" "$SCRIPT_DIR/sync-pr-labels.py"
    "--repo" "$REPO"
    "--pr" "$pr_number"
  )
  if [[ ${#PR_ISSUES[@]} -gt 0 ]]; then
    for issue_number in "${PR_ISSUES[@]}"; do
      label_command+=("--issue" "$issue_number")
    done
  fi
  if ! "${label_command[@]}"; then
    echo "Unable to copy source issue labels to pull request #$pr_number." >&2
    exit 1
  fi
fi

if [[ "$AUTO_APPROVE_NON_MAIN" == "true" && "$DRAFT" == "false" && "$BASE" != "main" ]]; then
  if [[ -z "${pr_number:-}" ]]; then
    echo "Unable to determine pull request number for auto-approval." >&2
    exit 1
  fi
  expected_author="${NATS_SINKS_PR_AUTO_APPROVE_EXPECTED_AUTHOR:-}"
  if [[ -z "$expected_author" ]]; then
    expected_author="$(gh api user --jq .login)"
  fi
  if ! "$SCRIPT_DIR/approve-non-main-pr.sh" \
    --repo "$REPO" \
    --pr "$pr_number" \
    --expected-author "$expected_author"; then
    if [[ "$AUTO_APPROVE_EXPLICIT" == "true" ]]; then
      exit 1
    fi
    echo "Non-main auto-approval was not applied. GitHub may reject self-approval;" >&2
    echo "use a separate reviewer/bot identity or approve the PR manually." >&2
  fi
fi
