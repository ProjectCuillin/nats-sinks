#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Deterministic sink coverage that should run before every release. Live Oracle
# and live NATS-to-Oracle checks remain opt-in because they require ignored
# local environment files and external services.

pytest \
  tests/unit/test_file_mapping.py \
  tests/unit/test_file_sink.py \
  tests/integration/test_file_sink_e2e.py \
  tests/unit/test_fanout_certification.py \
  tests/unit/test_oracle_mapping.py \
  tests/unit/test_oracle_routing.py \
  tests/unit/test_sink_certification.py \
  tests/unit/test_oracle_sink_contract.py \
  tests/unit/test_oracle_sql.py

nats-sink validate examples/file-basic/config.json
nats-sink test-sink examples/file-basic/config.json
nats-sink validate examples/payload-encryption/file-config.json
nats-sink test-sink examples/payload-encryption/file-config.json
nats-sink validate examples/oracle-jetstream/config.json

if [ "${NATS_SINKS_RUN_LIVE_ORACLE:-0}" = "1" ]; then
  pytest -m integration tests/integration/test_oracle_sink.py
fi

if [ "${NATS_SINKS_RUN_LIVE_E2E:-0}" = "1" ]; then
  scripts/run-oracle-e2e.sh
fi
