# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ssl
from typing import Any

import pytest

from nats_sinks.cli import main as cli_main
from nats_sinks.core.config import AppConfig
from nats_sinks.core.errors import ConfigurationError


def app_config(nats: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "ORDERS",
                "consumer": "oracle-orders-sink",
                "subject": "orders.*",
                **nats,
            },
            "sink": {
                "type": "oracle",
                "dsn": "localhost:1521/FREEPDB1",
                "user": "app_user",
                "password_env": "ORACLE_PASSWORD",
                "table": "NATS_SINK_EVENTS",
            },
        }
    )


def test_nats_options_resolve_username_password_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NATS_PASSWORD", "plain-client-secret")
    config = app_config({"user": "sink_user", "password_env": "NATS_PASSWORD"})

    options = cli_main._nats_options(config)

    assert options["user"] == "sink_user"
    assert options["password"] == "plain-client-secret"  # noqa: S105


def test_nats_options_allows_connection_without_authentication() -> None:
    config = app_config({})

    options = cli_main._nats_options(config)

    assert options["servers"] == ["nats://localhost:4222"]
    assert "user" not in options
    assert "password" not in options
    assert "token" not in options


def test_nats_options_resolve_token_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NATS_TOKEN", "client-token")
    config = app_config({"token_env": "NATS_TOKEN"})

    options = cli_main._nats_options(config)

    assert options["token"] == "client-token"  # noqa: S105


def test_nats_options_support_multiple_seed_urls_and_reconnect_tuning() -> None:
    config = app_config(
        {
            "urls": [
                "nats://nats-a.example:4222",
                "nats://nats-b.example:4222",
            ],
            "allow_reconnect": True,
            "connect_timeout_seconds": 7,
            "reconnect_time_wait_seconds": 3,
            "max_reconnect_attempts": -1,
            "ping_interval_seconds": 30,
            "max_outstanding_pings": 4,
            "pending_size_bytes": 4_194_304,
            "drain_timeout_seconds": 15,
        }
    )

    options = cli_main._nats_options(config)

    assert options["servers"] == [
        "nats://nats-a.example:4222",
        "nats://nats-b.example:4222",
    ]
    assert options["allow_reconnect"] is True
    assert options["connect_timeout"] == 7
    assert options["reconnect_time_wait"] == 3
    assert options["max_reconnect_attempts"] == -1
    assert options["ping_interval"] == 30
    assert options["max_outstanding_pings"] == 4
    assert options["pending_size"] == 4_194_304
    assert options["drain_timeout"] == 15


def test_nats_config_rejects_ambiguous_seed_urls() -> None:
    with pytest.raises(ValueError, match=r"nats\.urls"):
        app_config({"urls": ["nats://nats-a.example:4222", "   "]})


def test_nats_options_missing_secret_environment_variable_raises() -> None:
    config = app_config({"token_env": "MISSING_NATS_TOKEN"})

    with pytest.raises(ConfigurationError, match="MISSING_NATS_TOKEN"):
        cli_main._nats_options(config)


def test_nats_options_uses_local_ca_file_for_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeContext:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def load_cert_chain(self, *, certfile: str, keyfile: str | None = None) -> None:
            captured["certfile"] = certfile
            captured["keyfile"] = keyfile

    def fake_create_default_context(*, cafile: str | None = None) -> FakeContext:
        captured["cafile"] = cafile
        return FakeContext()

    monkeypatch.setattr(cli_main.ssl, "create_default_context", fake_create_default_context)
    config = app_config(
        {
            "url": "tls://nats.example:4222",
            "tls_ca_file": "/etc/nats/ca.crt",
            "tls_cert_file": "/etc/nats/client.crt",
            "tls_key_file": "/etc/nats/client.key",
        }
    )

    options = cli_main._nats_options(config)

    assert options["tls"].check_hostname is True
    assert captured == {
        "cafile": "/etc/nats/ca.crt",
        "certfile": "/etc/nats/client.crt",
        "keyfile": "/etc/nats/client.key",
    }
