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
    "build_oracle_benchmark_report",
    "generate_synthetic_scenario",
    "render_load_profile_report",
    "render_oracle_benchmark_report",
    "render_synthetic_report_markdown",
    "run_file_sink_synthetic_scenario",
    "run_load_profile",
    "sanitize_public_text",
    "synthetic_report",
]
