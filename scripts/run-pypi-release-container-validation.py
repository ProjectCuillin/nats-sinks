#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
"""Validate the public PyPI artifact in a short-lived Oracle Linux container.

This script is a local post-release QA guard. It intentionally installs
`nats-sinks` from PyPI inside a clean container instead of importing the local
checkout. The check is useful after publication, when maintainers need evidence
that external users can install and run the released package.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_IMAGE = "container-registry.oracle.com/os/oraclelinux:9-slim"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / ".local" / "pypi-release-validation"
DEFAULT_REPORT_DIR = DEFAULT_ARTIFACT_DIR / "reports"
DEFAULT_IMAGE_TAG_PREFIX = "nats-sinks-pypi-release-validation"
VALIDATION_TMPFS_SPEC = "/tmp:rw,exec,nosuid,nodev"  # noqa: S108 - bounded venv tmpfs.
VALIDATOR_TARGET = "/tmp/validator.py"  # noqa: S108 - read-only container validator mount.
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 1800
FAILED_OUTPUT_TAIL_CHARS = 4000
ALLOWED_EXTRAS = frozenset({"all", "crypto", "dev", "docs", "mysql", "oracle", "spool", "test"})
VERSION_RE = re.compile(r"^(latest|[0-9][A-Za-z0-9.!+_-]{0,63})$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|private[_-]?key)=\S+"
)


class PyPiArtifactValidationError(RuntimeError):
    """Raised when the local PyPI artifact validation cannot complete safely."""


def parse_args() -> argparse.Namespace:
    """Parse bounded local validation arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default="latest",
        help="PyPI version to verify, or 'latest' for the newest published release.",
    )
    parser.add_argument(
        "--extras",
        default="",
        help=(
            "Comma-separated optional extras to install and smoke-check. Supported values: "
            "all, crypto, dev, docs, mysql, oracle, spool, test."
        ),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Ignored repository-local directory where the sanitized report is written.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=900.0,
        help="Maximum seconds for Docker build and validation commands.",
    )
    parser.add_argument(
        "--image-tag",
        default=None,
        help="Optional local Docker image tag. A random local tag is used by default.",
    )
    parser.add_argument(
        "--preserve-artifacts",
        action="store_true",
        help="Keep the temporary image, container, and generated validator files.",
    )
    return parser.parse_args()


def validate_version(version: str) -> str:
    """Return a safe version token or reject unsafe package spec input."""

    normalized = version.strip()
    if not VERSION_RE.fullmatch(normalized):
        raise PyPiArtifactValidationError(
            "--version must be 'latest' or a simple PyPI version such as 0.4.1."
        )
    return normalized


def parse_extras(raw_extras: str) -> tuple[str, ...]:
    """Normalize and allow-list optional package extras."""

    if not raw_extras.strip():
        return ()
    extras = tuple(sorted({part.strip().lower() for part in raw_extras.split(",") if part.strip()}))
    invalid = [extra for extra in extras if extra not in ALLOWED_EXTRAS]
    if invalid:
        raise PyPiArtifactValidationError(f"Unsupported package extra(s): {', '.join(invalid)}")
    return extras


def package_spec(version: str, extras: tuple[str, ...]) -> str:
    """Build the exact PyPI package spec passed to pip as one argv value."""

    extra_spec = f"[{','.join(extras)}]" if extras else ""
    if version == "latest":
        return f"nats-sinks{extra_spec}"
    return f"nats-sinks{extra_spec}=={version}"


def random_suffix() -> str:
    """Return a compact random suffix for short-lived Docker object names."""

    return secrets.token_hex(8)


def validate_args(args: argparse.Namespace) -> tuple[str, tuple[str, ...], Path, str]:
    """Validate local arguments before Docker is called."""

    version = validate_version(args.version)
    extras = parse_extras(args.extras)
    report_dir = args.report_dir.resolve()
    try:
        report_dir.relative_to(REPO_ROOT)
    except ValueError as exc:
        message = "Report directory must stay inside the repository."
        raise PyPiArtifactValidationError(message) from exc

    if args.timeout_seconds < MIN_TIMEOUT_SECONDS or args.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise PyPiArtifactValidationError("--timeout-seconds must be between 60 and 1800.")

    image_tag = args.image_tag or f"{DEFAULT_IMAGE_TAG_PREFIX}:{random_suffix()}"
    if not IMAGE_TAG_RE.fullmatch(image_tag):
        raise PyPiArtifactValidationError("Docker image tag contains unsupported characters.")
    return version, extras, report_dir, image_tag


