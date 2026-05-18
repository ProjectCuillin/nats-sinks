#!/usr/bin/env sh
set -eu

ruff format .
ruff check --fix .
