# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Small metrics abstraction for production-safe instrumentation.

The runner records events through a tiny protocol instead of binding the core
package to a metrics backend.  This keeps the package suitable for libraries,
CLIs, containers, and embedded services that may already use Prometheus,
OpenTelemetry, StatsD, or another telemetry stack.

`InMemoryMetrics` is useful for tests and local embedding.  `NoopMetrics` is
the default when metrics are disabled.  Future exporters should implement the
same protocol while preserving metric names documented in the operations guide.

The constants in this module are the compatibility contract for runtime metric
names.  They intentionally avoid high-cardinality labels.  Operators can add
labels in an exporter, but the core runner emits simple counters, observations,
and gauges so instrumentation never changes ACK ordering or sink behavior.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast

DEFAULT_METRIC_NAMESPACE = "nats_sinks"
METRICS_SNAPSHOT_SCHEMA = "nats_sinks.metrics.snapshot.v1"
MAX_METRICS_SNAPSHOT_BYTES = 1_048_576
METRIC_NAMESPACE_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")


MetricKind = Literal["counter", "histogram", "gauge"]
MetricRowKind = Literal["counter", "gauge", "observation"]


class _NonStandardJsonConstantError(ValueError):
    """Raised when a metrics snapshot uses Python JSON extensions."""


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """Document one metric emitted by the core runner.

    The suffix is the name passed to a `MetricsRecorder`.  Exporters that need
    globally unique names should qualify it with the configured namespace, for
    example `nats_sinks_messages_fetched_total`.
    """

    name: str
    kind: MetricKind
    description: str

    def qualified_name(self, namespace: str = DEFAULT_METRIC_NAMESPACE) -> str:
        """Return the conventional exported name for this metric."""

        return qualified_metric_name(self.name, namespace=namespace)


