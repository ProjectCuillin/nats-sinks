# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for named multi-sink configuration.

The named sink registry is a configuration boundary, not a delivery engine by
itself. These tests keep that boundary strict: route targets must resolve to
declared sink instances, destination-specific config is validated by the CLI,
and redacted output must never expose credentials from any named instance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nats_sinks.cli.main import app
from nats_sinks.core.config import ConfigurationError, load_config, redacted_config


def _base_config(tmp_path: Path) -> dict[str, object]:
    return {
        "nats": {
            "url": "nats://localhost:4222",
            "stream": "MISSION",
            "consumer": "named-sink-test",
            "subject": "mission.>",
        },
        "sink": {
            "type": "file",
            "directory": str(tmp_path / "active"),
            "fsync": False,
        },
    }


def _write_config(tmp_path: Path, config: dict[str, object]) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_load_config_accepts_mixed_named_sinks_and_validates_route_references(
    tmp_path: Path,
) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "oracle_secret": {
            "type": "oracle",
            "dsn": "tcps://adb.example.invalid/secret",
            "user": "app_secret",
            "password_env": "ORACLE_SECRET_PASSWORD",
            "table": "NATS_SECRET_EVENTS",
        },
        "oracle_unclass": {
            "type": "oracle",
            "dsn": "tcps://adb.example.invalid/unclass",
            "user": "app_unclass",
            "password_env": "ORACLE_UNCLASS_PASSWORD",
            "table": "NATS_UNCLASS_EVENTS",
        },
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        },
    }
    config["routing"] = {
        "enabled": True,
        "mode": "first",
        "routes": [
            {
                "name": "nato_secret_sensor_audit",
                "match": {
                    "subject": "mission.sensor.>",
                    "classification": ["NATO SECRET"],
                    "labels_all": ["sensor", "audit"],
                },
                "targets": [
                    "oracle_secret",
                    {"sink": "file_audit", "required": False},
                ],
            },
            {
                "name": "nato_unclass_sensor_audit",
                "match": {
                    "subject": "mission.sensor.>",
                    "classification": ["NATO UNCLASS"],
                    "labels_all": ["sensor", "audit"],
                },
                "targets": ["oracle_unclass"],
            },
        ],
    }

    loaded = load_config(_write_config(tmp_path, config), env_overrides=False)

    assert tuple(loaded.sinks) == ("oracle_secret", "oracle_unclass", "file_audit")
    assert loaded.routing.target_names() == (
        "oracle_secret",
        "file_audit",
        "oracle_unclass",
    )
    assert loaded.routing.target_sink_types["oracle_secret"] == "oracle"  # noqa: S105
    assert loaded.routing.target_sink_types["file_audit"] == "file"
    assert loaded.routing.routes[0].targets[1].minimum_wait_ms == 100
    assert loaded.routing.routes[0].targets[1].timeout_ms == 1000


def test_load_config_accepts_two_file_destinations_and_two_oracle_tables(
    tmp_path: Path,
) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_mission_audit": {
            "type": "file",
            "directory": str(tmp_path / "mission-audit"),
            "fsync": False,
        },
        "file_replay_buffer": {
            "type": "file",
            "directory": str(tmp_path / "replay-buffer"),
            "fsync": False,
            "compression": "gzip",
        },
        "oracle_secret_table": {
            "type": "oracle",
            "dsn": "tcps://adb.example.invalid/secret",
            "user": "app_secret",
            "password_env": "ORACLE_SECRET_PASSWORD",
            "table": "NATS_SECRET_EVENTS",
        },
        "oracle_secret_audit_table": {
            "type": "oracle",
            "dsn": "tcps://adb.example.invalid/secret",
            "user": "app_secret",
            "password_env": "ORACLE_SECRET_PASSWORD",
            "table": "NATS_SECRET_AUDIT_EVENTS",
        },
    }

    loaded = load_config(_write_config(tmp_path, config), env_overrides=False)

    assert loaded.sinks["file_replay_buffer"].type == "file"
    assert loaded.sinks["oracle_secret_audit_table"].type == "oracle"


def test_load_config_rejects_unknown_route_named_sink(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        }
    }
    config["routing"] = {
        "enabled": True,
        "routes": [
            {
                "name": "route_alpha",
                "match": {"subject": "mission.>"},
                "targets": ["file_audit", "oracle_missing"],
            }
        ],
    }

    with pytest.raises(ConfigurationError, match="unknown named sink\\(s\\): oracle_missing"):
        load_config(_write_config(tmp_path, config), env_overrides=False)


