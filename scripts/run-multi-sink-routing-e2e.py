#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the deterministic multi-sink routing end-to-end certification flow."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from nats_sinks.testing.multi_sink_routing import (
    MULTI_SINK_EXAMPLE_CONFIG,
    MultiSinkRoutingCertificationError,
    run_reduced_multi_sink_routing_flow_sync,
    write_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the multi-sink routing matrix with local file-backed "
            "probe sinks. The default mode performs no network calls."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("reduced",),
        default="reduced",
        help="Certification mode to run. Only reduced deterministic mode is local-safe.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=MULTI_SINK_EXAMPLE_CONFIG,
        help="Fan-out config to validate before the reduced route flow starts.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help=(
            "Optional local working directory for sanitized evidence files. "
            "A temporary directory is used and removed when omitted."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the sanitized JSON report.",
    )
    parser.add_argument(
        "--preserve-work-dir",
        action="store_true",
        help="Keep the optional working directory after the run for local debugging.",
    )
    args = parser.parse_args(argv)

    temporary_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir is None:
        temporary_dir = tempfile.TemporaryDirectory(prefix="nats-sinks-multi-sink-")
        work_dir = Path(temporary_dir.name)
    else:
        work_dir = args.work_dir

    try:
        report = run_reduced_multi_sink_routing_flow_sync(
            work_dir=work_dir,
            config_path=args.config,
        )
    except MultiSinkRoutingCertificationError as exc:
        sys.stderr.write(f"multi-sink routing certification failed: {exc}\n")
        return 1
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()
        elif args.work_dir is not None and not args.preserve_work_dir:
            shutil.rmtree(args.work_dir, ignore_errors=True)

    if args.output is not None:
        write_report(report, args.output)
    sys.stdout.write(report.to_json() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