class MetricNames:
    """Stable metric-name constants used by the runner and tests."""

    MESSAGES_FETCHED_TOTAL = "messages_fetched_total"
    MESSAGES_PREPARED_TOTAL = "messages_prepared_total"
    MESSAGES_WRITTEN_TOTAL = "messages_written_total"
    MESSAGES_ACKED_TOTAL = "messages_acked_total"
    MESSAGES_TERMINATED_TOTAL = "messages_terminated_total"
    MESSAGES_NACKED_TOTAL = "messages_nacked_total"
    MESSAGES_FAILED_TOTAL = "messages_failed_total"
    MESSAGES_DLQ_TOTAL = "messages_dlq_total"
    BATCHES_FETCHED_TOTAL = "batches_fetched_total"
    NATS_FETCH_SECONDS = "nats_fetch_seconds"
    MESSAGE_MAPPING_SECONDS = "message_mapping_seconds"
    SINK_BATCHES_WRITTEN_TOTAL = "sink_batches_written_total"
    SINK_BATCH_WRITE_SECONDS = "sink_batch_write_seconds"
    ORACLE_EXECUTE_SECONDS = "oracle_execute_seconds"
    ORACLE_COMMIT_SECONDS = "oracle_commit_seconds"
    MESSAGE_ACK_SECONDS = "message_ack_seconds"
    MESSAGE_TERM_SECONDS = "message_term_seconds"
    RETRY_BACKOFF_DELAY_SECONDS = "retry_backoff_delay_seconds"
    SINK_WRITE_ERRORS_TOTAL = "sink_write_errors_total"
    MESSAGE_NORMALIZATION_ERRORS_TOTAL = "message_normalization_errors_total"
    PAYLOAD_ENCRYPTION_ERRORS_TOTAL = "payload_encryption_errors_total"
    DLQ_PUBLISH_ERRORS_TOTAL = "dlq_publish_errors_total"
    ACK_ERRORS_TOTAL = "ack_errors_total"
    TERM_ERRORS_TOTAL = "term_errors_total"
    NATS_CONNECTION_DISCONNECTED_TOTAL = "nats_connection_disconnected_total"
    NATS_CONNECTION_RECONNECTED_TOTAL = "nats_connection_reconnected_total"
    NATS_CONNECTION_CLOSED_TOTAL = "nats_connection_closed_total"
    NATS_DISCOVERED_SERVERS_TOTAL = "nats_discovered_servers_total"
    NATS_ASYNC_ERRORS_TOTAL = "nats_async_errors_total"
    JETSTREAM_ADVISORIES_RECEIVED_TOTAL = "jetstream_advisories_received_total"
    JETSTREAM_ADVISORIES_FILTERED_TOTAL = "jetstream_advisories_filtered_total"
    JETSTREAM_ADVISORY_PARSE_ERRORS_TOTAL = "jetstream_advisory_parse_errors_total"
    JETSTREAM_ADVISORY_UNSUPPORTED_TOTAL = "jetstream_advisory_unsupported_total"
    JETSTREAM_ADVISORY_MAX_DELIVER_TOTAL = "jetstream_advisory_max_deliver_total"
    JETSTREAM_ADVISORY_NAK_TOTAL = "jetstream_advisory_nak_total"
    JETSTREAM_ADVISORY_TERMINATED_TOTAL = "jetstream_advisory_terminated_total"
    JETSTREAM_ADVISORY_STREAM_QUORUM_LOST_TOTAL = "jetstream_advisory_stream_quorum_lost_total"
    JETSTREAM_ADVISORY_CONSUMER_QUORUM_LOST_TOTAL = "jetstream_advisory_consumer_quorum_lost_total"
    JETSTREAM_ADVISORY_STREAM_LEADER_ELECTED_TOTAL = (
        "jetstream_advisory_stream_leader_elected_total"
    )
    JETSTREAM_ADVISORY_CONSUMER_LEADER_ELECTED_TOTAL = (
        "jetstream_advisory_consumer_leader_elected_total"
    )
    JETSTREAM_ADVISORY_STREAM_ACTION_TOTAL = "jetstream_advisory_stream_action_total"
    JETSTREAM_ADVISORY_CONSUMER_ACTION_TOTAL = "jetstream_advisory_consumer_action_total"
    JETSTREAM_ADVISORY_API_AUDIT_TOTAL = "jetstream_advisory_api_audit_total"
    PRIORITY_LANE_BATCHES_TOTAL = "priority_lane_batches_total"
    PRIORITY_LANE_MESSAGES_TOTAL = "priority_lane_messages_total"
    PRIORITY_LANE_DEFAULTED_TOTAL = "priority_lane_defaulted_total"
    PRIORITY_LANE_REJECTED_TOTAL = "priority_lane_rejected_total"
    CURRENT_PRIORITY_LANES_ACTIVE = "current_priority_lanes_active"
    POLICY_MESSAGES_PASSED_TOTAL = "policy_messages_passed_total"
    POLICY_MESSAGES_REJECTED_TOTAL = "policy_messages_rejected_total"
    POLICY_BATCHES_PASSED_TOTAL = "policy_batches_passed_total"
    POLICY_BATCHES_REJECTED_TOTAL = "policy_batches_rejected_total"
    POLICY_EVALUATION_ERRORS_TOTAL = "policy_evaluation_errors_total"
    MESSAGE_AUTHENTICITY_MESSAGES_PASSED_TOTAL = "message_authenticity_messages_passed_total"
    MESSAGE_AUTHENTICITY_MESSAGES_REJECTED_TOTAL = "message_authenticity_messages_rejected_total"
    MESSAGE_AUTHENTICITY_BATCHES_PASSED_TOTAL = "message_authenticity_batches_passed_total"
    MESSAGE_AUTHENTICITY_BATCHES_REJECTED_TOTAL = "message_authenticity_batches_rejected_total"
    MESSAGE_AUTHENTICITY_EVALUATION_ERRORS_TOTAL = "message_authenticity_evaluation_errors_total"
    SIZE_POLICY_MESSAGES_PASSED_TOTAL = "size_policy_messages_passed_total"
    SIZE_POLICY_MESSAGES_REJECTED_TOTAL = "size_policy_messages_rejected_total"
    SIZE_POLICY_BATCHES_PASSED_TOTAL = "size_policy_batches_passed_total"
    SIZE_POLICY_BATCHES_REJECTED_TOTAL = "size_policy_batches_rejected_total"
    SIZE_POLICY_EVALUATION_ERRORS_TOTAL = "size_policy_evaluation_errors_total"
    EVENT_AGE_AT_RECEIVE_SECONDS = "event_age_at_receive_seconds"
    EVENT_AGE_AT_STORE_SECONDS = "event_age_at_store_seconds"
    EVENTS_STALE_AT_RECEIVE_TOTAL = "events_stale_at_receive_total"
    EVENTS_STALE_AT_STORE_TOTAL = "events_stale_at_store_total"
    EVENT_CREATION_TIMESTAMP_MISSING_TOTAL = "event_creation_timestamp_missing_total"
    EVENT_CREATION_TIMESTAMP_MALFORMED_TOTAL = "event_creation_timestamp_malformed_total"
    EVENT_CREATION_TIMESTAMP_FUTURE_TOTAL = "event_creation_timestamp_future_total"
    EVENT_SOURCE_CLOCK_SKEW_SECONDS = "event_source_clock_skew_seconds"
    ORACLE_CONFLICTS_TOTAL = "oracle_conflicts_total"
    ORACLE_DUPLICATES_TOTAL = "oracle_duplicates_total"
    ORACLE_DUPLICATE_IGNORED_TOTAL = "oracle_duplicate_ignored_total"
    ORACLE_DUPLICATE_NOOP_TOTAL = "oracle_duplicate_noop_total"
    ORACLE_MERGE_ROWS_TOTAL = "oracle_merge_rows_total"
    ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL = "oracle_merge_outcome_unknown_total"
    MYSQL_EXECUTE_SECONDS = "mysql_execute_seconds"
    MYSQL_COMMIT_SECONDS = "mysql_commit_seconds"
    MYSQL_CONFLICTS_TOTAL = "mysql_conflicts_total"
    MYSQL_DUPLICATES_TOTAL = "mysql_duplicates_total"
    MYSQL_DUPLICATE_IGNORED_TOTAL = "mysql_duplicate_ignored_total"
    MYSQL_DUPLICATE_NOOP_TOTAL = "mysql_duplicate_noop_total"
    MYSQL_UPSERT_ROWS_TOTAL = "mysql_upsert_rows_total"
    MYSQL_UPSERT_OUTCOME_UNKNOWN_TOTAL = "mysql_upsert_outcome_unknown_total"
    FANOUT_ROUTE_MATCHES_TOTAL = "fanout_route_matches_total"
    FANOUT_MESSAGES_ROUTED_TOTAL = "fanout_messages_routed_total"
    FANOUT_MESSAGES_NO_ROUTE_TOTAL = "fanout_messages_no_route_total"
    FANOUT_CHILD_SINKS_SELECTED_TOTAL = "fanout_child_sinks_selected_total"
    CURRENT_FANOUT_CHILD_SINKS_SELECTED = "current_fanout_child_sinks_selected"
    FANOUT_REQUIRED_CHILD_SUCCESS_TOTAL = "fanout_required_child_success_total"
    FANOUT_REQUIRED_CHILD_FAILURE_TOTAL = "fanout_required_child_failure_total"
    FANOUT_OPTIONAL_CHILD_SUCCESS_TOTAL = "fanout_optional_child_success_total"
    FANOUT_OPTIONAL_CHILD_FAILURE_TOTAL = "fanout_optional_child_failure_total"
    FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL = "fanout_optional_child_timeout_total"
    FANOUT_MESSAGES_ACKED_TOTAL = "fanout_messages_acked_total"
    FANOUT_MESSAGES_ACK_BLOCKED_TOTAL = "fanout_messages_ack_blocked_total"
    FANOUT_ACK_GATE_WAIT_SECONDS = "fanout_ack_gate_wait_seconds"
    FANOUT_BATCH_SECONDS = "fanout_batch_seconds"
    LAST_SINK_SUCCESS_EPOCH_SECONDS = "last_sink_success_epoch_seconds"
    CURRENT_BATCH_MESSAGES = "current_batch_messages"

    # Backward-compatible aliases from the original metrics abstraction.  The
    # runner still emits these while operators migrate dashboards to the clearer
    # names above.
    LEGACY_MESSAGES_RECEIVED_TOTAL = "messages_received_total"
    LEGACY_BATCHES_WRITTEN_TOTAL = "batches_written_total"
    LEGACY_BATCH_WRITE_SECONDS = "batch_write_seconds"
    LEGACY_LAST_SUCCESS_TIMESTAMP = "last_success_timestamp"
    LEGACY_CURRENT_BATCH_SIZE = "current_batch_size"


METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        MetricNames.MESSAGES_FETCHED_TOTAL,
        "counter",
        "Raw JetStream messages fetched by the pull consumer.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_PREPARED_TOTAL,
        "counter",
        "Messages converted into envelopes and transformed by core policies.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_WRITTEN_TOTAL,
        "counter",
        "Messages reported durable by the destination sink.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_ACKED_TOTAL,
        "counter",
        "Messages acknowledged to JetStream after durable success or DLQ success.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_TERMINATED_TOTAL,
        "counter",
        "Messages terminally acknowledged to JetStream after successful DLQ publication.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_NACKED_TOTAL,
        "counter",
        "Messages negatively acknowledged after retryable failures.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_FAILED_TOTAL,
        "counter",
        "Messages that entered a failure path before ACK.",
    ),
    MetricSpec(
        MetricNames.MESSAGES_DLQ_TOTAL,
        "counter",
        "Messages published to a configured dead-letter subject.",
    ),
    MetricSpec(
        MetricNames.BATCHES_FETCHED_TOTAL,
        "counter",
        "Non-empty batches fetched from JetStream.",
    ),
    MetricSpec(
        MetricNames.NATS_FETCH_SECONDS,
        "histogram",
        "Elapsed seconds spent waiting for JetStream pull fetch calls.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_MAPPING_SECONDS,
        "histogram",
        "Elapsed seconds spent converting raw NATS messages into internal envelopes.",
    ),
    MetricSpec(
        MetricNames.SINK_BATCHES_WRITTEN_TOTAL,
        "counter",
        "Batches that returned durable success from sink.write_batch.",
    ),
    MetricSpec(
        MetricNames.SINK_BATCH_WRITE_SECONDS,
        "histogram",
        "Elapsed seconds spent inside sink.write_batch for successful batches.",
    ),
    MetricSpec(
        MetricNames.ORACLE_EXECUTE_SECONDS,
        "histogram",
        "Elapsed seconds spent executing Oracle batch write statements before commit.",
    ),
    MetricSpec(
        MetricNames.ORACLE_COMMIT_SECONDS,
        "histogram",
        "Elapsed seconds spent committing Oracle transactions.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_ACK_SECONDS,
        "histogram",
        "Elapsed seconds spent ACKing JetStream messages after durable success.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_TERM_SECONDS,
        "histogram",
        "Elapsed seconds spent sending terminal acknowledgements after DLQ publication.",
    ),
    MetricSpec(
        MetricNames.RETRY_BACKOFF_DELAY_SECONDS,
        "histogram",
        "Retry delay seconds selected for retryable failures before delayed NAK.",
    ),
    MetricSpec(
        MetricNames.SINK_WRITE_ERRORS_TOTAL,
        "counter",
        "Sink write failures raised before durable success.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_NORMALIZATION_ERRORS_TOTAL,
        "counter",
        "Raw NATS messages that failed envelope normalization.",
    ),
    MetricSpec(
        MetricNames.PAYLOAD_ENCRYPTION_ERRORS_TOTAL,
        "counter",
        "Messages that failed core payload encryption before sink delivery.",
    ),
    MetricSpec(
        MetricNames.DLQ_PUBLISH_ERRORS_TOTAL,
        "counter",
        "Messages whose DLQ publication failed before original ACK.",
    ),
    MetricSpec(
        MetricNames.ACK_ERRORS_TOTAL,
        "counter",
        "Messages whose JetStream ACK failed after durable success.",
    ),
    MetricSpec(
        MetricNames.TERM_ERRORS_TOTAL,
        "counter",
        "Messages whose terminal acknowledgement failed after successful DLQ publication.",
    ),
    MetricSpec(
        MetricNames.NATS_CONNECTION_DISCONNECTED_TOTAL,
        "counter",
        "NATS client disconnect events observed by the runner.",
    ),
    MetricSpec(
        MetricNames.NATS_CONNECTION_RECONNECTED_TOTAL,
        "counter",
        "NATS client reconnect events observed by the runner.",
    ),
    MetricSpec(
        MetricNames.NATS_CONNECTION_CLOSED_TOTAL,
        "counter",
        "NATS client closed events observed by the runner.",
    ),
    MetricSpec(
        MetricNames.NATS_DISCOVERED_SERVERS_TOTAL,
        "counter",
        "NATS discovered-server events observed by the runner.",
    ),
    MetricSpec(
        MetricNames.NATS_ASYNC_ERRORS_TOTAL,
        "counter",
        "NATS asynchronous error callback events observed by the runner.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORIES_RECEIVED_TOTAL,
        "counter",
        "JetStream advisory messages accepted by the optional advisory monitor.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORIES_FILTERED_TOTAL,
        "counter",
        "JetStream advisory messages ignored because they did not match allowed subjects.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_PARSE_ERRORS_TOTAL,
        "counter",
        "JetStream advisory messages rejected by safe JSON parsing and validation.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_UNSUPPORTED_TOTAL,
        "counter",
        "JetStream advisory messages observed with unsupported advisory kinds.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_MAX_DELIVER_TOTAL,
        "counter",
        "JetStream max-deliver advisories observed without exposing stream or consumer names.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_NAK_TOTAL,
        "counter",
        "JetStream NAK advisories observed without exposing stream or consumer names.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_TERMINATED_TOTAL,
        "counter",
        "JetStream terminal-ack advisories observed without exposing stream or consumer names.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_STREAM_QUORUM_LOST_TOTAL,
        "counter",
        "JetStream stream quorum-lost advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_CONSUMER_QUORUM_LOST_TOTAL,
        "counter",
        "JetStream consumer quorum-lost advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_STREAM_LEADER_ELECTED_TOTAL,
        "counter",
        "JetStream stream leader-election advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_CONSUMER_LEADER_ELECTED_TOTAL,
        "counter",
        "JetStream consumer leader-election advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_STREAM_ACTION_TOTAL,
        "counter",
        "JetStream stream action advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_CONSUMER_ACTION_TOTAL,
        "counter",
        "JetStream consumer action advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.JETSTREAM_ADVISORY_API_AUDIT_TOTAL,
        "counter",
        "JetStream API audit advisories observed as aggregate events.",
    ),
    MetricSpec(
        MetricNames.PRIORITY_LANE_BATCHES_TOTAL,
        "counter",
        "Batches ordered by enabled priority-lane scheduling.",
    ),
    MetricSpec(
        MetricNames.PRIORITY_LANE_MESSAGES_TOTAL,
        "counter",
        "Messages evaluated by priority-lane scheduling without exposing subjects.",
    ),
    MetricSpec(
        MetricNames.PRIORITY_LANE_DEFAULTED_TOTAL,
        "counter",
        "Messages routed to the default priority lane because priority was missing or unknown.",
    ),
    MetricSpec(
        MetricNames.PRIORITY_LANE_REJECTED_TOTAL,
        "counter",
        "Messages rejected because priority metadata violated the priority-lane policy.",
    ),
    MetricSpec(
        MetricNames.CURRENT_PRIORITY_LANES_ACTIVE,
        "gauge",
        "Number of configured priority lanes represented in the active scheduled batch.",
    ),
    MetricSpec(
        MetricNames.POLICY_MESSAGES_PASSED_TOTAL,
        "counter",
        "Messages accepted by the pre-sink policy gate before sink delivery.",
    ),
    MetricSpec(
        MetricNames.POLICY_MESSAGES_REJECTED_TOTAL,
        "counter",
        "Messages rejected by the pre-sink policy gate before sink delivery.",
    ),
    MetricSpec(
        MetricNames.POLICY_BATCHES_PASSED_TOTAL,
        "counter",
        "Batches with at least one message accepted by the pre-sink policy gate.",
    ),
    MetricSpec(
        MetricNames.POLICY_BATCHES_REJECTED_TOTAL,
        "counter",
        "Batches with at least one message rejected by the pre-sink policy gate.",
    ),
    MetricSpec(
        MetricNames.POLICY_EVALUATION_ERRORS_TOTAL,
        "counter",
        "Messages left redeliverable because policy evaluation failed unexpectedly.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_AUTHENTICITY_MESSAGES_PASSED_TOTAL,
        "counter",
        "Messages accepted by message authenticity verification before sink delivery.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_AUTHENTICITY_MESSAGES_REJECTED_TOTAL,
        "counter",
        "Messages rejected by message authenticity verification before sink delivery.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_AUTHENTICITY_BATCHES_PASSED_TOTAL,
        "counter",
        "Batches with at least one message accepted by message authenticity verification.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_AUTHENTICITY_BATCHES_REJECTED_TOTAL,
        "counter",
        "Batches with at least one message rejected by message authenticity verification.",
    ),
    MetricSpec(
        MetricNames.MESSAGE_AUTHENTICITY_EVALUATION_ERRORS_TOTAL,
        "counter",
        "Messages left redeliverable because authenticity evaluation failed unexpectedly.",
    ),
    MetricSpec(
        MetricNames.SIZE_POLICY_MESSAGES_PASSED_TOTAL,
        "counter",
        "Messages accepted by the core size policy before sink delivery.",
    ),
    MetricSpec(
        MetricNames.SIZE_POLICY_MESSAGES_REJECTED_TOTAL,
        "counter",
        "Messages rejected by the core size policy before sink delivery.",
    ),
    MetricSpec(
        MetricNames.SIZE_POLICY_BATCHES_PASSED_TOTAL,
        "counter",
        "Batches with at least one message accepted by the core size policy.",
    ),
    MetricSpec(
        MetricNames.SIZE_POLICY_BATCHES_REJECTED_TOTAL,
        "counter",
        "Batches with at least one message rejected by the core size policy.",
    ),
    MetricSpec(
        MetricNames.SIZE_POLICY_EVALUATION_ERRORS_TOTAL,
        "counter",
        "Messages left redeliverable because size-policy evaluation failed unexpectedly.",
    ),
    MetricSpec(
        MetricNames.EVENT_AGE_AT_RECEIVE_SECONDS,
        "histogram",
        "Observed event age in seconds when the runner received the message.",
    ),
    MetricSpec(
        MetricNames.EVENT_AGE_AT_STORE_SECONDS,
        "histogram",
        "Observed event age in seconds after the sink reported durable success.",
    ),
    MetricSpec(
        MetricNames.EVENTS_STALE_AT_RECEIVE_TOTAL,
        "counter",
        "Events older than the configured stale threshold at runner receive time.",
    ),
    MetricSpec(
        MetricNames.EVENTS_STALE_AT_STORE_TOTAL,
        "counter",
        "Events older than the configured stale threshold after durable sink success.",
    ),
    MetricSpec(
        MetricNames.EVENT_CREATION_TIMESTAMP_MISSING_TOTAL,
        "counter",
        "Messages without a usable publisher or JetStream creation timestamp.",
    ),
    MetricSpec(
        MetricNames.EVENT_CREATION_TIMESTAMP_MALFORMED_TOTAL,
        "counter",
        "Messages with a malformed publisher creation timestamp header.",
    ),
    MetricSpec(
        MetricNames.EVENT_CREATION_TIMESTAMP_FUTURE_TOTAL,
        "counter",
        "Messages whose creation timestamp is beyond the configured future-skew tolerance.",
    ),
    MetricSpec(
        MetricNames.EVENT_SOURCE_CLOCK_SKEW_SECONDS,
        "histogram",
        "Positive source clock skew seconds observed for future-dated messages.",
    ),
    MetricSpec(
        MetricNames.ORACLE_CONFLICTS_TOTAL,
        "counter",
        "Oracle write conflicts observed by OracleSink, such as duplicate-key conflicts.",
    ),
    MetricSpec(
        MetricNames.ORACLE_DUPLICATES_TOTAL,
        "counter",
        "Oracle rows identified as duplicate prior processing through idempotent handling.",
    ),
    MetricSpec(
        MetricNames.ORACLE_DUPLICATE_IGNORED_TOTAL,
        "counter",
        "Oracle duplicate rows safely ignored by insert_ignore mode.",
    ),
    MetricSpec(
        MetricNames.ORACLE_DUPLICATE_NOOP_TOTAL,
        "counter",
        "Oracle duplicate rows safely left unchanged by merge mode with no update columns.",
    ),
    MetricSpec(
        MetricNames.ORACLE_MERGE_ROWS_TOTAL,
        "counter",
        "Oracle rows committed through merge mode.",
    ),
    MetricSpec(
        MetricNames.ORACLE_MERGE_OUTCOME_UNKNOWN_TOTAL,
        "counter",
        "Oracle merge rows where insert-versus-match outcome is not reliably exposed.",
    ),
    MetricSpec(
        MetricNames.MYSQL_EXECUTE_SECONDS,
        "histogram",
        "Elapsed seconds spent executing Oracle MySQL batch write statements before commit.",
    ),
    MetricSpec(
        MetricNames.MYSQL_COMMIT_SECONDS,
        "histogram",
        "Elapsed seconds spent committing Oracle MySQL transactions.",
    ),
    MetricSpec(
        MetricNames.MYSQL_CONFLICTS_TOTAL,
        "counter",
        "Oracle MySQL write conflicts observed by MySqlSink, such as duplicate-key conflicts.",
    ),
    MetricSpec(
        MetricNames.MYSQL_DUPLICATES_TOTAL,
        "counter",
        "Oracle MySQL rows identified as duplicate prior processing through idempotent handling.",
    ),
    MetricSpec(
        MetricNames.MYSQL_DUPLICATE_IGNORED_TOTAL,
        "counter",
        "Oracle MySQL duplicate rows safely ignored by insert_ignore mode.",
    ),
    MetricSpec(
        MetricNames.MYSQL_DUPLICATE_NOOP_TOTAL,
        "counter",
        "Oracle MySQL duplicate rows safely left unchanged by upsert mode with no update columns.",
    ),
    MetricSpec(
        MetricNames.MYSQL_UPSERT_ROWS_TOTAL,
        "counter",
        "Oracle MySQL rows committed through upsert mode.",
    ),
    MetricSpec(
        MetricNames.MYSQL_UPSERT_OUTCOME_UNKNOWN_TOTAL,
        "counter",
        "Oracle MySQL upsert rows where insert-versus-match outcome is not reliably exposed.",
    ),
    MetricSpec(
        MetricNames.FANOUT_ROUTE_MATCHES_TOTAL,
        "counter",
        "Route policy entries matched by fan-out delivery without exporting route names.",
    ),
    MetricSpec(
        MetricNames.FANOUT_MESSAGES_ROUTED_TOTAL,
        "counter",
        "Messages with at least one selected fan-out child sink target.",
    ),
    MetricSpec(
        MetricNames.FANOUT_MESSAGES_NO_ROUTE_TOTAL,
        "counter",
        "Messages rejected or ignored because routing selected no fan-out targets.",
    ),
    MetricSpec(
        MetricNames.FANOUT_CHILD_SINKS_SELECTED_TOTAL,
        "counter",
        "Selected fan-out child sink operations across routed messages.",
    ),
    MetricSpec(
        MetricNames.CURRENT_FANOUT_CHILD_SINKS_SELECTED,
        "gauge",
        "Number of fan-out child sink targets selected for the latest evaluated message.",
    ),
    MetricSpec(
        MetricNames.FANOUT_REQUIRED_CHILD_SUCCESS_TOTAL,
        "counter",
        "Required fan-out child sink operations that committed before ACK.",
    ),
    MetricSpec(
        MetricNames.FANOUT_REQUIRED_CHILD_FAILURE_TOTAL,
        "counter",
        "Required fan-out child sink operations that failed and blocked ACK.",
    ),
    MetricSpec(
        MetricNames.FANOUT_OPTIONAL_CHILD_SUCCESS_TOTAL,
        "counter",
        "Optional fan-out child sink operations that completed before the ACK gate released.",
    ),
    MetricSpec(
        MetricNames.FANOUT_OPTIONAL_CHILD_FAILURE_TOTAL,
        "counter",
        "Optional fan-out child sink operations that failed without blocking required ACK.",
    ),
    MetricSpec(
        MetricNames.FANOUT_OPTIONAL_CHILD_TIMEOUT_TOTAL,
        "counter",
        "Optional fan-out child sink operations that exceeded their bounded ACK wait window.",
    ),
    MetricSpec(
        MetricNames.FANOUT_MESSAGES_ACKED_TOTAL,
        "counter",
        "Fan-out messages whose original JetStream message became eligible for ACK.",
    ),
    MetricSpec(
        MetricNames.FANOUT_MESSAGES_ACK_BLOCKED_TOTAL,
        "counter",
        "Fan-out messages whose original JetStream ACK was blocked by required failure.",
    ),
    MetricSpec(
        MetricNames.FANOUT_ACK_GATE_WAIT_SECONDS,
        "histogram",
        "Elapsed seconds spent waiting at the fan-out ACK gate.",
    ),
    MetricSpec(
        MetricNames.FANOUT_BATCH_SECONDS,
        "histogram",
        "Elapsed seconds spent processing a fan-out batch across selected child sinks.",
    ),
    MetricSpec(
        MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS,
        "gauge",
        "Unix epoch seconds for the latest durable sink success followed by ACK.",
    ),
    MetricSpec(
        MetricNames.CURRENT_BATCH_MESSAGES,
        "gauge",
        "Number of messages in the active batch currently being processed.",
    ),
)