def test_load_config_rejects_routing_type_mismatch_for_named_sink(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        }
    }
    config["routing"] = {
        "enabled": True,
        "target_sink_types": {"file_audit": "oracle"},
        "routes": [
            {
                "name": "route_alpha",
                "match": {"subject": "mission.>"},
                "targets": ["file_audit"],
            }
        ],
    }

    with pytest.raises(ConfigurationError, match="configured as file, routed as oracle"):
        load_config(_write_config(tmp_path, config), env_overrides=False)


def test_load_config_rejects_invalid_named_sink_names(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "../file": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        }
    }

    with pytest.raises(ConfigurationError, match="sinks name"):
        load_config(_write_config(tmp_path, config), env_overrides=False)


def test_duplicate_named_sink_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        f"""
{{
  "nats": {{
    "url": "nats://localhost:4222",
    "stream": "MISSION",
    "consumer": "named-sink-test",
    "subject": "mission.>"
  }},
  "sink": {{
    "type": "file",
    "directory": "{tmp_path / "active"}",
    "fsync": false
  }},
  "sinks": {{
    "file_audit": {{
      "type": "file",
      "directory": "{tmp_path / "audit-a"}"
    }},
    "file_audit": {{
      "type": "file",
      "directory": "{tmp_path / "audit-b"}"
    }}
  }}
}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="duplicate JSON object key: file_audit"):
        load_config(path, env_overrides=False)


def test_redacted_config_hides_named_sink_secrets(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "oracle_secret": {
            "type": "oracle",
            "dsn": "user/pass@db.example.invalid/service",
            "user": "app_secret",
            "password": "direct-secret",
            "wallet_password": "wallet-secret",
            "wallet_location": "/etc/nats-sinks/wallet",
            "table": "NATS_SECRET_EVENTS",
        }
    }

    rendered = redacted_config(load_config(_write_config(tmp_path, config), env_overrides=False))

    assert rendered["sinks"]["oracle_secret"]["dsn"] == "********"
    assert rendered["sinks"]["oracle_secret"]["password"] == "********"  # noqa: S105
    assert rendered["sinks"]["oracle_secret"]["wallet_password"] == "********"  # noqa: S105


def test_cli_validate_reports_named_sinks_and_routes(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        },
        "file_replay": {
            "type": "file",
            "directory": str(tmp_path / "replay"),
            "fsync": False,
        },
    }
    config["routing"] = {
        "enabled": True,
        "mode": "all",
        "routes": [
            {
                "name": "route_alpha",
                "match": {"subject": "mission.>"},
                "targets": ["file_audit", "file_replay"],
            }
        ],
    }

    result = CliRunner().invoke(app, ["validate", str(_write_config(tmp_path, config))])

    assert result.exit_code == 0
    assert "Named sinks: file_audit (file), file_replay (file)" in result.output
    assert "route_alpha: file_audit (required), file_replay (required)" in result.output


def test_cli_validates_named_multi_sink_example() -> None:
    config = Path(__file__).resolve().parents[2] / "examples/named-multi-sink/config.json"

    result = CliRunner().invoke(app, ["validate", str(config)])

    assert result.exit_code == 0
    assert "Named sinks: file_audit (file), oracle_secret (oracle), oracle_unclass (oracle)" in (
        result.output
    )
    assert "nato_secret_sensor_audit" in result.output
    assert "nato_unclass_sensor_audit" in result.output


def test_cli_validate_runs_sink_specific_validation_for_named_sinks(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "fsync": False,
        }
    }

    result = CliRunner().invoke(app, ["validate", str(_write_config(tmp_path, config))])

    assert result.exit_code == 2
    assert "sinks.file_audit" in result.output
    assert "directory" in result.output


def test_cli_test_sink_can_healthcheck_named_file_sink(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    named_dir = tmp_path / "audit"
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(named_dir),
            "fsync": False,
        }
    }

    result = CliRunner().invoke(
        app,
        ["test-sink", str(_write_config(tmp_path, config)), "--sink-name", "file_audit"],
    )

    assert result.exit_code == 0
    assert "Named sink selected: file_audit (file)" in result.output
    assert "Sink test succeeded for file_audit." in result.output
    assert named_dir.is_dir()


def test_cli_test_sink_can_healthcheck_all_named_file_sinks(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["sinks"] = {
        "file_audit": {
            "type": "file",
            "directory": str(tmp_path / "audit"),
            "fsync": False,
        },
        "file_replay": {
            "type": "file",
            "directory": str(tmp_path / "replay"),
            "fsync": False,
        },
    }

    result = CliRunner().invoke(
        app,
        ["test-sink", str(_write_config(tmp_path, config)), "--all-named-sinks"],
    )

    assert result.exit_code == 0
    assert "Named sinks selected: file_audit, file_replay" in result.output
    assert "Sink test succeeded for file_audit." in result.output
    assert "Sink test succeeded for file_replay." in result.output
