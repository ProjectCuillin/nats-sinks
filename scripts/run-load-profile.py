#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Run sanitized local load profiles for nats-sinks.

This script is safe for local development and CI because it uses generated
messages only.  It does not connect to NATS, Oracle, file sinks, Prometheus, or
any network service.  The output is public-safe by design and suitable for
release evidence when copied without local paths.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nats_sinks.testing import (
    LoadProfileOptions,
    render_load_profile_report,
    run_load_profile,
)


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for local load profiles."""

    parser = argparse.ArgumentParser(description="Run sanitized synthetic load profiles.")
    parser.add_argument(
        "--profile",
        choices=("normal", "retry", "dlq", "shutdown"),
        default="normal",
        help="Synthetic load profile to run.",
    )
    parser.add_argument("--message-count", type=int, default=256, help="Generated messages.")
    parser.add_argument("--batch-size", type=int, default=64, help="Generated batch size.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic scenario seed.")
    parser.add_argument(
        "--with-encryption",
        action="store_true",
        help="Include synthetic encryption workload timing without producing key material.",
    )
    parser.add_argument(
        "--metrics-snapshot-file",
        type=Path,
        help=(
            "Optional local metrics snapshot path. Deleted unless "
            "--preserve-metrics-snapshot is set."
        ),
    )
    parser.add_argument(
        "--preserve-metrics-snapshot",
        action="store_true",
        help="Keep the optional metrics snapshot file after the run.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Report output format.",
    )
    parser.add_argument("--report-file", type=Path, help="Optional sanitized report output path.")
    return parser


def main() -> int:
    """Run the requested load profile and print or write the sanitized report."""

    args = _build_parser().parse_args()
    try:
        options = LoadProfileOptions(
            profile=args.profile,
            message_count=args.message_count,
            batch_size=args.batch_size,
            seed=args.seed,
            encrypt_payloads=args.with_encryption,
            metrics_snapshot_file=args.metrics_snapshot_file,
            preserve_metrics_snapshot=args.preserve_metrics_snapshot,
        )
        report = run_load_profile(options)
        rendered = render_load_profile_report(report, output_format=args.format)
    except Exception as exc:
        sys.stderr.write(f"load profile failed: {exc}\n")
        return 1

    if args.report_file is not None:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