METRIC_SPEC_BY_NAME = {spec.name: spec for spec in METRIC_SPECS}

LEGACY_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    MetricNames.MESSAGES_PREPARED_TOTAL: (MetricNames.LEGACY_MESSAGES_RECEIVED_TOTAL,),
    MetricNames.SINK_BATCHES_WRITTEN_TOTAL: (MetricNames.LEGACY_BATCHES_WRITTEN_TOTAL,),
    MetricNames.SINK_BATCH_WRITE_SECONDS: (MetricNames.LEGACY_BATCH_WRITE_SECONDS,),
    MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS: (MetricNames.LEGACY_LAST_SUCCESS_TIMESTAMP,),
    MetricNames.CURRENT_BATCH_MESSAGES: (MetricNames.LEGACY_CURRENT_BATCH_SIZE,),
}


def validate_metric_namespace(namespace: str) -> str:
    """Validate a namespace that can be used safely by common metrics systems."""

    rendered = namespace.strip()
    if not rendered:
        raise ValueError("metrics namespace must not be empty")
    if not METRIC_NAMESPACE_RE.fullmatch(rendered):
        raise ValueError(
            "metrics namespace may contain only letters, digits, underscores, and colons, "
            "and must not start with a digit"
        )
    return rendered


def qualified_metric_name(name: str, *, namespace: str = DEFAULT_METRIC_NAMESPACE) -> str:
    """Return the conventional exported metric name.

    The runner emits suffixes to `MetricsRecorder` implementations. Exporters
    can call this helper to produce names such as
    `nats_sinks_messages_fetched_total` without duplicating namespace rules.
    """

    rendered_namespace = validate_metric_namespace(namespace)
    if name not in METRIC_SPEC_BY_NAME and name not in _ALL_LEGACY_METRIC_NAMES:
        raise ValueError(f"unknown nats-sinks metric name: {name}")
    return f"{rendered_namespace}_{name}"


