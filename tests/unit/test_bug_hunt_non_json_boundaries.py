# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any, ClassVar

import pytest

from nats_sinks.cli import main as cli_main
from nats_sinks.core.config import AppConfig
from nats_sinks.core.consumer import envelope_from_nats_message
from nats_sinks.core.errors import ValidationError
from nats_sinks.core.retry import RetryPolicy
from nats_sinks.oracle.config import OracleIdempotencyConfig
from nats_sinks.oracle.idempotency import extract_payload_field


def app_config(nats: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(
        {
            "nats": {
                "url": "nats://localhost:4222",
                "stream": "ORDERS",
                "consumer": "orders-sink",
                "subject": "orders.*",
                **nats,
            },
            "sink": {
                "type": "file",
                "directory": str(Path("nats-sinks-test-output")),
            },
        }
    )


def _url(scheme: str, host: str = "nats.example.invalid") -> str:
    """Build test URLs without embedding public issue-blocked URL literals."""

    return f"{scheme}://{host}:4222"


def test_nats_config_rejects_unsupported_primary_url_scheme() -> None:
    with pytest.raises(ValueError, match=r"nats\.url"):
        app_config({"url": _url("http")})


def test_nats_config_rejects_unsupported_seed_url_scheme() -> None:
    with pytest.raises(ValueError, match=r"nats\.urls"):
        app_config({"urls": [_url("nats", "nats-a.example.invalid"), _url("ftp")]})


def test_nats_config_rejects_password_without_username() -> None:
    with pytest.raises(ValueError, match=r"nats\.user is required"):
        app_config({"password_env": "NATS_PASSWORD"})


def test_nats_config_rejects_token_combined_with_user_password() -> None:
    with pytest.raises(ValueError, match="single NATS authentication"):
        app_config(
            {
                "user": "nats_app",
                "password_env": "NATS_PASSWORD",
                "token_env": "NATS_TOKEN",
            }
        )


def test_nats_config_rejects_creds_file_combined_with_token() -> None:
    with pytest.raises(ValueError, match="single NATS authentication"):
        app_config(
            {
                "creds_file": "/etc/nats/user.creds",
                "token_env": "NATS_TOKEN",
            }
        )


def test_nats_options_builds_tls_context_for_tls_seed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeContext:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

    def fake_create_default_context(*, cafile: str | None = None) -> FakeContext:
        captured["cafile"] = cafile
        return FakeContext()

    monkeypatch.setattr(cli_main.ssl, "create_default_context", fake_create_default_context)
    config = app_config({"urls": [_url("tls", "nats-a.example.invalid")]})

    options = cli_main._nats_options(config)

    assert options["servers"] == [_url("tls", "nats-a.example.invalid")]
    assert isinstance(options["tls"], FakeContext)
    assert captured == {"cafile": None}


def test_retry_policy_rejects_negative_max_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_retries=-1)


def test_retry_policy_rejects_negative_backoff_values() -> None:
    with pytest.raises(ValueError, match="backoff_ms"):
        RetryPolicy(backoff_ms=-1)


def test_retry_policy_rejects_unknown_runtime_modes() -> None:
    with pytest.raises(ValueError, match="backoff_mode"):
        RetryPolicy(backoff_mode="quadratic")  # type: ignore[arg-type]


def test_retry_policy_caps_extreme_exponential_attempts_without_overflow() -> None:
    policy = RetryPolicy(
        backoff_ms=1000,
        max_backoff_ms=5000,
        backoff_multiplier=10.0,
        jitter="none",
    )

    assert policy.backoff_seconds(10_000) == 5.0


def test_oracle_payload_field_rejects_empty_path_segments() -> None:
    with pytest.raises(ValueError, match="payload_field"):
        OracleIdempotencyConfig(strategy="payload_field", payload_field="order..id")


def test_oracle_payload_field_rejects_structured_values() -> None:
    with pytest.raises(ValidationError, match="must resolve to a scalar"):
        extract_payload_field({"order": {"id": {"nested": "value"}}}, "order.id")


def test_consumer_ignores_negative_jetstream_metadata_values() -> None:
    class Sequence:
        stream = -1
        consumer = "-2"

    class Metadata:
        sequence = Sequence()
        stream = "ORDERS"
        consumer = "consumer"
        num_delivered = "-4"
        num_pending = -3

    class RawMessage:
        subject = "orders.created"
        data = b"{}"
        headers: ClassVar[dict[str, str]] = {}
        metadata = Metadata()

    envelope = envelope_from_nats_message(RawMessage())

    assert envelope.stream_sequence is None
    assert envelope.consumer_sequence is None
    assert envelope.pending is None
    assert envelope.redelivered is None
