#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

PRESERVE_KEY_MATERIAL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --preserve-key-material)
      PRESERVE_KEY_MATERIAL=1
      shift
      ;;
    --help|-h)
      echo "Usage: scripts/check-encryption.sh [--preserve-key-material]"
      echo
      echo "Generates temporary AES-256 key material and runs encryption-focused tests."
      echo "By default the generated key file is deleted after the tests finish."
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

KEY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nats-sinks-crypto-test.XXXXXX")"
KEY_FILE="${KEY_DIR}/nats-sinks-test-key.env"

cleanup() {
  if [ "$PRESERVE_KEY_MATERIAL" -eq 1 ]; then
    echo "Preserved generated encryption key material in: ${KEY_FILE}"
  else
    rm -rf "$KEY_DIR"
  fi
}
trap cleanup EXIT INT TERM

KEY_B64="$(python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode("ascii"))')"
umask 077
printf 'NATS_SINKS_TEST_ENCRYPTION_KEY_B64=%s\n' "$KEY_B64" > "$KEY_FILE"

export NATS_SINKS_TEST_ENCRYPTION_KEY_B64="$KEY_B64"

pytest \
  tests/unit/test_encryption.py \
  tests/unit/test_commit_then_ack_contract.py \
  tests/unit/test_file_sink.py \
  tests/integration/test_file_sink_e2e.py \
  tests/unit/test_oracle_mapping.py \
  tests/unit/test_oracle_sink_contract.py
