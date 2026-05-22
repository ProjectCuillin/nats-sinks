#!/usr/bin/env sh
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

set -eu

ruff format .
ruff check --fix .
