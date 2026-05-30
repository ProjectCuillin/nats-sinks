#!/usr/bin/env sh
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

set -eu

REPO_URL="${NATS_SINKS_DEMO_REPO_URL:-https://github.com/ProjectCuillin/nats-sinks.git}"
REF="${NATS_SINKS_DEMO_REF:-main}"
MESSAGE_COUNT="${NATS_SINKS_DEMO_MESSAGE_COUNT:-8}"
KEEP_RUNNING="${NATS_SINKS_DEMO_KEEP_RUNNING:-1}"
PROJECT_NAME="${NATS_SINKS_DEMO_PROJECT_NAME:-nats-sinks-mvp-demo}"
DEMO_ROOT="${NATS_SINKS_DEMO_ROOT:-}"

fail() {
    printf '%s\n' "nats-sinks local MVP demo: $*" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

detect_supported_os() {
    if [ ! -r /etc/os-release ]; then
        printf '%s\n' "Unable to read /etc/os-release; continuing with generic prerequisite checks." >&2
        return 0
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    os_line="${ID:-} ${ID_LIKE:-}"
    case "$os_line" in
        *debian*|*ubuntu*|*ol*|*oracle*|*rhel*|*fedora*)
            return 0
            ;;
        *)
            printf '%s\n' "This demo helper is documented for Debian, Ubuntu, and Oracle Linux." >&2
            printf '%s\n' "Continuing anyway because Docker, Git, and Python availability matter most." >&2
            return 0
            ;;
    esac
}

check_prerequisites() {
    have git || fail "git is required so the helper can clone the demo checkout."
    have docker || fail "docker with the Compose plugin is required for the container demo."
    have python3 || fail "python3 is required to create the local demo virtual environment."

    docker compose version >/dev/null 2>&1 || fail "docker compose is required."
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        || fail "Python 3.11 or newer is required."
}

create_demo_root() {
    if [ -n "$DEMO_ROOT" ]; then
        mkdir -p "$DEMO_ROOT"
        printf '%s\n' "$DEMO_ROOT"
        return 0
    fi
    mktemp -d "${TMPDIR:-/tmp}/nats-sinks-mvp-demo.XXXXXX"
}

detect_supported_os
check_prerequisites

DEMO_ROOT="$(create_demo_root)"
REPO_DIR="$DEMO_ROOT/nats-sinks"
VENV_DIR="$DEMO_ROOT/.venv"

if [ -e "$REPO_DIR" ]; then
    fail "demo repository path already exists: $REPO_DIR"
fi

printf '%s\n' "Cloning nats-sinks $REF into $REPO_DIR"
git clone --depth 1 --branch "$REF" "$REPO_URL" "$REPO_DIR"

printf '%s\n' "Creating local Python environment under $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$REPO_DIR"

set -- "$VENV_DIR/bin/python" "$REPO_DIR/scripts/run-docker-local-smoke.py" \
    --message-count "$MESSAGE_COUNT" \
    --project-name "$PROJECT_NAME"

if [ "$KEEP_RUNNING" = "1" ] || [ "$KEEP_RUNNING" = "true" ]; then
    set -- "$@" --keep-running --keep-output
fi

printf '%s\n' "Running the local Docker/NATS/file-sink MVP demo."
cd "$REPO_DIR"
"$@"

printf '%s\n' ""
printf '%s\n' "Demo checkout: $REPO_DIR"
printf '%s\n' "File-sink output: $REPO_DIR/.local/docker-file-sink"

if [ "$KEEP_RUNNING" = "1" ] || [ "$KEEP_RUNNING" = "true" ]; then
    printf '%s\n' ""
    printf '%s\n' "The demo stack was left running for inspection."
    printf '%s\n' "Stop it with:"
    printf '  %s\n' "cd '$REPO_DIR' && docker compose -p '$PROJECT_NAME' -f examples/docker-local/compose.json down --volumes"
else
    printf '%s\n' "The demo stack was stopped after the smoke test."
fi
