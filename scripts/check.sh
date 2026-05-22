#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

ruff format --check .
ruff check .
mypy src
python scripts/check-version-consistency.py
python scripts/update-dependency-manifests.py --check
python scripts/sync-backlog-issues.py --check
python scripts/sync-bug-reports.py --check
python scripts/check-markdown-links.py
pytest
scripts/check-encryption.sh
scripts/check-docs.sh
scripts/check-sinks.sh
scripts/security.sh
python -m build
scripts/sbom.sh
python scripts/generate-checksums.py dist
twine check dist/*.whl dist/*.tar.gz
