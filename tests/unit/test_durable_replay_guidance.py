# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

DOCS_DIR = Path("docs")
REPLAY_DOC = DOCS_DIR / "durable-replay-to-sinks.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def test_durable_replay_guidance_is_discoverable() -> None:
    content = _read(REPLAY_DOC)
    mkdocs_config = _read(Path("mkdocs.yml"))
    ordered_evaluation = _read(DOCS_DIR / "ordered-consumer-evaluation.md")
    operations = _read(DOCS_DIR / "operations.md")
    sink_framework = _read(DOCS_DIR / "sink-framework.md")
    testing = _read(DOCS_DIR / "testing.md")

    assert "# Durable Replay To Sinks" in content
    assert "Durable Replay To Sinks: durable-replay-to-sinks.md" in mkdocs_config
    assert "(durable-replay-to-sinks.md)" in ordered_evaluation
    assert "(durable-replay-to-sinks.md)" in operations
    assert "(durable-replay-to-sinks.md)" in sink_framework
    assert "(durable-replay-to-sinks.md)" in testing


def test_durable_replay_guidance_keeps_delivery_semantics_explicit() -> None:
    content = _read(REPLAY_DOC)

    assert "use a durable pull consumer for sink writes" in content
    assert "never use an ordered consumer for production sink writes" in content
    assert "never ACK before durable sink success" in content
    assert "commit-then-acknowledge" in content
    assert "at-least-once semantics" in content
    assert "idempotency strategy" in content


def test_durable_replay_guidance_documents_required_boundaries() -> None:
    content = _read(REPLAY_DOC)

    for term in (
        "`stream`",
        "`subject_filter` or `subject_filters`",
        "`durable_consumer`",
        "`start_sequence` or `start_time`",
        "`max_messages`",
        "`batch_size`",
        "`dry_run`",
        "`report_file`",
    ):
        assert term in content

    assert "Configure one, not both" in content
    assert "Prevents accidental unbounded scans or writes" in content


def test_durable_replay_guidance_documents_security_and_reporting() -> None:
    content = _read(REPLAY_DOC)
    normalized = _normalized(content)

    assert "redacted reports by default" in content
    assert "least-privilege NATS permissions" in content
    assert "must not include message payloads" in normalized
    assert "credentials" in content
    assert "connection strings" in normalized
    assert "Oracle wallet details" in content
    assert "sensitive subject names" in content


def test_durable_replay_guidance_covers_sink_reviews_and_future_tests() -> None:
    content = _read(REPLAY_DOC)

    for sink_name in (
        "Oracle Database",
        "Oracle MySQL",
        "File sink",
        "Edge spool sink",
        "Fan-out sink",
        "Future sinks",
    ):
        assert sink_name in content

    for test_expectation in (
        "configuration validation rejects",
        "dry run does not instantiate or call a sink",
        "no early ACK is possible when a sink write fails",
        "idempotent duplicate replay",
        "DLQ-before-ACK behavior is preserved",
        "reports are valid JSON or Markdown, bounded, and redacted",
    ):
        assert test_expectation in content
