#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

# Build each documentation target in an isolated temporary output directory.
# MkDocs cleans the output directory before building; using a shared `site/`
# directory for two simultaneous builds can make one build remove files that the
# other build is still traversing. Release and CI checks therefore use unique
# directories and leave `site/` to the GitHub Pages workflow that uploads it.
BUILD_ROOT="${NATS_SINKS_DOCS_BUILD_ROOT:-.local/docs-build}"
BUILD_ROOT="${BUILD_ROOT%/}"
mkdir -p "$BUILD_ROOT"

READTHEDOCS_SITE_DIR="$(mktemp -d "$BUILD_ROOT/readthedocs.XXXXXX")"
GITHUB_PAGES_SITE_DIR="$(mktemp -d "$BUILD_ROOT/github-pages.XXXXXX")"

cleanup() {
  rm -rf "$READTHEDOCS_SITE_DIR" "$GITHUB_PAGES_SITE_DIR"
}

trap cleanup EXIT HUP INT TERM

mkdocs build --strict --site-dir "$READTHEDOCS_SITE_DIR"
NATS_SINKS_DOCS_SITE_URL="https://projectcuillin.github.io/nats-sinks/" \
  mkdocs build --strict --site-dir "$GITHUB_PAGES_SITE_DIR"
