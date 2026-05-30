#!/usr/bin/env sh
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>

set -eu

capture_pid=""

stop_capture() {
    if [ -n "$capture_pid" ]; then
        kill "$capture_pid" 2>/dev/null || true
        wait "$capture_pid" 2>/dev/null || true
    fi
}

trap stop_capture EXIT INT TERM

mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi /tmp/nginx/uwsgi /tmp/nginx/scgi /var/lib/nats-sinks-http

python3 /usr/local/bin/nats-sinks-http-capture &
capture_pid="$!"

nginx -e /dev/stderr -c /etc/nats-sinks-http/nginx.conf -g 'daemon off; master_process off;'