def sanitize_text(value: str) -> str:
    """Return log/report-safe text without control characters or obvious secrets."""

    sanitized = CONTROL_CHAR_RE.sub("?", value)
    sanitized = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", sanitized)
    sanitized = sanitized.replace(str(REPO_ROOT), "<repo>")
    return sanitized


def output_tail(value: str) -> str:
    """Return a sanitized tail suitable for operator-facing failure reports."""

    return sanitize_text(value[-FAILED_OUTPUT_TAIL_CHARS:])


def emit(message: str, *, stderr: bool = False) -> None:
    """Write one operator-facing line without using shell-oriented formatting."""

    stream = sys.stderr if stderr else sys.stdout
    stream.write(f"{message}\n")


def render_dockerfile() -> str:
    """Render the temporary Oracle Linux validation image Dockerfile."""

    return f"""# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
FROM {DEFAULT_BASE_IMAGE}

RUN microdnf install -y --setopt=install_weak_deps=0 \\
        ca-certificates \\
        python3.11 \\
        python3.11-pip \\
        shadow-utils \\
    && microdnf clean all \\
    && rm -rf /var/cache/dnf

RUN groupadd --gid 10001 nats-sinks \\
    && useradd --uid 10001 --gid 10001 --home-dir /home/nats-sinks \\
        --create-home --shell /sbin/nologin nats-sinks

USER 10001:10001
WORKDIR /
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PYTHONUNBUFFERED=1 \\
    XDG_CACHE_HOME=/tmp/nats-sinks-cache
HEALTHCHECK NONE
"""


