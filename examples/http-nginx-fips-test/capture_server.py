#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Local-only HTTP capture service behind the NGINX test endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CAPTURE_DIR = Path(os.environ.get("NATS_SINKS_HTTP_CAPTURE_DIR", "/var/lib/nats-sinks-http"))
CAPTURE_FILE = CAPTURE_DIR / "requests.jsonl"
MAX_BODY_BYTES = int(os.environ.get("NATS_SINKS_HTTP_CAPTURE_MAX_BODY_BYTES", "2097152"))
RESPONSE_STATUS = int(os.environ.get("NATS_SINKS_HTTP_RESPONSE_STATUS", "202"))
MAX_CAPTURED_HEADER_VALUE_BYTES = 512
CONTROL_CHARACTER_CUTOFF = 32
ASCII_DELETE = 127
MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599
SAFE_HEADERS = (
    "content-type",
    "idempotency-key",
    "user-agent",
    "x-nats-sinks-route",
    "x-forwarded-proto",
)


def _safe_header_value(value: str | None) -> str | None:
    """Return a small printable header value for local evidence."""

    if value is None:
        return None
    rendered = value.strip()
    if not rendered or len(rendered) > MAX_CAPTURED_HEADER_VALUE_BYTES:
        return None
    if any(
        ord(character) < CONTROL_CHARACTER_CUTOFF or ord(character) == ASCII_DELETE
        for character in rendered
    ):
        return None
    return rendered


class CaptureHandler(BaseHTTPRequestHandler):
    """Capture HTTP sink requests without logging sensitive request bodies."""

    server_version = "nats-sinks-http-capture"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress default request logging so payloads are not written to stderr."""

    def do_GET(self) -> None:
        """Serve a small health endpoint for readiness checks."""

        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_json(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:
        """Capture a POST request."""

        self._capture()

    def do_PUT(self) -> None:
        """Capture a PUT request."""

        self._capture()

    def do_PATCH(self) -> None:
        """Capture a PATCH request."""

        self._capture()

    def _capture(self) -> None:
        if self.path != "/nats-sink":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        record = {
            "method": self.command,
            "path": self.path,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_size": len(body),
            "body": body.decode("utf-8", errors="replace"),
            "headers": {
                header: _safe_header_value(self.headers.get(header))
                for header in SAFE_HEADERS
                if _safe_header_value(self.headers.get(header)) is not None
            },
        }
        try:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            with CAPTURE_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"accepted": False})
            return

        status = (
            RESPONSE_STATUS
            if MIN_HTTP_STATUS <= RESPONSE_STATUS <= MAX_HTTP_STATUS
            else int(HTTPStatus.ACCEPTED)
        )
        self._send_json(status, {"accepted": True})

    def _send_json(self, status: int | HTTPStatus, value: dict[str, object]) -> None:
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    """Start the local capture server on the container loopback interface."""

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 18080), CaptureHandler)
    except OSError as exc:
        sys.stderr.write(f"Unable to start HTTP capture server: {exc}\n")
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
