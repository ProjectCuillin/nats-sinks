#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Run the Oracle Coherence CE sink e2e test against a local test container."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "run-oracle-coherence-container-smoke.py"


class CoherenceSinkE2eError(RuntimeError):
    """Raised when the Oracle Coherence sink e2e workflow fails."""


def _load_smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oracle_coherence_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise CoherenceSinkE2eError("Unable to load Oracle Coherence CE smoke helper.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    """Parse bounded local e2e arguments."""

    smoke = _load_smoke_module()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-tag",
        default=smoke.DEFAULT_IMAGE_TAG,
        help="Local image tag used for the Oracle Coherence CE test backend image.",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=smoke.DEFAULT_DOCKERFILE,
        help="Dockerfile used to build the Oracle Coherence CE test backend image.",
    )
    parser.add_argument(
        "--cache-name",
        default="nats_sinks_sink_e2e",
        help="Named cache used by the Oracle Coherence sink e2e test.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum time to wait for Oracle Coherence CE readiness.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the container for debugging.",
    )
    return parser.parse_args()


def _run_pytest(*, address: str, cache_name: str) -> None:
    env = os.environ.copy()
    env["NATS_SINKS_COHERENCE_INTEGRATION"] = "1"
    env["NATS_SINKS_COHERENCE_ADDRESS"] = address
    env["NATS_SINKS_COHERENCE_CACHE_NAME"] = cache_name
    completed = subprocess.run(  # noqa: S603 - fixed pytest argv list, no shell.
        [sys.executable, "-m", "pytest", "tests/integration/test_coherence_sink_e2e.py", "-q"],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        shell=False,
    )
    if completed.returncode != 0:
        raise CoherenceSinkE2eError(
            "Oracle Coherence sink e2e pytest run failed.\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    sys.stdout.write(completed.stdout)


def main() -> int:
    """Build the local backend image, run sink e2e tests, and clean up."""

    smoke = _load_smoke_module()
    args = parse_args()
    suffix = smoke.random_suffix()
    container_name = f"nats-sinks-oracle-coherence-ce-sink-e2e-{suffix}"
    sensitive_values = (container_name,)

    try:
        smoke.validate_args(args)
        smoke.require_coherence_client()
        host_port = smoke.find_free_port()

        smoke.run_command(["docker", "version"], timeout=30, sensitive_values=sensitive_values)
        smoke.run_command(
            [
                "docker",
                "build",
                "-t",
                args.image_tag,
                "-f",
                str(args.dockerfile.resolve()),
                ".",
            ],
            timeout=900,
            sensitive_values=sensitive_values,
        )
        smoke.run_command(
            smoke.docker_run_args(
                container_name=container_name,
                host_port=host_port,
                image_tag=args.image_tag,
            ),
            timeout=120,
            sensitive_values=sensitive_values,
        )
        smoke.wait_for_tcp_port(port=host_port, timeout_seconds=args.timeout_seconds)
        _run_pytest(
            address=f"127.0.0.1:{host_port}",
            cache_name=args.cache_name,
        )
        sys.stdout.write("Oracle Coherence sink e2e test passed.\n")
        return 0
    except (CoherenceSinkE2eError, smoke.OracleCoherenceSmokeError) as exc:
        safe_error = smoke.redact(str(exc), sensitive_values)
        sys.stderr.write(f"Oracle Coherence sink e2e test failed: {safe_error}\n")
        return 1
    finally:
        smoke.cleanup(container_name, preserve=args.preserve_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
