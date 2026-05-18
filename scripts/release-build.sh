#!/usr/bin/env sh
set -eu

rm -rf dist
python -m build
twine check dist/*