def _reject_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous duplicate keys in metrics snapshot JSON."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in metrics snapshot: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    """Reject Python JSON extensions while loading metrics snapshots."""

    raise _NonStandardJsonConstantError(f"non-standard JSON constant is not allowed: {value}")


def _finite_metric_float(value: object, *, name: str) -> float:
    """Return a finite metric float or fail before JSON output."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"metrics value for {name!r} must be numeric")
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"metrics value for {name!r} must be finite")
    return rendered


def _observation_summary(values: list[float]) -> dict[str, float]:
    """Summarize observations without writing unbounded raw arrays to disk."""

    finite_values = [_finite_metric_float(value, name="observation") for value in values]
    if not finite_values:
        return {"count": 0.0, "sum": 0.0, "min": 0.0, "max": 0.0, "last": 0.0}
    return {
        "count": float(len(finite_values)),
        "sum": float(sum(finite_values)),
        "min": float(min(finite_values)),
        "max": float(max(finite_values)),
        "last": float(finite_values[-1]),
    }


def _coerce_metric_number(value: object, *, name: str) -> float:
    """Coerce JSON metric values while rejecting non-numeric snapshot data."""

    return _finite_metric_float(value, name=name)


@dataclass(frozen=True, slots=True)
class MetricRow:
    """One flattened metric value read from a snapshot.

    The metrics CLI uses rows because shell-friendly formats need a simple
    sequence of `kind`, `name`, `value`, and `description` values instead of the
    nested snapshot structure.
    """

    kind: MetricRowKind
    name: str
    value: float
    description: str = ""
    stat: str | None = None

    @property
    def shell_name(self) -> str:
        """Return an environment-variable-safe representation of the row name."""

        rendered = self.name.replace(".", "_")
        return re.sub(r"[^A-Za-z0-9_]", "_", rendered).upper()


