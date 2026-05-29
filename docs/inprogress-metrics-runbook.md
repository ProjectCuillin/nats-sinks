# InProgress Metrics Runbook

This runbook explains how to interpret the `nats-sinks` InProgress metric
family. It is written for operators who enable optional JetStream progress
heartbeats around long-running sink writes, and for maintainers who need stable
metric names across observability connectors.

The current release provides the metric contract, local snapshot support,
`nats-sink-metrics` rendering, Prometheus text rendering, and operational
guidance. Runtime InProgress heartbeats remain disabled by default. They emit
these metrics only when `delivery.in_progress.enabled=true`,
safe effective AckWait timing is verified before fetch, no configured or
effective BackOff policy is present, and the heartbeat interval is below 80%
of AckWait.

## What InProgress Means

JetStream `InProgress` is a progress signal. It says that a delivered message
or batch is still being worked on. It does not mean the destination sink has
committed data, it does not acknowledge the original message, and it does not
replace DLQ, NAK, Term, retry, or idempotency behavior.

Use this plain-language interpretation:

- increasing attempts with matching successes can mean slow but active work;
- failures mean the progress signal itself is unreliable and should be
  investigated;
- maximum-heartbeat exits mean the bounded progress window was exhausted;
- an active-batch gauge that returns to zero is normal;
- an active-batch gauge that stays non-zero while write and ACK counters stop
  moving is a risk signal.

## Metric Names

The metric names are stable suffixes. Exporters qualify them with the selected
namespace, such as `nats_sinks_in_progress_attempts_total` or
`mission_ops_in_progress_attempts_total`.

| Metric suffix | Type | Meaning | What it is not |
| --- | --- | --- | --- |
| `in_progress_attempts_total` | counter | Heartbeat send attempts while sink work is active. | Not a sink write count. |
| `in_progress_successes_total` | counter | Heartbeats accepted by the client path. | Not durable sink success. |
| `in_progress_failures_total` | counter | Heartbeat calls that failed before the final ACK decision. | Not final message failure by itself. |
| `in_progress_max_heartbeats_reached_total` | counter | Batches where the configured heartbeat limit was reached. | Not a DLQ event by itself. |
| `current_in_progress_batches_active` | gauge | Batches currently protected by heartbeat supervision. | Not queue depth or pending message count. |
| `in_progress_heartbeat_seconds` | observation | Time spent sending heartbeat operations. | Not total sink write duration. |

These metrics do not include payloads, subjects, message IDs, table names,
file paths, destination names, classification values, labels, credentials, or
server locations.

## Shell Inspection

Use shell output for local service checks and runbooks:

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json \
  --format shell \
  --metric "in_progress_*" \
  --metric "current_in_progress_*"
```

Example output:

```text
CURRENT_IN_PROGRESS_BATCHES_ACTIVE=0.0
IN_PROGRESS_ATTEMPTS_TOTAL=18
IN_PROGRESS_FAILURES_TOTAL=0
IN_PROGRESS_HEARTBEAT_SECONDS_COUNT=18
IN_PROGRESS_HEARTBEAT_SECONDS_MAX=0.031
IN_PROGRESS_HEARTBEAT_SECONDS_SUM=0.322
IN_PROGRESS_MAX_HEARTBEATS_REACHED_TOTAL=0
IN_PROGRESS_SUCCESSES_TOTAL=18
```

This output is safe for ordinary operator evidence because it contains only
aggregate counts and timing summaries.

## Table Inspection

Use table output when reading a snapshot by hand:

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json \
  --metric "in_progress_*" \
  --metric "current_in_progress_*"
```

Example output:

```text
KIND         METRIC                                    VALUE  DESCRIPTION
counter      in_progress_attempts_total                  18  JetStream InProgress heartbeat attempts while sink work is still active.
counter      in_progress_successes_total                 18  JetStream InProgress heartbeats accepted by the client; this is not sink success.
counter      in_progress_failures_total                   0  JetStream InProgress heartbeat failures observed before the final ACK decision.
counter      in_progress_max_heartbeats_reached_total     0  Batches where the configured InProgress heartbeat limit was reached.
gauge        current_in_progress_batches_active         0.0  Batches currently protected by InProgress heartbeat supervision.
observation  in_progress_heartbeat_seconds.count         18  Elapsed seconds spent sending JetStream InProgress heartbeat operations.
```

