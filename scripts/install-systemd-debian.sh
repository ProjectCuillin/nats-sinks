#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)

echo "scripts/install-systemd-debian.sh is deprecated; use scripts/install-systemd.sh." >&2
exec "$SCRIPT_DIR/install-systemd.sh"
