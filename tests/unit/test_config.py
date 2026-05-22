# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nats_sinks.core.config import (
    MAX_CONFIG_BYTES,
    ConfigurationError,
    load_config,
    redacted_config,
)


def test_load_valid_config_with_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NATS_SINKS_NATS_URL", "tls://nats.example:4222")

    config = load_config(path)

    assert config.nats.url == "tls://nats.example:4222"
    assert config.delivery.ack_policy == "after_sink_commit"


def test_invalid_json_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('["not", "a", "mapping"]\n', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="root must be a mapping"):
        load_config(path)


def test_null_json_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("null\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="root must be a mapping"):
        load_config(path)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "stream": "ORDERS_DUPLICATE",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="duplicate JSON object key: stream"):
        load_config(path, env_overrides=False)


def test_oversized_config_is_rejected_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(" " * (MAX_CONFIG_BYTES + 1), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"exceeds the .* byte limit"):
        load_config(path, env_overrides=False)


def test_unknown_logging_level_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "logging": {
    "level": "TRACE"
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"logging\.level must be one of"):
        load_config(path, env_overrides=False)


def test_redacted_config_hides_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NATS_SINKS_NATS_URL", "nats://localhost:4222")
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "token": "super-secret"
  },
  "sink": {
    "type": "oracle",
    "dsn": "user/pass@db.example/FREEPDB1",
    "user": "app_user",
    "password": "direct-secret",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )

    rendered = redacted_config(load_config(path))

    assert rendered["nats"]["token"] == "********"  # noqa: S105
    assert rendered["sink"]["password"] == "********"  # noqa: S105
    assert rendered["sink"]["dsn"] == "********"
    assert os.environ["NATS_SINKS_NATS_URL"] == "nats://localhost:4222"


