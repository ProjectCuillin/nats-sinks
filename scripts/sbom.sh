#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Generate release-evidence SBOM files without touching runtime configuration.
# The script intentionally reads package metadata from pyproject.toml and the
# active Python environment only. It must not inspect `.local/`, live service
# configuration, NATS messages, Oracle wallets, certificates, or payload data.
OUT_DIR="${1:-dist/sbom}"
PROJECT_FILE="pyproject.toml"

if ! python -m cyclonedx_py --version >/dev/null 2>&1; then
  echo "cyclonedx-bom is required to generate SBOM files." >&2
  echo "Install development dependencies with: python -m pip install -e '.[dev]'" >&2
  exit 1
fi

VERSION="$(
  python - <<'PY'
import tomllib
from pathlib import Path

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
)"

mkdir -p "$OUT_DIR"

JSON_OUT="$OUT_DIR/nats-sinks-${VERSION}.cyclonedx.json"
XML_OUT="$OUT_DIR/nats-sinks-${VERSION}.cyclonedx.xml"

# CycloneDX JSON and XML describe the same dependency inventory. Both are
# generated because different security platforms prefer different formats.
python -m cyclonedx_py environment \
  --pyproject "$PROJECT_FILE" \
  --mc-type library \
  --output-reproducible \
  --of JSON \
  -o "$JSON_OUT"

python -m cyclonedx_py environment \
  --pyproject "$PROJECT_FILE" \
  --mc-type library \
  --output-reproducible \
  --of XML \
  -o "$XML_OUT"

echo "Generated SBOM files:"
echo "  $JSON_OUT"
echo "  $XML_OUT"
