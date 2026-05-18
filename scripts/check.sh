#!/usr/bin/env sh
set -eu

ruff format --check .
ruff check .
mypy src
python scripts/check-markdown-links.py
pytest
mkdocs build --strict
scripts/check-sinks.sh
python -m build
twine check dist/*
