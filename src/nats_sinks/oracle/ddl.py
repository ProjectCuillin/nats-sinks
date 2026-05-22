# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Oracle DDL helpers.

DDL support is intentionally opt-in.  Production systems often manage database
objects through migrations, Terraform, Liquibase, Flyway, or DBA-controlled
change processes, so nats-sinks does not create or mutate tables by default.

The helper returns a recommended event table shape suitable for the default
stream-sequence idempotency strategy.  Callers can display it, test it, or
execute it only when `auto_create` is explicitly enabled.
"""

from __future__ import annotations

from nats_sinks.oracle.sql import validate_identifier


def create_events_table_ddl(table: str = "NATS_SINK_EVENTS") -> str:
    """Return recommended table DDL. It is not executed unless explicitly enabled."""

    table_name = validate_identifier(table)
    return f"""create table {table_name} (
    stream_name       varchar2(255) not null,
    stream_sequence   number not null,
    subject           clob not null,
    message_id        varchar2(512),
    priority          clob,
    classification    clob,
    labels            clob,
    received_at       timestamp default systimestamp not null,
    message_created_at_epoch_ns number(19),
    jetstream_timestamp_epoch_ns number(19),
    received_at_epoch_ns number(19) not null,
    stored_at_epoch_ns number(19) not null,
    payload_json      json,
    headers_json      json,
    metadata_json     json,
    mission_metadata_json json,
    constraint {table_name.split(".")[-1]}_pk
        primary key (stream_name, stream_sequence)
)"""
