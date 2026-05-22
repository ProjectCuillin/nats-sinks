# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nats_sinks.core.config import (
    MAX_CONFIG_BYTES,
    ConfigurationError,
    SinkPluginConfig,
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
    assert config.nats.no_echo is False


def test_nats_no_echo_can_be_enabled_with_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NATS_SINKS_NATS_NO_ECHO", "true")

    config = load_config(path)

    assert config.nats.no_echo is True


def test_sink_plugin_config_defaults_to_disabled() -> None:
    config = SinkPluginConfig()

    assert config.enabled is False
    assert config.allowed_sinks == ()
    assert config.require_production_ready is True


def test_sink_plugin_config_normalizes_explicit_allow_list() -> None:
    config = SinkPluginConfig(enabled=True, allowed_sinks=["  ACME_File  ", "acme-http"])

    assert config.allowed_sinks == ("acme_file", "acme-http")


def test_sink_plugin_config_requires_allow_list_when_enabled() -> None:
    with pytest.raises(ValueError, match="requires at least one allowed sink name"):
        SinkPluginConfig(enabled=True)


def test_sink_plugin_config_rejects_duplicate_or_unsafe_names() -> None:
    with pytest.raises(ValueError, match="duplicate sink name"):
        SinkPluginConfig(allowed_sinks=["acme", "ACME"])

    with pytest.raises(ValueError, match="entries must start"):
        SinkPluginConfig(allowed_sinks=["../acme"])


