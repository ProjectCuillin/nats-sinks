#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Probe a live NATS server connection without committing secrets.

This script is intended for manual operator validation, not CI. It can verify:

* TLS connectivity using a local CA certificate,
* token or username/password authentication,
* subscribing to a subject,
* optionally publishing a test message and receiving it back.

Secrets should be supplied through environment variables or an ignored env file.
The script does not print secret values or message payloads by default.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import ssl
from pathlib import Path
from typing import Final

import nats

DEFAULT_TIMEOUT_SECONDS: Final[int] = 20
DEFAULT_PASSWORD_ENV: Final[str] = "NATS_PASSWORD"
DEFAULT_TOKEN_ENV: Final[str] = "NATS_TOKEN"


def parse_args() -> argparse.Namespace:
    """Parse command-line options for a live, opt-in NATS probe."""

    parser = argparse.ArgumentParser(
        description="Probe a live NATS server connection and optional message delivery."
    )
    parser.add_argument(
        "--server", required=True, help="NATS server URL, for example tls://host:4222"
    )
    parser.add_argument("--subject", required=True, help="Subject to subscribe to")
    parser.add_argument("--user", help="NATS username for username/password authentication")
    parser.add_argument(
        "--password-env",
        default=DEFAULT_PASSWORD_ENV,
        help=f"Environment variable containing the NATS password; default: {DEFAULT_PASSWORD_ENV}",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"Environment variable containing the NATS token; default: {DEFAULT_TOKEN_ENV}",
    )
    parser.add_argument(
        "--auth-mode",
        choices=("password", "token", "none"),
        default="password",
        help="Authentication mode to use for the probe",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional ignored KEY=VALUE env file to load before resolving secrets",
    )
    parser.add_argument("--ca-file", type=Path, help="Local CA certificate for TLS verification")
    parser.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="Disable TLS verification for short-lived local testing only",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for one message; default: {DEFAULT_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish a test message after the subscription is active",
    )
    parser.add_argument(
        "--message",
        default='{"probe":"nats-sinks","kind":"live-test"}',
        help="Payload to publish when --publish is set",
    )
    parser.add_argument(
        "--print-payload",
        action="store_true",
        help="Print received payload text; disabled by default because payloads may be sensitive",
    )
    return parser.parse_args()


def load_env_file(path: Path | None) -> None:
    """Load a simple KEY=VALUE env file without overriding existing variables."""

    if path is None:
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def resolve_secret(env_name: str, *, prompt: str) -> str:
    """Resolve a secret from an environment variable or prompt without echo."""

    value = os.getenv(env_name)
    if value:
        return value
    return getpass.getpass(prompt)


def build_tls_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    """Build an SSL context when TLS is requested by URL or certificate options."""

    if not args.server.startswith("tls://") and args.ca_file is None:
        return None
    context = ssl.create_default_context(cafile=str(args.ca_file) if args.ca_file else None)
    if args.no_tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


async def run_probe(args: argparse.Namespace) -> int:
    """Connect, subscribe, optionally publish, and wait for one message."""

    load_env_file(args.env_file)
    tls_context = build_tls_context(args)
    connect_options: dict[str, object] = {
        "tls": tls_context,
        "name": "nats-sinks-live-probe",
        "connect_timeout": 5,
        "allow_reconnect": False,
    }

    if args.auth_mode == "password":
        if not args.user:
            print("--user is required for password authentication")
            return 2
        connect_options["user"] = args.user
        connect_options["password"] = resolve_secret(
            args.password_env,
            prompt="NATS password: ",
        )
    elif args.auth_mode == "token":
        connect_options["token"] = resolve_secret(
            args.token_env,
            prompt="NATS token: ",
        )

    try:
        nc = await nats.connect(args.server, **connect_options)
    except Exception as exc:
        print(f"connect failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"connected: server={args.server} subject={args.subject}")
    received = asyncio.Event()

    async def handler(message: object) -> None:
        subject = getattr(message, "subject", "<unknown>")
        data = getattr(message, "data", b"")
        print(f"received: subject={subject} payload_bytes={len(data)}")
        if args.print_payload:
            print(data.decode("utf-8", errors="replace"))
        received.set()

    subscription = await nc.subscribe(args.subject, cb=handler)
    await nc.flush()
    print(f"subscribed: waiting up to {args.timeout}s for one message")

    if args.publish:
        payload = args.message.encode("utf-8")
        await nc.publish(args.subject, payload)
        await nc.flush()
        print(f"published: subject={args.subject} payload_bytes={len(payload)}")

    try:
        await asyncio.wait_for(received.wait(), timeout=args.timeout)
        status = 0
    except TimeoutError:
        print("no message received during wait window")
        status = 3
    finally:
        await subscription.unsubscribe()
        await nc.close()
        print("closed")
    return status


def main() -> int:
    """Entrypoint used when the script is run directly."""

    return asyncio.run(run_probe(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
