#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Deterministic sink coverage that should run before every release. Live Oracle
# and live NATS-to-Oracle checks remain opt-in because they require ignored
# local environment files and external services.

pytest \
  tests/unit/test_container_e2e_suite.py \
  tests/unit/test_disconnected_spool_replay.py \
  tests/unit/test_oracle_disconnected_replay_verification.py \
  tests/unit/test_file_mapping.py \
  tests/unit/test_file_sink.py \
  tests/unit/test_oracle_nosql_sink.py \
  tests/unit/test_oracle_nosql_test_container.py \
  tests/unit/test_coherence_sink.py \
  tests/unit/test_foundry_sink.py \
  tests/unit/test_gotham_sink.py \
  tests/unit/test_s3_sink.py \
  tests/unit/test_multi_sink_routing_e2e.py \
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
nats-sink validate examples/oracle-nosql-basic/config.json
nats-sink validate examples/oracle-coherence-basic/config.json
nats-sink validate examples/multi-sink-routing-e2e/config.json
nats-sink validate examples/foundry-basic/config.json
nats-sink validate examples/gotham-basic/config.json
nats-sink validate examples/s3-basic/config.json
python scripts/run-multi-sink-routing-e2e.py \
  --mode reduced \
  --output .local/check-sinks/multi-sink-routing-report.json

if [ "${NATS_SINKS_RUN_LIVE_ORACLE:-0}" = "1" ]; then
  pytest -m integration tests/integration/test_oracle_sink.py
fi

if [ "${NATS_SINKS_RUN_LIVE_E2E:-0}" = "1" ]; then
  scripts/run-oracle-e2e.sh
fi

if [ "${NATS_SINKS_RUN_CONTAINER_E2E:-0}" = "1" ]; then
  python scripts/run-container-e2e-suite.py
fi

if [ "${NATS_SINKS_RUN_COHERENCE_E2E:-0}" = "1" ]; then
  python scripts/run-coherence-sink-e2e.py
fi

if [ "${NATS_SINKS_RUN_ORACLE_NOSQL_E2E:-0}" = "1" ]; then
  NATS_SINKS_ORACLE_NOSQL_INTEGRATION=1 \
    pytest -m integration tests/integration/test_oracle_nosql_sink_e2e.py
fi

if [ "${NATS_SINKS_RUN_ORACLE_NOSQL_CONTAINER_SMOKE:-0}" = "1" ]; then
  python scripts/run-oracle-nosql-container-smoke.py
fi

if [ "${NATS_SINKS_RUN_ORACLE_NOSQL_SINK_E2E:-0}" = "1" ]; then
  python scripts/run-oracle-nosql-sink-e2e.py
fi
