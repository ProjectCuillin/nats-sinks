#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Validate local GitHub CLI authentication before a maintainer release.
#
# This helper is intentionally local-only. The GitHub Actions release workflow
# uses OIDC and repository permissions; it does not depend on a maintainer's
# workstation token. The helper exists so maintainers can reliably inspect
# workflow status with `gh run list`, `gh run view`, and release commands after
# pushing a tag.

set -euo pipefail

HOST="github.com"
INTERACTIVE=true

usage() {
  cat <<'USAGE'
Usage: scripts/check-gh-auth.sh [options]

Options:
  --hostname HOST       GitHub hostname to check. Default: github.com.
  --check-only          Do not prompt for login; exit non-zero if auth is bad.
  -h, --help            Show this help.

The script never prints token values. If authentication is invalid and an
interactive terminal is available, it can run:

  gh auth login --hostname HOST --web

USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      HOST="${2:?--hostname requires a value}"
      shift 2
      ;;
    --check-only)
      INTERACTIVE=false
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
  echo "GitHub CLI is not installed or is not on PATH." >&2
  echo "Install gh before using release status commands." >&2
  exit 1
fi

if gh auth status --hostname "$HOST" >/dev/null 2>&1; then
  echo "GitHub CLI authentication is valid for $HOST."
  exit 0
fi

echo "GitHub CLI authentication is not valid for $HOST." >&2
if [[ -n "${GH_TOKEN:-}" || -n "${GITHUB_TOKEN:-}" ]]; then
  echo "A GH_TOKEN or GITHUB_TOKEN environment variable is set." >&2
  echo "If that token is stale, unset it before relying on stored gh auth." >&2
fi

if [[ "$INTERACTIVE" != "true" ]]; then
  echo "Run 'gh auth login --hostname $HOST --web' and retry." >&2
  exit 1
fi

if [[ ! -t 0 ]]; then
  echo "No interactive terminal is available." >&2
  echo "Run 'gh auth login --hostname $HOST --web' in a terminal and retry." >&2
  exit 1
fi

read -r -p "Run 'gh auth login --hostname $HOST --web' now? [y/N] " answer
case "$answer" in
  y|Y|yes|YES)
    gh auth login --hostname "$HOST" --web
    ;;
  *)
    echo "Authentication was not changed." >&2
    exit 1
    ;;
esac

if gh auth status --hostname "$HOST" >/dev/null 2>&1; then
  echo "GitHub CLI authentication is valid for $HOST."
else
  echo "GitHub CLI authentication is still not valid for $HOST." >&2
  exit 1
fi
