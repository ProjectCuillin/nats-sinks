#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

rm -rf dist
python -m build
scripts/sbom.sh
python scripts/generate-checksums.py dist
twine check dist/*.whl dist/*.tar.gz
