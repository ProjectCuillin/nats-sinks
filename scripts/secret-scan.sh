#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Keep this scanner dependency-free so it can run in local development, CI, and
# pre-commit without requiring another package.  It intentionally looks for
# high-confidence secret material and known local-test credential shapes rather
# than broad words such as "password", which appear legitimately in docs and
# schema examples.
PATTERN='-----BEGIN ((RSA|DSA|EC|OPENSSH) )?PRIVATE KEY-----|-----BEGIN CERTIFICATE-----|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|abc123ABC123|c354c3d51abfd2964be907e40a6a2af6c67c2c76f931702b'

set +e
if command -v rg >/dev/null 2>&1; then
  rg -n --hidden \
    -g '!.git/**' \
    -g '!.local/**' \
    -g '!site/**' \
    -g '!dist/**' \
    -g '!build/**' \
    -g '!*.egg-info/**' \
    -g '!*.pyc' \
    -g '!scripts/secret-scan.sh' \
    -- "$PATTERN" .
else
  grep -RInE \
    --exclude-dir=.git \
    --exclude-dir=.local \
    --exclude-dir=site \
    --exclude-dir=dist \
    --exclude-dir=build \
    --exclude='*.egg-info' \
    --exclude='*.pyc' \
    --exclude='secret-scan.sh' \
    -- "$PATTERN" .
fi
status=$?
set -e

if [ "$status" -eq 0 ]; then
  printf '%s\n' "Potential secret material was found. Remove it or move it under an ignored local path." >&2
  exit 1
fi

if [ "$status" -gt 1 ]; then
  printf '%s\n' "Secret scan failed before completion." >&2
  exit "$status"
fi

printf '%s\n' "No high-confidence secret material found."
