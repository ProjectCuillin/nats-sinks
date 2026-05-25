# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle MySQL DDL helpers.

DDL execution is opt-in through ``auto_create``.  Production teams should
usually manage tables through reviewed migrations, but the helper is valuable
for local test containers, examples, and first-run validation.  The default
table shape mirrors the normalized event record used by the Oracle Database
sink while using Oracle MySQL JSON columns for structured content.
"""

from __future__ import annotations

from nats_sinks.mysql.sql import quote_identifier, validate_identifier


def create_events_table_ddl(table: str = "NATS_SINK_EVENTS") -> str:
    """Return recommended Oracle MySQL table DDL."""

    table_name = quote_identifier(validate_identifier(table))
    constraint_name = validate_identifier(f"{table.split('.')[-1]}_pk")
    return f"""create table if not exists {table_name} (
    `STREAM_NAME` varchar(255) not null,
    `STREAM_SEQUENCE` bigint not null,
    `SUBJECT` text not null,
    `MESSAGE_ID` varchar(512),
    `PRIORITY` text,
    `CLASSIFICATION` text,
    `LABELS` text,
    `RECEIVED_AT` timestamp(6) not null default current_timestamp(6),
    `MESSAGE_CREATED_AT_EPOCH_NS` bigint,
    `JETSTREAM_TIMESTAMP_EPOCH_NS` bigint,
    `RECEIVED_AT_EPOCH_NS` bigint not null,
    `STORED_AT_EPOCH_NS` bigint not null,
    `PAYLOAD_JSON` json,
    `HEADERS_JSON` json,
    `METADATA_JSON` json,
    `MISSION_METADATA_JSON` json,
    `SECURITY_LABELS_JSON` json,
    constraint `{constraint_name}` primary key (`STREAM_NAME`, `STREAM_SEQUENCE`)
)"""
