#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

#
# Run the live Oracle benchmark using local, ignored environment files.  The
# wrapper exists so operators can keep NATS and Oracle secrets out of command
# history and source them from .local files instead.

set -euo pipefail

source_if_present() {
  local path="$1"
  if [[ -f "$path" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$path"
    set +a
  fi
}

source_if_present ".local/nats-live/nats-sink.env"
source_if_present ".local/oracle-adb/integration.env"
source_if_present ".local/nats-oracle-e2e/integration.env"
source_if_present ".local/oracle-benchmark/integration.env"

export NATS_SINKS_ORACLE_BENCHMARK=1

python scripts/run-oracle-benchmark.py "$@"