class MetricsRecorder(Protocol):
    """Metrics interface used by the runner."""

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter."""

    def observe(self, name: str, value: float) -> None:
        """Observe a floating-point value."""

    def set_value(self, name: str, value: float) -> None:
        """Set a gauge-like value."""


def increment_metric(recorder: MetricsRecorder, name: str, value: int = 1) -> None:
    """Increment a metric and its documented legacy aliases.

    Alias emission is intentionally centralized so compatibility names remain
    temporary and visible.  It also keeps the runner readable: delivery logic
    should describe what happened, while this module owns naming details.
    """

    recorder.increment(name, value)
    for alias in LEGACY_METRIC_ALIASES.get(name, ()):
        recorder.increment(alias, value)


def observe_metric(recorder: MetricsRecorder, name: str, value: float) -> None:
    """Observe a metric and its documented legacy aliases."""

    recorder.observe(name, value)
    for alias in LEGACY_METRIC_ALIASES.get(name, ()):
        recorder.observe(alias, value)


def set_metric_value(recorder: MetricsRecorder, name: str, value: float) -> None:
    """Set a gauge metric and its documented legacy aliases."""

    recorder.set_value(name, value)
    for alias in LEGACY_METRIC_ALIASES.get(name, ()):
        recorder.set_value(alias, value)


@dataclass(slots=True)
class InMemoryMetrics:
    """Deterministic in-memory metrics recorder useful for tests and embedding."""

    counters: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    observations: defaultdict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    gauges: dict[str, float] = field(default_factory=dict)
    namespace: str = DEFAULT_METRIC_NAMESPACE

    def increment(self, name: str, value: int = 1) -> None:
        """Increase a named counter by `value`."""

        self.counters[name] += value

    def observe(self, name: str, value: float) -> None:
        """Record one floating-point observation for a named metric."""

        self.observations[name].append(value)

    def set_value(self, name: str, value: float) -> None:
        """Set the latest value for a named gauge-style metric."""

        self.gauges[name] = value

    def mark_success(self) -> None:
        """Record the current wall-clock time as the last successful write."""

        set_metric_value(self, MetricNames.LAST_SINK_SUCCESS_EPOCH_SECONDS, time.time())

    def qualified_name(self, name: str) -> str:
        """Return the conventional exported name for a metric suffix."""

        return qualified_metric_name(name, namespace=self.namespace)

    def snapshot(self) -> dict[str, object]:
        """Return a bounded, JSON-serializable metrics snapshot.

        Observations are summarized instead of written as raw arrays so the
        snapshot file remains small and safe to inspect with shell tools.
        """

        return metrics_snapshot(
            counters=dict(self.counters),
            gauges=dict(self.gauges),
            observations={name: list(values) for name, values in self.observations.items()},
            namespace=self.namespace,
        )


class NoopMetrics:
    """Metrics recorder that intentionally does nothing."""

    def increment(self, name: str, value: int = 1) -> None:
        """Accept counter updates when metrics collection is disabled."""

        del name, value

    def observe(self, name: str, value: float) -> None:
        """Accept observations when metrics collection is disabled."""

        del name, value

    def set_value(self, name: str, value: float) -> None:
        """Accept gauge updates when metrics collection is disabled."""

        del name, value


class JsonFileMetrics(InMemoryMetrics):
    """In-memory metrics recorder that atomically writes a JSON snapshot.

    This recorder is intentionally simple and local.  It is useful for the
    standalone `nats-sink-metrics` CLI, systemd health scripts, and developer
    smoke tests.  It is not a substitute for a full Prometheus or OpenTelemetry
    exporter in larger deployments.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        namespace: str = DEFAULT_METRIC_NAMESPACE,
        auto_flush: bool = True,
    ) -> None:
        super().__init__(namespace=validate_metric_namespace(namespace))
        self.path = Path(path).expanduser()
        self.auto_flush = auto_flush
        self.flush()

    def increment(self, name: str, value: int = 1) -> None:
        """Increase a named counter and update the snapshot file if requested."""

        super().increment(name, value)
        self._flush_if_enabled()

    def observe(self, name: str, value: float) -> None:
        """Record an observation and update the snapshot file if requested."""

        super().observe(name, value)
        self._flush_if_enabled()

    def set_value(self, name: str, value: float) -> None:
        """Set a gauge and update the snapshot file if requested."""

        super().set_value(name, value)
        self._flush_if_enabled()

    def flush(self) -> None:
        """Write the latest metrics snapshot to disk using atomic replacement."""

        write_metrics_snapshot(self.snapshot(), self.path)

    def _flush_if_enabled(self) -> None:
        if self.auto_flush:
            self.flush()