def test_redacted_config_hides_nats_url_credentials(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://token-value@nats.example:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )

    rendered = redacted_config(load_config(path, env_overrides=False))

    assert rendered["nats"]["url"] == "********"


def test_redacted_config_hides_oracle_wallet_password(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "oracle",
    "dsn": "mydb_low",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "wallet_location": ".local/oracle-adb/wallet",
    "wallet_password": "wallet-secret",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )

    rendered = redacted_config(load_config(path, env_overrides=False))

    assert rendered["sink"]["wallet_password"] == "********"  # noqa: S105
    assert rendered["sink"]["wallet_location"] == ".local/oracle-adb/wallet"


def test_nats_password_env_does_not_require_secret_during_load(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*",
    "user": "sink_user",
    "password_env": "NATS_PASSWORD"
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.nats.user == "sink_user"
    assert config.nats.password_env == "NATS_PASSWORD"  # noqa: S105


def test_logging_level_can_be_set_to_debug(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "oracle-orders-sink",
    "subject": "orders.*"
  },
  "logging": {
    "level": "DEBUG",
    "payload_logging": false
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.logging.level == "DEBUG"


def test_delivery_retry_backoff_controls_are_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "delivery": {
    "max_retries": 8,
    "retry_backoff_ms": 500,
    "retry_backoff_max_ms": 30000,
    "retry_backoff_mode": "exponential",
    "retry_backoff_multiplier": 2.5,
    "retry_jitter": "equal"
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.delivery.max_retries == 8
    assert config.delivery.retry_backoff_ms == 500
    assert config.delivery.retry_backoff_max_ms == 30_000
    assert config.delivery.retry_backoff_mode == "exponential"
    assert config.delivery.retry_backoff_multiplier == 2.5
    assert config.delivery.retry_jitter == "equal"


def test_delivery_retry_backoff_cap_must_cover_base_delay(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "delivery": {
    "retry_backoff_ms": 5000,
    "retry_backoff_max_ms": 1000
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="retry_backoff_max_ms"):
        load_config(path, env_overrides=False)


def test_dead_letter_ackterm_after_publish_is_explicit_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "dead_letter": {
    "enabled": true,
    "subject": "orders.dlq",
    "ack_term_after_publish": true
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.dead_letter.ack_term_after_publish is True


def test_delivery_priority_lanes_are_validated_and_normalized(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "delivery": {
    "priority_lanes": {
      "enabled": true,
      "default_lane": "Routine",
      "unknown_priority_action": "default_lane",
      "max_priority_value_length": 32,
      "lanes": [
        {
          "name": "Urgent",
          "priorities": ["URGENT", "immediate"],
          "weight": 3
        },
        {
          "name": "routine",
          "priorities": ["normal", "routine"],
          "weight": 1
        }
      ]
    }
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.delivery.priority_lanes.enabled is True
    assert config.delivery.priority_lanes.default_lane == "routine"
    assert config.delivery.priority_lanes.lanes[0].name == "urgent"
    assert config.delivery.priority_lanes.lanes[0].priorities == ("urgent", "immediate")
    assert config.delivery.priority_lanes.lanes[0].weight == 3


def test_delivery_priority_lanes_reject_ambiguous_priority_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "delivery": {
    "priority_lanes": {
      "enabled": true,
      "default_lane": "routine",
      "lanes": [
        {
          "name": "urgent",
          "priorities": ["urgent"],
          "weight": 3
        },
        {
          "name": "routine",
          "priorities": ["URGENT"],
          "weight": 1
        }
      ]
    }
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="assigned to both"):
        load_config(path, env_overrides=False)


def test_delivery_priority_lanes_reject_control_characters_in_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "delivery": {
    "priority_lanes": {
      "enabled": true,
      "default_lane": "routine",
      "lanes": [
        {
          "name": "urgent",
          "priorities": ["urgent\\tspoof"],
          "weight": 3
        },
        {
          "name": "routine",
          "priorities": ["routine"],
          "weight": 1
        }
      ]
    }
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="control characters"):
        load_config(path, env_overrides=False)


def test_oracle_payload_mode_can_be_configured_for_encrypted_text(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "SECURE",
    "consumer": "oracle-secure-sink",
    "subject": "secure.*"
  },
  "sink": {
    "type": "oracle",
    "dsn": "localhost:1521/FREEPDB1",
    "user": "app_user",
    "password_env": "ORACLE_PASSWORD",
    "table": "NATS_SINK_EVENTS",
    "payload_mode": "text_envelope"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.sink.payload_mode == "text_envelope"


def test_file_sink_config_loads_without_oracle_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events",
    "filename_strategy": "stream_sequence",
    "duplicate_policy": "skip_existing",
    "payload_mode": "json_or_envelope"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.sink.type == "file"
    assert config.sink.directory == "/var/lib/nats-sinks/events"


def test_encryption_config_loads_without_resolving_key_env(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "encryption": {
    "enabled": true,
    "algorithm": "AES-256-CCM",
    "key_id": "orders-test-key",
    "key_b64_env": "NATS_SINKS_PAYLOAD_KEY_B64"
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.encryption.enabled is True
    assert config.encryption.algorithm == "aes-256-ccm"
    assert config.encryption.key_b64_env == "NATS_SINKS_PAYLOAD_KEY_B64"


def test_subject_encryption_rules_load_and_redact_key_material(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": ">"
  },
  "encryption": {
    "enabled": false,
    "rules": [
      {
        "subject": "secure.>",
        "enabled": true,
        "algorithm": "AES_256_GCM",
        "key_id": "secure-key",
        "key_b64_env": "NATS_SINKS_SECURE_PAYLOAD_KEY_B64"
      },
      {
        "subject": "public.>",
        "enabled": false
      }
    ]
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)
    rendered = redacted_config(config)

    assert config.encryption.enabled is False
    assert config.encryption.rules[0].subject == "secure.>"
    assert config.encryption.rules[0].algorithm == "aes-256-gcm"
    assert config.encryption.rules[1].enabled is False
    assert rendered["encryption"]["rules"][0]["key_b64_env"] == "********"


def test_message_metadata_config_supports_headers_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "message_metadata": {
    "priority": {
      "header": "X-Event-Priority",
      "default": "normal"
    },
    "classification": {
      "header": "X-Data-Classification",
      "default": "internal"
    },
    "labels": {
      "header": "X-Event-Labels",
      "default": "customer-facing;billing"
    }
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.message_metadata.priority.header == "X-Event-Priority"
    assert config.message_metadata.priority.default == "normal"
    assert config.message_metadata.classification.header == "X-Data-Classification"
    assert config.message_metadata.classification.default == "internal"
    assert config.message_metadata.labels.header == "X-Event-Labels"
    assert config.message_metadata.labels.default == ("customer-facing", "billing")


def test_message_metadata_config_supports_subject_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": ">"
  },
  "message_metadata": {
    "priority": {
      "header": "X-Priority",
      "default": "normal"
    },
    "classification": {
      "header": "X-Classification",
      "default": "internal"
    },
    "labels": {
      "header": "X-Labels",
      "default": ["orders", "default"]
    },
    "rules": [
      {
        "subject": "orders.urgent.>",
        "priority": "urgent",
        "classification": "restricted",
        "labels": "urgent;customer-facing"
      },
      {
        "subject": "public.>",
        "classification": null,
        "labels": null
      }
    ]
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.message_metadata.priority_default_for_subject("orders.urgent.created") == "urgent"
    assert (
        config.message_metadata.classification_default_for_subject("orders.urgent.created")
        == "restricted"
    )
    assert config.message_metadata.priority_default_for_subject("public.status") == "normal"
    assert config.message_metadata.classification_default_for_subject("public.status") is None
    assert config.message_metadata.labels_default_for_subject("orders.urgent.created") == (
        "urgent",
        "customer-facing",
    )
    assert config.message_metadata.labels_default_for_subject("public.status") == ()
    assert config.message_metadata.labels_default_for_subject("orders.created") == (
        "orders",
        "default",
    )


def test_message_metadata_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*"
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NATS_SINKS_PRIORITY_DEFAULT", "normal")
    monkeypatch.setenv("NATS_SINKS_CLASSIFICATION_DEFAULT", "internal")
    monkeypatch.setenv("NATS_SINKS_LABELS_DEFAULT", "blue;green")

    config = load_config(path)

    assert config.message_metadata.priority.default == "normal"
    assert config.message_metadata.classification.default == "internal"
    assert config.message_metadata.labels.default == ("blue", "green")
