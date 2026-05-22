#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run the nats-sinks synthetic scenario harness.

The script is intentionally local-only by default.  It can generate a
destination-neutral report or write messages through the file sink without
requiring NATS, Oracle, credentials, wallets, certificates, or live network
access.  Reports are sanitized and suitable for issue evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nats_sinks.testing import (
    SyntheticScenarioProfile,
    generate_synthetic_scenario,
    render_synthetic_report_markdown,
    run_file_sink_synthetic_scenario,
    synthetic_report,
)


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the synthetic harness."""

    parser = argparse.ArgumentParser(description="Run sanitized synthetic nats-sinks scenarios.")
    parser.add_argument(
        "--sink",
        choices=("core", "file"),
        default="core",
        help="Target harness adapter. 'core' only generates envelopes; 'file' writes to FileSink.",
    )
    parser.add_argument("--profile", default="mission-smoke", help="Public profile name.")
    parser.add_argument("--message-count", type=int, default=32, help="Number of messages.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic scenario seed.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="File sink output directory. Deleted after the run unless --preserve-files is set.",
    )
    parser.add_argument(
        "--preserve-files",
        action="store_true",
        help="Keep file sink output files after the run.",
    )
    parser.add_argument(
        "--compression",
        choices=("none", "gzip"),
        default="none",
        help="File sink compression mode.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Report output format.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="Optional destination for the sanitized report.",
    )
    return parser


def _render_report(report: object, *, output_format: str) -> str:
    """Render a synthetic report object as JSON or Markdown."""

    if not hasattr(report, "to_dict"):
        raise TypeError("report object does not support to_dict()")
    if output_format == "markdown":
        return render_synthetic_report_markdown(report)  # type: ignore[arg-type]
    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    """Run the selected synthetic scenario and print a sanitized report."""

    args = _build_parser().parse_args()
    try:
        profile = SyntheticScenarioProfile(
            name=args.profile,
            message_count=args.message_count,
            seed=args.seed,
        )
        if args.sink == "file":
            result = run_file_sink_synthetic_scenario(
                profile=profile,
                output_dir=args.output_dir,
                compression=args.compression,
                preserve_files=args.preserve_files,
            )
            report = result.report
        else:
            messages = generate_synthetic_scenario(profile)
            report = synthetic_report(messages, profile_name=profile.name)
        rendered = _render_report(report, output_format=args.format)
    except Exception as exc:
        sys.stderr.write(f"synthetic harness failed: {exc}\n")
        return 1

    if args.report_file is not None:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