def metrics_snapshot(
    *,
    counters: dict[str, int],
    gauges: dict[str, float],
    observations: dict[str, list[float]],
    namespace: str = DEFAULT_METRIC_NAMESPACE,
) -> dict[str, object]:
    """Build a JSON-compatible metrics snapshot document."""

    rendered_namespace = validate_metric_namespace(namespace)
    generated_at = time.time()
    return {
        "schema": METRICS_SNAPSHOT_SCHEMA,
        "namespace": rendered_namespace,
        "generated_at_epoch_seconds": generated_at,
        "counters": {name: int(value) for name, value in sorted(counters.items())},
        "gauges": {
            name: _finite_metric_float(value, name=name) for name, value in sorted(gauges.items())
        },
        "observations": {
            name: _observation_summary([_finite_metric_float(value, name=name) for value in values])
            for name, values in sorted(observations.items())
        },
    }


def write_metrics_snapshot(snapshot: dict[str, object], path: str | os.PathLike[str]) -> None:
    """Write a metrics snapshot atomically.

    The temporary file is created in the destination directory so `os.replace`
    remains atomic on the same filesystem.  The file mode is owner-readable and
    owner-writable because operational metrics can still expose deployment
    behavior.
    """

    destination = Path(path).expanduser()
    if destination.name in {"", ".", ".."}:
        raise ValueError("metrics snapshot path must name a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, destination)
    finally:
        if temp_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temp_name)


