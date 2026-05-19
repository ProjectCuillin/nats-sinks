#!/usr/bin/env sh
set -eu

ruff format --check .
ruff check .
mypy src
python scripts/check-markdown-links.py
pytest
mkdocs build --strict
NATS_SINKS_DOCS_SITE_URL="https://projectcuillin.github.io/nats-sinks/" mkdocs build --strict
scripts/check-sinks.sh
scripts/security.sh
python -m build
twine check dist/*