def render_validator_script() -> str:
    """Render the Python script executed inside the validation container."""

    return r'''#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

WORK_DIR = Path("/tmp/nats-sinks-artifact")
VENV_DIR = WORK_DIR / "venv"
CONFIG_FILE = WORK_DIR / "file-sink-config.json"
METRICS_FILE = WORK_DIR / "metrics.json"
EVENT_DIR = WORK_DIR / "events"
TIMEOUT_SECONDS = 240


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-spec", required=True)
    parser.add_argument("--requested-version", required=True)
    parser.add_argument("--extras", default="")
    return parser.parse_args()


def check(name, args, *, env=None, cwd=WORK_DIR, timeout=TIMEOUT_SECONDS):
    started = time.monotonic()
    completed = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    item = {
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "elapsed_ms": elapsed_ms,
    }
    if completed.returncode != 0:
        item["stdout_tail"] = completed.stdout[-2000:]
        item["stderr_tail"] = completed.stderr[-2000:]
    return item, completed


def write_config():
    config = {
        "nats": {
            "url": "nats://127.0.0.1:4222",
            "stream": "PYPI_ARTIFACT_SMOKE",
            "consumer": "pypi-artifact-file-sink",
            "subject": "pypi.artifact.*",
            "durable": True,
        },
        "delivery": {
            "batch_size": 8,
            "batch_timeout_ms": 500,
            "ack_policy": "after_sink_commit",
            "temporary_failure_action": "nak",
        },
        "dead_letter": {
            "enabled": False,
            "subject": "pypi.artifact.dlq",
            "include_payload": False,
            "include_headers": True,
            "include_error": True,
        },
        "logging": {"level": "INFO", "payload_logging": False},
        "message_metadata": {
            "priority": {"header": "Nats-Sinks-Priority", "default": "routine"},
            "classification": {
                "header": "Nats-Sinks-Classification",
                "default": "NATO UNCLASSIFIED",
            },
            "labels": {"header": "Nats-Sinks-Labels", "default": ["pypi-artifact-smoke"]},
        },
        "sink": {
            "type": "file",
            "directory": str(EVENT_DIR),
            "mode": "one_file_per_message",
            "filename_strategy": "stream_sequence",
            "duplicate_policy": "skip_existing",
            "payload_mode": "json_or_envelope",
            "extension": ".json",
            "compression": "none",
            "compression_level": 6,
            "include_metadata": True,
            "partition_by_subject": True,
            "create_directory": True,
            "fsync": False,
        },
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def python_snippet(code):
    return [str(VENV_DIR / "bin" / "python"), "-c", textwrap.dedent(code)]


def main():
    args = parse_args()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "home").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(WORK_DIR / "home")
    env["PATH"] = f"{VENV_DIR / 'bin'}:{env.get('PATH', '')}"
    env["PYTHONNOUSERSITE"] = "1"

    checks = []
    metadata = {
        "package_spec": args.package_spec,
        "requested_version": args.requested_version,
        "extras": [extra for extra in args.extras.split(",") if extra],
    }

    for name, command in (
        ("create virtual environment", ["python3.11", "-m", "venv", str(VENV_DIR)]),
        (
            "install PyPI artifact",
            [
                str(VENV_DIR / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--quiet",
                args.package_spec,
            ],
        ),
    ):
        item, _ = check(name, command, env=env)
        checks.append(item)
        if item["status"] != "passed":
            print(json.dumps({"status": "failed", "metadata": metadata, "checks": checks}))
            return 1

    import_code = """
    import json
    from pathlib import Path

    import nats_sinks
    from nats_sinks import JetStreamSinkRunner
    from nats_sinks.file import FileSink
    from nats_sinks.core.config import load_config

    module_path = Path(nats_sinks.__file__).resolve()
    venv_path = Path('/tmp/nats-sinks-artifact/venv').resolve()
    if venv_path not in module_path.parents:
        raise SystemExit(f"nats_sinks imported outside validation venv: {module_path}")
    print(json.dumps({"version": nats_sinks.__version__, "module_path": str(module_path)}))
    """
    item, completed = check(
        "import installed package from validation venv",
        python_snippet(import_code),
        env=env,
    )
    checks.append(item)
    if item["status"] == "passed":
        import_info = json.loads(completed.stdout.strip())
        metadata["installed_version"] = import_info["version"]
        metadata["module_path"] = import_info["module_path"]
        if args.requested_version != "latest" and import_info["version"] != args.requested_version:
            checks.append(
                {
                    "name": "explicit version matches installed package",
                    "status": "failed",
                    "exit_code": 1,
                    "elapsed_ms": 0,
                    "stderr_tail": (
                        f"requested {args.requested_version}, installed {import_info['version']}"
                    ),
                }
            )
            print(json.dumps({"status": "failed", "metadata": metadata, "checks": checks}))
            return 1
        checks.append(
            {
                "name": "explicit version matches installed package",
                "status": "passed",
                "exit_code": 0,
                "elapsed_ms": 0,
            }
        )
    else:
        print(json.dumps({"status": "failed", "metadata": metadata, "checks": checks}))
        return 1

    write_config()
    cli_commands = [
        ("nats-sink version", [str(VENV_DIR / "bin" / "nats-sink"), "--version"]),
        ("nats-sink help", [str(VENV_DIR / "bin" / "nats-sink"), "--help"]),
        (
            "nats-sink validate file config",
            [str(VENV_DIR / "bin" / "nats-sink"), "validate", str(CONFIG_FILE)],
        ),
        (
            "nats-sink file sink smoke",
            [str(VENV_DIR / "bin" / "nats-sink"), "test-sink", str(CONFIG_FILE)],
        ),
        ("nats-sink-metrics version", [str(VENV_DIR / "bin" / "nats-sink-metrics"), "--version"]),
        ("nats-sink-metrics help", [str(VENV_DIR / "bin" / "nats-sink-metrics"), "--help"]),
        (
            "nats-sink-metrics describe",
            [str(VENV_DIR / "bin" / "nats-sink-metrics"), "describe", "--format", "names"],
        ),
        ("nats-sink-observe version", [str(VENV_DIR / "bin" / "nats-sink-observe"), "--version"]),
        ("nats-sink-observe help", [str(VENV_DIR / "bin" / "nats-sink-observe"), "--help"]),
    ]
    for name, command in cli_commands:
        item, _ = check(name, command, env=env)
        checks.append(item)

    metrics_code = f"""
    from nats_sinks.core.metrics import JsonFileMetrics, MetricNames, increment_metric

    metrics = JsonFileMetrics({str(METRICS_FILE)!r}, namespace="pypi_artifact")
    increment_metric(metrics, MetricNames.MESSAGES_FETCHED_TOTAL, 3)
    increment_metric(metrics, MetricNames.MESSAGES_WRITTEN_TOTAL, 3)
    """
    item, _ = check("create metrics snapshot", python_snippet(metrics_code), env=env)
    checks.append(item)
    if item["status"] == "passed":
        for name, command in (
            (
                "nats-sink-metrics show json",
                [
                    str(VENV_DIR / "bin" / "nats-sink-metrics"),
                    "show",
                    str(METRICS_FILE),
                    "--format",
                    "json",
                ],
            ),
            (
                "nats-sink-metrics get counter",
                [
                    str(VENV_DIR / "bin" / "nats-sink-metrics"),
                    "get",
                    str(METRICS_FILE),
                    "messages_fetched_total",
                ],
            ),
        ):
            metric_item, _ = check(name, command, env=env)
            checks.append(metric_item)

    extra_imports = {
        "crypto": "import cryptography",
        "mysql": "import mysql.connector",
        "oracle": "import oracledb",
        "spool": "from nats_sinks.spool import SpoolSink",
    }
    selected_extras = set(metadata["extras"])
    if "all" in selected_extras:
        selected_extras.update(extra_imports)
    for extra in sorted(selected_extras):
        code = extra_imports.get(extra)
        if code is None:
            continue
        item, _ = check(f"optional extra import {extra}", python_snippet(code), env=env)
        checks.append(item)

    status = "passed" if all(item["status"] == "passed" for item in checks) else "failed"
    print(json.dumps({"status": status, "metadata": metadata, "checks": checks}, sort_keys=True))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def write_validation_assets(work_dir: Path) -> tuple[Path, Path]:
    """Write the temporary Dockerfile and in-container validator."""

    work_dir.mkdir(parents=True, exist_ok=True)
    dockerfile = work_dir / "Dockerfile"
    validator = work_dir / "validator.py"
    dockerfile.write_text(render_dockerfile(), encoding="utf-8")
    validator.write_text(render_validator_script(), encoding="utf-8")
    validator.chmod(0o700)
    return dockerfile, validator


def run_command(
    args: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed-argument subprocess without invoking a shell."""

    return subprocess.run(  # noqa: S603 - fixed Docker argv lists are assembled by this script.
        args,
        check=False,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
    )


def raise_on_failure(
    completed: subprocess.CompletedProcess[str],
    *,
    command_name: str,
) -> None:
    """Turn a failed command into a sanitized validation error."""

    if completed.returncode == 0:
        return
    raise PyPiArtifactValidationError(
        f"{command_name} failed with exit code {completed.returncode}\n"
        f"stdout:\n{output_tail(completed.stdout)}\n"
        f"stderr:\n{output_tail(completed.stderr)}"
    )


def failed_command_report(
    *,
    name: str,
    completed: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    """Return a sanitized check entry for a command that failed before validation."""

    return {
        "name": name,
        "status": "failed",
        "exit_code": completed.returncode,
        "elapsed_ms": 0,
        "stdout_tail": output_tail(completed.stdout),
        "stderr_tail": output_tail(completed.stderr),
    }


def docker_build_command(*, dockerfile: Path, image_tag: str, work_dir: Path) -> list[str]:
    """Return the safe Docker build argv."""

    return ["docker", "build", "--pull", "-f", str(dockerfile), "-t", image_tag, str(work_dir)]


def validation_container_command(
    *,
    image_tag: str,
    container_name: str,
    validator: Path,
    package_spec_value: str,
    version: str,
    extras: tuple[str, ...],
) -> list[str]:
    """Return the safe Docker run argv for the PyPI artifact validation."""

    return [
        "docker",
        "run",
        "--name",
        container_name,
        "--read-only",
        "--tmpfs",
        VALIDATION_TMPFS_SPEC,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--network",
        "bridge",
        "--env",
        "HOME=/tmp/nats-sinks-home",
        "--mount",
        f"type=bind,source={validator},target={VALIDATOR_TARGET},readonly",
        image_tag,
        "python3.11",
        VALIDATOR_TARGET,
        "--package-spec",
        package_spec_value,
        "--requested-version",
        version,
        "--extras",
        ",".join(extras),
    ]


def remove_docker_object(args: list[str], *, timeout: float) -> None:
    """Best-effort cleanup for short-lived local Docker objects."""

    _ = run_command(args, timeout=timeout)


def cleanup(
    *,
    container_name: str,
    image_tag: str,
    work_dir: Path,
    preserve: bool,
    timeout: float,
) -> None:
    """Remove temporary Docker objects and generated validator files by default."""

    if preserve:
        emit(f"Preserved validation container artifacts under {work_dir}")
        emit(f"Preserved Docker image tag: {image_tag}")
        return
    remove_docker_object(["docker", "rm", "-f", container_name], timeout=timeout)
    remove_docker_object(["docker", "image", "rm", "-f", image_tag], timeout=timeout)
    shutil.rmtree(work_dir, ignore_errors=True)


def parse_container_report(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Parse the JSON report emitted by the in-container validator."""

    try:
        return json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "failed",
            "metadata": {},
            "checks": [
                {
                    "name": "parse container validator output",
                    "status": "failed",
                    "exit_code": completed.returncode,
                    "elapsed_ms": 0,
                    "stdout_tail": output_tail(completed.stdout),
                    "stderr_tail": output_tail(completed.stderr),
                }
            ],
        }


def sanitize_report(value: Any) -> Any:
    """Recursively sanitize report strings before they are written locally."""

    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_report(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_report(item) for key, item in value.items()}
    return value


def write_report(
    *,
    report_dir: Path,
    version: str,
    extras: tuple[str, ...],
    package_spec_value: str,
    image_tag: str,
    container_report: dict[str, Any],
) -> Path:
    """Write a sanitized local JSON report and return its path."""

    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    version_part = version.replace(".", "_").replace("+", "_").replace("-", "_")
    suffix = random_suffix()
    report_path = report_dir / f"nats-sinks-pypi-artifact-{version_part}-{timestamp}-{suffix}.json"
    report = {
        "schema": "nats_sinks.pypi_artifact_validation.v1",
        "generated_at_epoch_seconds": int(time.time()),
        "requested_version": version,
        "requested_extras": extras,
        "package_spec": package_spec_value,
        "base_image": DEFAULT_BASE_IMAGE,
        "validation_image_tag": image_tag,
        "container_report": container_report,
    }
    report_path.write_text(
        json.dumps(sanitize_report(report), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    return report_path


def run_validation(args: argparse.Namespace) -> int:
    """Run the full local PyPI artifact validation flow."""

    version, extras, report_dir, image_tag = validate_args(args)
    package_spec_value = package_spec(version, extras)
    suffix = random_suffix()
    work_dir = DEFAULT_ARTIFACT_DIR / "work" / suffix
    container_name = f"nats-sinks-pypi-release-validation-{suffix}"
    dockerfile, validator = write_validation_assets(work_dir)
    container_report: dict[str, Any] = {
        "status": "failed",
        "metadata": {},
        "checks": [],
    }

    try:
        build = run_command(
            docker_build_command(dockerfile=dockerfile, image_tag=image_tag, work_dir=work_dir),
            timeout=args.timeout_seconds,
        )
        if build.returncode != 0:
            container_report = {
                "status": "failed",
                "metadata": {
                    "package_spec": package_spec_value,
                    "requested_version": version,
                    "extras": list(extras),
                },
                "checks": [failed_command_report(name="Docker image build", completed=build)],
            }
        else:
            completed = run_command(
                validation_container_command(
                    image_tag=image_tag,
                    container_name=container_name,
                    validator=validator,
                    package_spec_value=package_spec_value,
                    version=version,
                    extras=extras,
                ),
                timeout=args.timeout_seconds,
            )
            container_report = parse_container_report(completed)
            if completed.returncode != 0 and container_report.get("status") == "passed":
                container_report["status"] = "failed"
    finally:
        cleanup(
            container_name=container_name,
            image_tag=image_tag,
            work_dir=work_dir,
            preserve=args.preserve_artifacts,
            timeout=30.0,
        )

    report_path = write_report(
        report_dir=report_dir,
        version=version,
        extras=extras,
        package_spec_value=package_spec_value,
        image_tag=image_tag,
        container_report=container_report,
    )
    status = str(container_report.get("status", "failed"))
    installed_version = (
        container_report.get("metadata", {}).get("installed_version")
        if isinstance(container_report.get("metadata"), dict)
        else None
    )
    emit(f"PyPI artifact validation status: {status}")
    emit(f"Requested version: {version}")
    if installed_version:
        emit(f"Installed version: {installed_version}")
    emit(f"Report: {report_path.relative_to(REPO_ROOT)}")
    return 0 if status == "passed" else 1


def main() -> int:
    """CLI entrypoint."""

    args = parse_args()
    try:
        return run_validation(args)
    except PyPiArtifactValidationError as exc:
        emit(f"PyPI artifact validation error: {exc}", stderr=True)
        return 2
    except subprocess.TimeoutExpired as exc:
        emit(f"PyPI artifact validation timed out: {exc}", stderr=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