def test_load_config_accepts_disabled_plugin_section(tmp_path: Path) -> None:
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
  "plugins": {
    "enabled": false,
    "allowed_sinks": [],
    "require_production_ready": true
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

    assert config.plugins.enabled is False
    assert config.plugins.allowed_sinks == ()


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


def test_nats_url_credentials_are_rejected(tmp_path: Path) -> None:
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

    with pytest.raises(ConfigurationError, match="must not include credentials"):
        load_config(path, env_overrides=False)


def test_redacted_config_hides_websocket_header_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "wss://nats.example:8443",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.*",
    "websocket_headers": {
      "X-Route-Hint": "approved-edge"
    },
    "websocket_headers_env": {
      "Authorization": "NATS_WS_AUTHORIZATION"
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

    rendered = redacted_config(load_config(path, env_overrides=False))

    assert rendered["nats"]["websocket_headers"]["X-Route-Hint"] == "********"
    assert rendered["nats"]["websocket_headers_env"]["Authorization"] == "********"


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


def test_custody_config_can_be_enabled_with_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NATS_SINKS_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NATS_SINKS_CUSTODY_ALGORITHM", "sha512")

    config = load_config(path)

    assert config.custody.enabled is True
    assert config.custody.algorithm == "sha512"


def test_enabled_custody_requires_at_least_one_hash(tmp_path: Path) -> None:
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
  "custody": {
    "enabled": true,
    "hash_payload": false,
    "hash_metadata": false
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="hash_payload or hash_metadata"):
        load_config(path, env_overrides=False)


def test_advisory_config_is_disabled_by_default(tmp_path: Path) -> None:
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
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    config = load_config(path, env_overrides=False)

    assert config.advisories.enabled is False
    assert "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*" in config.advisories.subjects


def test_advisory_config_can_be_enabled_with_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NATS_SINKS_ADVISORIES_ENABLED", "true")

    config = load_config(path)

    assert config.advisories.enabled is True


def test_advisory_config_rejects_non_advisory_subjects(tmp_path: Path) -> None:
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
  "advisories": {
    "enabled": true,
    "subjects": ["orders.*"]
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="advisory subject"):
        load_config(path, env_overrides=False)


def test_advisory_config_rejects_duplicate_subjects(tmp_path: Path) -> None:
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
  "advisories": {
    "enabled": true,
    "subjects": [
      "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*",
      "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.*.*"
    ]
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="duplicate"):
        load_config(path, env_overrides=False)


def test_pre_sink_policy_config_validates_subject_rules(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*"
  },
  "pre_sink_policy": {
    "enabled": true,
    "rules": [
      {
        "subject": "orders.*",
        "require_priority": true,
        "require_classification": true,
        "required_labels": "orders;audit",
        "require_mission_metadata": true,
        "allowed_mission_metadata_keys": ["profile", "phase"],
        "max_payload_bytes": 1048576
      }
    ]
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

    assert config.pre_sink_policy.enabled
    assert config.pre_sink_policy.rules[0].subject == "orders.*"
    assert config.pre_sink_policy.rules[0].required_labels == ("orders", "audit")


def test_size_policy_config_loads_documented_bounds(tmp_path: Path) -> None:
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
  "size_policy": {
    "enabled": true,
    "max_payload_bytes": 1024,
    "max_header_count": 16,
    "max_header_name_bytes": 128,
    "max_header_value_bytes": 512,
    "max_headers_bytes": 4096,
    "max_label_count": 8,
    "max_label_bytes": 64,
    "max_labels_bytes": 512,
    "max_mission_metadata_bytes": 2048,
    "max_standard_metadata_bytes": 8192,
    "max_normalized_record_bytes": 16384,
    "max_batch_messages": 32
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

    assert config.size_policy.enabled is True
    assert config.size_policy.max_payload_bytes == 1024
    assert config.size_policy.max_batch_messages == 32


def test_size_policy_rejects_inconsistent_aggregate_limits(tmp_path: Path) -> None:
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
  "size_policy": {
    "enabled": true,
    "max_payload_bytes": 1024,
    "max_normalized_record_bytes": 512
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"size_policy\.max_normalized_record_bytes"):
        load_config(path, env_overrides=False)


def test_enabled_pre_sink_policy_requires_explicit_rules(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*"
  },
  "pre_sink_policy": {
    "enabled": true
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"pre_sink_policy\.enabled requires"):
        load_config(path, env_overrides=False)


def test_pre_sink_policy_rejects_noop_and_secret_like_key_rules(tmp_path: Path) -> None:
    noop_path = tmp_path / "noop.json"
    noop_path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*"
  },
  "pre_sink_policy": {
    "enabled": true,
    "rules": [{"subject": "orders.*"}]
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )
    secret_key_path = tmp_path / "secret-key.json"
    secret_key_path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "orders-file-sink",
    "subject": "orders.*"
  },
  "pre_sink_policy": {
    "enabled": true,
    "rules": [
      {
        "subject": "orders.*",
        "allowed_mission_metadata_keys": ["profile", "api_key"]
      }
    ]
  },
  "sink": {
    "type": "file",
    "directory": "/tmp/nats-sinks-test"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="at least one check"):
        load_config(noop_path, env_overrides=False)
    with pytest.raises(ConfigurationError, match="secret-like names"):
        load_config(secret_key_path, env_overrides=False)


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


def test_consumer_management_defaults_to_create_if_missing(tmp_path: Path) -> None:
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

    config = load_config(path, env_overrides=False)

    assert config.consumer_management.mode == "create_if_missing"
    assert config.consumer_management.filter_subjects == ()
    assert config.consumer_management.deliver_policy == "all"
    assert config.consumer_management.replay_policy == "instant"
    assert config.consumer_management.backoff_seconds is None
    assert config.consumer_management.headers_only is None
    assert config.consumer_management.metadata == {}


def test_consumer_management_rejects_unknown_mode(tmp_path: Path) -> None:
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
  "consumer_management": {
    "mode": "unsafe-update"
  },
  "sink": {
    "type": "file",
    "directory": "/var/lib/nats-sinks/events"
  }
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_config(path, env_overrides=False)


def test_consumer_management_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.setenv("NATS_SINKS_CONSUMER_MANAGEMENT_MODE", "bind_only")

    config = load_config(path)

    assert config.consumer_management.mode == "bind_only"


def test_consumer_management_loads_richer_policy_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        """
{
  "nats": {
    "url": "nats://localhost:4222",
    "stream": "ORDERS",
    "consumer": "file-orders-sink",
    "subject": "orders.>"
  },
  "consumer_management": {
    "mode": "create_if_missing",
    "filter_subjects": ["orders.created", "orders.updated"],
    "max_deliver": 5,
    "backoff_seconds": [1, 5, 30],
    "max_ack_pending": 500,
    "max_waiting": 64,
    "headers_only": true,
    "num_replicas": 3,
    "memory_storage": true,
    "metadata": {
      "component": "nats-sinks",
      "purpose": "sink-worker"
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

    assert config.consumer_management.filter_subjects == ("orders.created", "orders.updated")
    assert config.consumer_management.backoff_seconds == (1.0, 5.0, 30.0)
    assert config.consumer_management.max_deliver == 5
    assert config.consumer_management.max_ack_pending == 500
    assert config.consumer_management.max_waiting == 64
    assert config.consumer_management.headers_only is True
    assert config.consumer_management.num_replicas == 3
    assert config.consumer_management.memory_storage is True
    assert config.consumer_management.metadata == {
        "component": "nats-sinks",
        "purpose": "sink-worker",
    }


@pytest.mark.parametrize(
    ("consumer_management", "expected"),
    [
        ({"filter_subjects": ["orders.*", "orders.*"]}, "duplicate"),
        ({"backoff_seconds": [1, 5, 30]}, "max_deliver"),
        (
            {"max_deliver": 5, "ack_wait_seconds": 30, "backoff_seconds": [1, 5]},
            "overrides AckWait",
        ),
        ({"max_deliver": 2, "backoff_seconds": [1, 5, 30]}, "length"),
        ({"metadata": {"secret_token": "value"}}, "secret"),
    ],
)
def test_consumer_management_rejects_unsafe_policy_values(
    consumer_management: dict[str, object],
    expected: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "nats": {
                    "url": "nats://localhost:4222",
                    "stream": "ORDERS",
                    "consumer": "file-orders-sink",
                    "subject": "orders.*",
                },
                "consumer_management": consumer_management,
                "sink": {
                    "type": "file",
                    "directory": "/var/lib/nats-sinks/events",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=expected):
        load_config(path, env_overrides=False)
