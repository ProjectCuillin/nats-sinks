# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Synthetic testing helpers for nats-sinks maintainers and integrators.

The objects exported here are intentionally safe for public test evidence.
They generate fake `NatsEnvelope` instances and sanitized reports that exercise
mission-style metadata without requiring real operational subjects, payloads,
credentials, or infrastructure locators.
"""

from nats_sinks.testing.load_profile import (
    LoadPhaseTiming,
    LoadProfileOptions,
    LoadProfileReport,
    render_load_profile_report,
    run_load_profile,
)
from nats_sinks.testing.oracle_benchmark import (
    BenchmarkPhaseTiming,
    OracleBenchmarkOptions,
    OracleBenchmarkReport,
    build_oracle_benchmark_report,
    render_oracle_benchmark_report,
    sanitize_public_text,
)
from nats_sinks.testing.synthetic import (
    SyntheticFileSinkResult,
    SyntheticMessage,
    SyntheticScenarioProfile,
    SyntheticScenarioReport,
    generate_synthetic_scenario,
    render_synthetic_report_markdown,
    run_file_sink_synthetic_scenario,
    synthetic_report,
)
from nats_sinks.testing.websocket_harness import (
    WebSocketHarnessConfig,
    WebSocketHarnessPorts,
    choose_loopback_port,
    choose_websocket_harness_ports,
    nats_server_command,
    port_is_available,
    render_nats_websocket_config,
    sanitized_selected_ports,
    wait_for_tcp_port,
    write_nats_websocket_config,
)

__all__ = [
    "BenchmarkPhaseTiming",
    "LoadPhaseTiming",
    "LoadProfileOptions",
    "LoadProfileReport",
    "OracleBenchmarkOptions",
    "OracleBenchmarkReport",
    "SyntheticFileSinkResult",
    "SyntheticMessage",
    "SyntheticScenarioProfile",
    "SyntheticScenarioReport",
    "WebSocketHarnessConfig",
    "WebSocketHarnessPorts",
    "build_oracle_benchmark_report",
    "choose_loopback_port",
    "choose_websocket_harness_ports",
    "generate_synthetic_scenario",
    "nats_server_command",
    "port_is_available",
    "render_load_profile_report",
    "render_nats_websocket_config",
    "render_oracle_benchmark_report",
    "render_synthetic_report_markdown",
    "run_file_sink_synthetic_scenario",
    "run_load_profile",
    "sanitize_public_text",
    "sanitized_selected_ports",
    "synthetic_report",
    "wait_for_tcp_port",
    "write_nats_websocket_config",
]