def load_metrics_snapshot(path: str | os.PathLike[str]) -> dict[str, object]:
    """Load and validate a JSON metrics snapshot from disk."""

    source = Path(path).expanduser()
    try:
        stat = source.stat()
    except OSError as exc:
        raise ValueError(f"cannot read metrics snapshot {source}") from exc
    if stat.st_size > MAX_METRICS_SNAPSHOT_BYTES:
        raise ValueError(
            f"metrics snapshot {source} is too large; maximum is {MAX_METRICS_SNAPSHOT_BYTES} bytes"
        )
    try:
        text = source.read_text(encoding="utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"metrics snapshot {source} must be UTF-8") from exc
    except _NonStandardJsonConstantError as exc:
        raise ValueError(f"metrics snapshot {source} is not valid JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"metrics snapshot {source} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("metrics snapshot root must be a JSON object")
    if payload.get("schema") != METRICS_SNAPSHOT_SCHEMA:
        raise ValueError(
            f"metrics snapshot schema must be {METRICS_SNAPSHOT_SCHEMA!r}, "
            f"got {payload.get('schema')!r}"
        )
    validate_metric_namespace(str(payload.get("namespace", DEFAULT_METRIC_NAMESPACE)))
    for section in ("counters", "gauges", "observations"):
        if not isinstance(payload.get(section), dict):
            raise ValueError(f"metrics snapshot {section!r} section must be an object")
    return cast(dict[str, object], payload)


def metric_rows_from_snapshot(
    snapshot: dict[str, object],
    *,
    include_legacy: bool = False,
) -> list[MetricRow]:
    """Flatten a snapshot into rows suitable for CLI display."""

    legacy_names = _ALL_LEGACY_METRIC_NAMES
    rows: list[MetricRow] = []
    counters = cast(dict[str, object], snapshot["counters"])
    gauges = cast(dict[str, object], snapshot["gauges"])
    observations = cast(dict[str, object], snapshot["observations"])
    for name, value in counters.items():
        if name in legacy_names and not include_legacy:
            continue
        rows.append(
            MetricRow(
                kind="counter",
                name=name,
                value=_coerce_metric_number(value, name=name),
                description=METRIC_SPEC_BY_NAME.get(
                    name, MetricSpec(name, "counter", "")
                ).description,
            )
        )
    for name, value in gauges.items():
        if name in legacy_names and not include_legacy:
            continue
        rows.append(
            MetricRow(
                kind="gauge",
                name=name,
                value=_coerce_metric_number(value, name=name),
                description=METRIC_SPEC_BY_NAME.get(
                    name, MetricSpec(name, "gauge", "")
                ).description,
            )
        )
    for name, raw_summary in observations.items():
        if name in legacy_names and not include_legacy:
            continue
        if not isinstance(raw_summary, dict):
            raise ValueError(f"metrics observation {name!r} must be an object")
        description = METRIC_SPEC_BY_NAME.get(name, MetricSpec(name, "histogram", "")).description
        for stat in ("count", "sum", "min", "max", "last"):
            value = raw_summary.get(stat, 0.0)
            rows.append(
                MetricRow(
                    kind="observation",
                    name=f"{name}.{stat}",
                    value=_coerce_metric_number(value, name=f"{name}.{stat}"),
                    description=description,
                    stat=stat,
                )
            )
    return rows


_ALL_LEGACY_METRIC_NAMES = {
    alias for aliases in LEGACY_METRIC_ALIASES.values() for alias in aliases
}
