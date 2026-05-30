#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the local container-backed sink e2e suite.

The suite is intentionally opt-in. It starts short-lived local containers
through the destination-specific helpers and therefore requires Docker plus the
optional backend client dependencies.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 300.0
MIN_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 900.0


class ContainerE2eSuiteError(RuntimeError):
    """Raised when local container-backed e2e orchestration is invalid."""


@dataclass(frozen=True)
class BackendRunner:
    """One container-backed e2e backend command."""

    name: str
    script: Path


BACKEND_RUNNERS: tuple[BackendRunner, ...] = (
    BackendRunner(
        name="HTTP Sink NGINX FIPS Endpoint",
        script=REPO_ROOT / "scripts" / "run-http-sink-nginx-e2e.py",
    ),
    BackendRunner(
        name="Oracle MySQL Database",
        script=REPO_ROOT / "scripts" / "run-mysql-sink-e2e.py",
    ),
    BackendRunner(
        name="Oracle NoSQL Database",
        script=REPO_ROOT / "scripts" / "run-oracle-nosql-sink-e2e.py",
    ),
    BackendRunner(
        name="Oracle Coherence Community Edition",
        script=REPO_ROOT / "scripts" / "run-coherence-sink-e2e.py",
    ),
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse bounded local suite arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Readiness timeout passed to each backend e2e runner.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Ask backend runners to keep their containers for local debugging.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    """Reject unbounded readiness settings before container helpers run."""

    timeout = float(args.timeout_seconds)
    if timeout < MIN_TIMEOUT_SECONDS or timeout > MAX_TIMEOUT_SECONDS:
        raise ContainerE2eSuiteError(
            "--timeout-seconds must be between "
            f"{MIN_TIMEOUT_SECONDS:g} and {MAX_TIMEOUT_SECONDS:g}."
        )


def backend_command(
    runner: BackendRunner,
    *,
    timeout_seconds: float,
    preserve_artifacts: bool,
) -> list[str]:
    """Build a fixed command line for one backend e2e runner."""

    command = [
        sys.executable,
        str(runner.script),
        "--timeout-seconds",
        f"{timeout_seconds:g}",
    ]
    if preserve_artifacts:
        command.append("--preserve-artifacts")
    return command


def run_backend(
    runner: BackendRunner,
    *,
    timeout_seconds: float,
    preserve_artifacts: bool,
) -> None:
    """Run one backend e2e helper and fail closed on a non-zero result."""

    if not runner.script.is_file():
        raise ContainerE2eSuiteError(f"{runner.name} e2e runner is missing.")

    sys.stdout.write(f"Running {runner.name} container-backed sink e2e...\n")
    completed = subprocess.run(  # noqa: S603 - fixed repository-local argv list, no shell.
        backend_command(
            runner,
            timeout_seconds=timeout_seconds,
            preserve_artifacts=preserve_artifacts,
        ),
        check=False,
        cwd=REPO_ROOT,
        shell=False,
    )
    if completed.returncode != 0:
        raise ContainerE2eSuiteError(
            f"{runner.name} container-backed sink e2e failed with exit code {completed.returncode}."
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run all configured local container-backed sink e2e flows."""

    args = parse_args(argv)
    try:
        validate_args(args)
        for runner in BACKEND_RUNNERS:
            run_backend(
                runner,
                timeout_seconds=float(args.timeout_seconds),
                preserve_artifacts=bool(args.preserve_artifacts),
            )
        sys.stdout.write("Full container-backed sink e2e suite passed.\n")
        return 0
    except (ContainerE2eSuiteError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"Full container-backed sink e2e suite failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
