#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the Oracle NoSQL sink e2e test against a short-lived KVLite container."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-nosql-container-smoke.py"
DEFAULT_TABLE = "nats_sinks_nosql_sink_e2e_events"


class OracleNoSqlSinkE2eError(RuntimeError):
    """Raised when the Oracle NoSQL sink e2e harness cannot complete."""


def _load_smoke_module() -> ModuleType:
    """Load the existing Oracle NoSQL test-container helpers."""

    spec = importlib.util.spec_from_file_location("oracle_nosql_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise OracleNoSqlSinkE2eError("Unable to load Oracle NoSQL smoke helper.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    """Parse bounded e2e-test arguments."""

    smoke = _load_smoke_module()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", default=smoke.DEFAULT_IMAGE_REF)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container for debugging.",
    )
    return parser.parse_args()


def _pytest_env(*, endpoint: str, table: str) -> dict[str, str]:
    """Build a sanitized subprocess environment for pytest."""

    env = os.environ.copy()
    env.update(
        {
            "NATS_SINKS_ORACLE_NOSQL_INTEGRATION": "1",
            "NATS_SINKS_ORACLE_NOSQL_ENDPOINT": endpoint,
            "NATS_SINKS_ORACLE_NOSQL_TABLE": table,
            "NATS_SINKS_ORACLE_NOSQL_MODE": "kvstore",
            "NATS_SINKS_ORACLE_NOSQL_AUTO_CREATE": "1",
            "NATS_SINKS_ORACLE_NOSQL_DISCONNECTED_REPLAY": "1",
        }
    )
    return env


def _run_pytest_with_env(smoke: ModuleType, *, endpoint: str, table: str) -> None:
    """Run pytest with an explicit environment through subprocess."""

    completed = subprocess.run(  # noqa: S603,RUF100 - fixed local pytest argv list.
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_oracle_nosql_sink_e2e.py",
            "-q",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=_pytest_env(endpoint=endpoint, table=table),
        text=True,
        capture_output=True,
        timeout=240,
        shell=False,
    )
    if completed.returncode != 0:
        output = (completed.stdout + completed.stderr)[-smoke.FAILED_OUTPUT_TAIL_CHARS :]
        raise OracleNoSqlSinkE2eError(
            f"Oracle NoSQL sink pytest failed with exit code {completed.returncode}:\n{output}"
        )


def main() -> int:
    """Start KVLite, run Oracle NoSQL sink e2e, and clean up by default."""

    smoke = _load_smoke_module()
    args = parse_args()
    suffix = smoke.random_suffix()
    container_name = f"nats-sinks-oracle-nosql-sink-e2e-{suffix}"
    sensitive_values = (container_name,)

    try:
        smoke.validate_args(args)
        smoke.require_borneo()
        host_port = smoke.find_free_port()
        endpoint = f"http://127.0.0.1:{host_port}"
        smoke.run_command(["docker", "version"], timeout=30, sensitive_values=sensitive_values)
        smoke.run_command(
            ["docker", "pull", args.image_ref],
            timeout=900,
            sensitive_values=sensitive_values,
        )
        smoke.run_command(
            smoke.docker_run_args(
                container_name=container_name,
                host_port=host_port,
                image_ref=args.image_ref,
            ),
            timeout=120,
            sensitive_values=sensitive_values,
        )
        smoke.wait_for_tcp_port(port=host_port, timeout_seconds=args.timeout_seconds)
        smoke.wait_for_oracle_nosql_ready(
            endpoint=endpoint,
            table_name=args.table,
            suffix=suffix,
            timeout_seconds=args.timeout_seconds,
        )
        _run_pytest_with_env(smoke, endpoint=endpoint, table=args.table)
        sys.stdout.write("Oracle NoSQL sink container e2e test passed.\n")
        return 0
    except (
        OracleNoSqlSinkE2eError,
        smoke.OracleNoSqlContainerSmokeError,
        subprocess.TimeoutExpired,
    ) as exc:
        safe_error = smoke.redact(str(exc), sensitive_values)
        sys.stderr.write(f"Oracle NoSQL sink e2e test failed: {safe_error}\n")
        return 1
    finally:
        smoke.cleanup(container_name, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
