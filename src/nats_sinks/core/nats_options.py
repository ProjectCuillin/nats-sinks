# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Central NATS connection option construction.

The runner, CLI, and future operational tools all need to turn validated JSON
configuration into keyword arguments for `nats.connect`.  Keeping that logic in
one module avoids small security differences between entry points: every caller
gets the same authentication-mode validation, secret resolution timing, TLS
context handling, WebSocket header handling, and reconnect tuning.

This module deliberately returns plain `dict[str, object]` values because that
is the interface expected by `nats-py`.  The inputs, however, are always typed
`NatsConfig` objects that have already passed the strict configuration model.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any, Literal

from nats_sinks.core.config import NATS_WEBSOCKET_URL_SCHEMES, NatsConfig

NatsAuthMode = Literal[
    "none",
    "username_password",
    "token",
    "credentials_file",
    "nkey_seed_file",
]


@dataclass(frozen=True)
class NatsConnectionSummary:
    """Safe-to-log summary of how a NATS connection will be opened.

    The summary intentionally avoids secret values and local identity-material
    paths.  It is suitable for diagnostics, tests, and human-readable CLI
    output because it describes the selected authentication and transport mode
    without exposing passwords, tokens, seed files, credentials files, or client
    certificate locations.
    """

    auth_mode: NatsAuthMode
    tls_enabled: bool
    tls_verify: bool
    tls_client_certificate: bool
    websocket: bool
    seed_count: int


def nats_auth_mode(config: NatsConfig) -> NatsAuthMode:
    """Return the single validated authentication mode for a NATS connection."""

    if config.user is not None:
        return "username_password"
    if config.token is not None or config.token_env is not None:
        return "token"
    if config.creds_file is not None:
        return "credentials_file"
    if config.nkey_seed_file is not None:
        return "nkey_seed_file"
    return "none"


def nats_uses_websocket(config: NatsConfig) -> bool:
    """Return true when the configured seed URLs use NATS WebSocket transport."""

    return any(_url_uses_websocket(url) for url in _servers(config))


def nats_uses_tls(config: NatsConfig) -> bool:
    """Return true when NATS transport or TLS files require an SSL context."""

    return any(
        (
            config.tls_ca_file,
            config.tls_cert_file,
            config.tls_key_file,
            any(server.startswith("tls://") for server in _servers(config)),
            any(server.startswith("wss://") for server in _servers(config)),
        )
    )


def describe_nats_connection(config: NatsConfig) -> NatsConnectionSummary:
    """Build a redaction-safe connection summary for diagnostics and tests."""

    return NatsConnectionSummary(
        auth_mode=nats_auth_mode(config),
        tls_enabled=nats_uses_tls(config),
        tls_verify=config.tls_verify,
        tls_client_certificate=config.tls_cert_file is not None,
        websocket=nats_uses_websocket(config),
        seed_count=len(_servers(config)),
    )


def build_nats_tls_context(config: NatsConfig) -> ssl.SSLContext | None:
    """Build the optional TLS context used by `nats-py`.

    TLS verification is enabled by default in the configuration model.  If an
    operator explicitly disables verification for a lab or break-glass scenario,
    the change is kept visible in JSON and documented as unsafe for production.
    """

    if not nats_uses_tls(config):
        return None
    context = ssl.create_default_context(cafile=config.tls_ca_file)
    context.check_hostname = config.tls_verify
    if not config.tls_verify:
        context.verify_mode = ssl.CERT_NONE  # nosec B323 - explicit opt-in configuration.
    if config.tls_cert_file:
        context.load_cert_chain(
            certfile=config.tls_cert_file,
            keyfile=config.tls_key_file,
        )
    return context


def build_nats_connect_options(config: NatsConfig) -> dict[str, Any]:
    """Convert validated NATS config into `nats.connect` keyword arguments.

    Secrets are resolved only at this boundary.  That lets `validate` and
    `show-effective-config` operate without reading secret environment variables
    while `run` and live tests still receive the values required by `nats-py`.
    """

    password = config.resolve_password()
    token = config.resolve_token()
    websocket_headers = config.resolve_websocket_headers()
    options: dict[str, Any] = {
        key: value
        for key, value in {
            "servers": _servers(config),
            "user": config.user,
            "password": password,
            "token": token,
            "name": config.name,
            "user_credentials": config.creds_file,
            "nkeys_seed": config.nkey_seed_file,
            "no_echo": config.no_echo,
            "allow_reconnect": config.allow_reconnect,
            "connect_timeout": config.connect_timeout_seconds,
            "reconnect_time_wait": config.reconnect_time_wait_seconds,
            "max_reconnect_attempts": config.max_reconnect_attempts,
            "ping_interval": config.ping_interval_seconds,
            "max_outstanding_pings": config.max_outstanding_pings,
            "pending_size": config.pending_size_bytes,
            "drain_timeout": config.drain_timeout_seconds,
            "ws_connection_headers": websocket_headers or None,
        }.items()
        if value is not None
    }
    tls_context = build_nats_tls_context(config)
    if tls_context is not None:
        options["tls"] = tls_context
    return options


def _servers(config: NatsConfig) -> list[str]:
    """Return the effective seed URL list while preserving configured order."""

    return config.urls or [config.url]


def _url_uses_websocket(url: str) -> bool:
    """Return true when a validated NATS URL uses WebSocket transport."""

    return url.split(":", maxsplit=1)[0] in NATS_WEBSOCKET_URL_SCHEMES
