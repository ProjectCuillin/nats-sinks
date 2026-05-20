#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run the live NATS-to-Oracle end-to-end test using local, ignored environment
# files. This script deliberately does not contain server names, usernames,
# passwords, CA certificates, wallets, or database connection strings.

set -euo pipefail

DROP_TABLE_BEFORE=false
DROP_TABLE_AFTER=false
TABLE_OVERRIDE=""
MESSAGE_COUNT_OVERRIDE=""
BATCH_SIZE_OVERRIDE=""
WITH_ENCRYPTION=false
ENCRYPTION_ALGORITHM="aes-256-gcm"
PRESERVE_KEY_MATERIAL=false
KEY_DIR=""

usage() {
  cat <<'USAGE'
Usage: scripts/run-oracle-e2e.sh [options]

Options:
  --drop-table-before        Drop the configured e2e Oracle test table before running.
  --drop-table-after         Drop the configured e2e Oracle test table after running.
  --table NAME               Override NATS_SINKS_E2E_ORACLE_TABLE for this run.
  --message-count N          Override NATS_SINKS_E2E_MESSAGE_COUNT for this run.
  --batch-size N             Override NATS_SINKS_E2E_BATCH_SIZE for this run.
  --with-encryption          Encrypt payloads before Oracle writes and verify decryption.
  --encryption-algorithm ALG  Use aes-256-gcm or aes-256-ccm with --with-encryption.
  --preserve-key-material    Keep generated temporary e2e key material after the run.
  -h, --help                 Show this help.

Defaults keep the Oracle test table after the run so operators can inspect rows.
Secrets should live in .local/* env files or the shell environment, not here.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --drop-table-before)
      DROP_TABLE_BEFORE=true
      shift
      ;;
    --drop-table-after)
      DROP_TABLE_AFTER=true
      shift
      ;;
    --table)
      TABLE_OVERRIDE="${2:?--table requires a value}"
      shift 2
      ;;
    --message-count)
      MESSAGE_COUNT_OVERRIDE="${2:?--message-count requires a value}"
      shift 2
      ;;
    --batch-size)
      BATCH_SIZE_OVERRIDE="${2:?--batch-size requires a value}"
      shift 2
      ;;
    --with-encryption)
      WITH_ENCRYPTION=true
      shift
      ;;
    --encryption-algorithm)
      ENCRYPTION_ALGORITHM="${2:?--encryption-algorithm requires a value}"
      shift 2
      ;;
    --preserve-key-material)
      PRESERVE_KEY_MATERIAL=true
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

cleanup_key_material() {
  if [[ -n "$KEY_DIR" ]]; then
    if [[ "$PRESERVE_KEY_MATERIAL" == "true" ]]; then
      echo "Preserved generated Oracle e2e encryption key material in: ${KEY_DIR}/nats-sinks-e2e-key.env"
    else
      rm -rf "$KEY_DIR"
    fi
  fi
}
trap cleanup_key_material EXIT INT TERM

# Apply command-line overrides after local environment files are sourced so
# repeatable test invocations cannot be accidentally changed by stale .local
# defaults.
if [[ -n "$TABLE_OVERRIDE" ]]; then
  export NATS_SINKS_E2E_ORACLE_TABLE="$TABLE_OVERRIDE"
fi
if [[ -n "$MESSAGE_COUNT_OVERRIDE" ]]; then
  export NATS_SINKS_E2E_MESSAGE_COUNT="$MESSAGE_COUNT_OVERRIDE"
fi
if [[ -n "$BATCH_SIZE_OVERRIDE" ]]; then
  export NATS_SINKS_E2E_BATCH_SIZE="$BATCH_SIZE_OVERRIDE"
fi
export NATS_SINKS_E2E_DROP_TABLE_BEFORE="$DROP_TABLE_BEFORE"
export NATS_SINKS_E2E_DROP_TABLE_AFTER="$DROP_TABLE_AFTER"

if [[ "$WITH_ENCRYPTION" == "true" ]]; then
  export NATS_SINKS_E2E_ENCRYPTION_ENABLED=true
  export NATS_SINKS_E2E_ENCRYPTION_ALGORITHM="$ENCRYPTION_ALGORITHM"
  export NATS_SINKS_E2E_ENCRYPTION_KEY_ID="nats-sinks-e2e-generated"
  export NATS_SINKS_E2E_ENCRYPTION_KEY_B64_ENV="NATS_SINKS_E2E_ENCRYPTION_KEY_B64"
  if [[ -z "${NATS_SINKS_E2E_ENCRYPTION_KEY_B64:-}" ]]; then
    KEY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nats-sinks-oracle-e2e-key.XXXXXX")"
    KEY_B64="$(python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))')"
    umask 077
    printf 'NATS_SINKS_E2E_ENCRYPTION_KEY_B64=%s\n' "$KEY_B64" > "${KEY_DIR}/nats-sinks-e2e-key.env"
    export NATS_SINKS_E2E_ENCRYPTION_KEY_B64="$KEY_B64"
  fi
fi

python -m pytest -q -s -m integration tests/integration/test_nats_oracle_e2e.py