## Prometheus Text Output

Prometheus sharing remains disabled by default. When an operator explicitly
allows these metrics through an observability policy, the local rendering is
still aggregate-only:

```bash
nats-sink-metrics show .local/nats-sinks/metrics.json \
  --format prometheus \
  --metric "in_progress_*" \
  --metric "current_in_progress_*"
```

Example output:

```text
# HELP nats_sinks_in_progress_attempts_total JetStream InProgress heartbeat attempts while sink work is still active.
# TYPE nats_sinks_in_progress_attempts_total counter
nats_sinks_in_progress_attempts_total 18
# HELP nats_sinks_in_progress_successes_total JetStream InProgress heartbeats accepted by the client; this is not sink success.
# TYPE nats_sinks_in_progress_successes_total counter
nats_sinks_in_progress_successes_total 18
# HELP nats_sinks_current_in_progress_batches_active Batches currently protected by InProgress heartbeat supervision.
# TYPE nats_sinks_current_in_progress_batches_active gauge
nats_sinks_current_in_progress_batches_active 0.0
```

Do not add subject, classification, label, destination, or host labels to
these series unless a separate approved observability policy explicitly allows
that bounded series.

## Healthy Slow Work

InProgress usually indicates healthy slow work when all of these are true:

- `in_progress_attempts_total` and `in_progress_successes_total` rise together;
- `in_progress_failures_total` remains zero or rare;
- `in_progress_max_heartbeats_reached_total` remains zero;
- `current_in_progress_batches_active` returns to zero after the write window;
- sink write counters and ACK counters continue to move after durable commits;
- sink write duration explains the progress activity.

Example checks:

```bash
nats-sink-metrics get .local/nats-sinks/metrics.json in_progress_failures_total --default 0
nats-sink-metrics get .local/nats-sinks/metrics.json current_in_progress_batches_active --default 0
nats-sink-metrics get .local/nats-sinks/metrics.json messages_acked_total --default 0
```

## Risky Slowness

Treat InProgress as risky when any of these patterns appear:

- attempts rise while successes do not;
- failures increase after a NATS reconnect, timeout, or shutdown event;
- maximum-heartbeat exits increase;
- active batches stay non-zero for longer than the expected sink write window;
- `sink_batch_write_seconds` grows while `messages_written_total` and
  `messages_acked_total` stop moving;
- optional fan-out targets time out while required target failures block ACK.

These signals should lead to ordinary operational diagnosis: inspect sink
latency, destination health, batch sizing, indexes, network quality,
connection events, DLQ activity, and idempotency behavior. Do not treat
InProgress as a performance fix.

## Alerting Guidance

Start with conservative aggregate alerts:

| Signal | Suggested interpretation |
| --- | --- |
| `in_progress_failures_total` increases | Progress calls are failing; review NATS connectivity and heartbeat timing. |
| `in_progress_max_heartbeats_reached_total` increases | Work exceeded the bounded progress window; tune or investigate the sink path. |
| `current_in_progress_batches_active` remains high | Long-running work may be stuck; compare with sink write and ACK metrics. |
| Attempts increase while `messages_acked_total` is flat | Progress is extending work, but durable completion is not happening. |

Alert thresholds are deployment-specific. In mission-support environments,
prefer alerts that correlate progress metrics with ACK, DLQ, sink write, NATS
connection, and destination-health metrics instead of alerting on heartbeat
attempts alone.

## Required Safety Boundaries

Keep these invariants intact when using or changing the runtime heartbeat:

- ACK only after durable required sink success or successful DLQ handling;
- NAK, Term, and DLQ decisions stay on their existing failure paths;
- idempotency remains required for duplicate-safe redelivery handling;
- heartbeat intervals, counts, and shutdown behavior stay bounded;
- observability output stays aggregate-only unless an explicit policy allows
  a low-cardinality label family;
- metrics failures never cause early ACK and never mutate message envelopes.

The detailed timing evaluation remains in
[InProgress Evaluation](in-progress-evaluation.md).
