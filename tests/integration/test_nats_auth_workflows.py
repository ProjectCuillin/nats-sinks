# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Environment-gated NATS authentication workflow checks.

These tests are intentionally skipped during normal unit-test and smoke-test
runs.  They exist so maintainers can certify a specific NATS deployment profile
without committing credentials, certificates, seed files, or private endpoints
to the repository.
"""

from __future__ import annotations

import os
from typing import Any

import nats
import pytest

from nats_sinks.core.config import NatsConfig
from nats_sinks.core.nats_options import build_nats_connect_options, describe_nats_connection


def _nats_auth_integration_enabled() -> bool:
    """Return true when an operator explicitly enables live auth workflow tests."""

    return os.getenv("NATS_SINKS_NATS_AUTH_INTEGRATION") == "1"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _nats_auth_integration_enabled(),
        reason="set NATS_SINKS_NATS_AUTH_INTEGRATION=1 to run live NATS auth tests",
    ),
]


def _required_env(name: str) -> str:
    """Read a required environment variable without printing its value."""

    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _optional_env(name: str) -> str | None:
    """Read an optional environment variable without logging its value."""

    return os.getenv(name) or None


def _auth_config() -> NatsConfig:
    """Build NATS config for the selected live authentication workflow."""

    mode = _required_env("NATS_SINKS_AUTH_MODE")
    fields: dict[str, Any] = {
        "url": _required_env("NATS_SINKS_AUTH_URL"),
        "stream": os.getenv("NATS_SINKS_AUTH_STREAM", "NATS_SINKS_AUTH_TEST"),
        "consumer": os.getenv("NATS_SINKS_AUTH_CONSUMER", "nats-sinks-auth-test"),
        "subject": os.getenv("NATS_SINKS_AUTH_SUBJECT", "nats.sinks.auth.test"),
        "tls_ca_file": _optional_env("NATS_SINKS_AUTH_TLS_CA_FILE"),
        "tls_cert_file": _optional_env("NATS_SINKS_AUTH_TLS_CERT_FILE"),
        "tls_key_file": _optional_env("NATS_SINKS_AUTH_TLS_KEY_FILE"),
        "tls_verify": os.getenv("NATS_SINKS_AUTH_TLS_VERIFY", "true").lower()
        not in {"0", "false", "no"},
    }
    if mode == "none":
        pass
    elif mode == "username_password":
        fields["user"] = _required_env("NATS_SINKS_AUTH_USER")
        fields["password_env"] = "NATS_SINKS_AUTH_PASSWORD"  # noqa: S105 - environment name.
        _required_env("NATS_SINKS_AUTH_PASSWORD")
    elif mode == "token":
        fields["token_env"] = "NATS_SINKS_AUTH_TOKEN"  # noqa: S105 - environment name.
        _required_env("NATS_SINKS_AUTH_TOKEN")
    elif mode == "credentials_file":
        fields["creds_file"] = _required_env("NATS_SINKS_AUTH_CREDS_FILE")
    elif mode == "nkey_seed_file":
        fields["nkey_seed_file"] = _required_env("NATS_SINKS_AUTH_NKEY_SEED_FILE")
    elif mode == "tls_client_certificate":
        fields["tls_cert_file"] = _required_env("NATS_SINKS_AUTH_TLS_CERT_FILE")
        fields["tls_key_file"] = _required_env("NATS_SINKS_AUTH_TLS_KEY_FILE")
    else:
        pytest.skip(
            "NATS_SINKS_AUTH_MODE must be one of none, username_password, token, "
            "credentials_file, nkey_seed_file, or tls_client_certificate"
        )
    return NatsConfig.model_validate(fields)


@pytest.mark.asyncio
async def test_live_nats_authentication_workflow_connects() -> None:
    """Connect to a live NATS server using the selected certified auth workflow."""

    config = _auth_config()
    summary = describe_nats_connection(config)
    options = build_nats_connect_options(config)

    assert summary.auth_mode in {
        "none",
        "username_password",
        "token",
        "credentials_file",
        "nkey_seed_file",
    }

    client = await nats.connect(**options)
    try:
        assert client.is_connected
    finally:
        await client.drain()
