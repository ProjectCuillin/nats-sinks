#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Thin shell entry point for the Python approval helper. The project keeps the
# policy logic in Python so it can be unit tested without touching GitHub.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
exec python "$SCRIPT_DIR/approve-non-main-pr.py" "$@"
