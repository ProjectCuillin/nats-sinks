#!/usr/bin/env sh
set -eu

ruff format --check .
ruff check .
mypy src
pytest
python -m build
twine check dist/*
