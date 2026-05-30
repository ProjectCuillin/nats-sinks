# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local HTTP sink NGINX FIPS test endpoint assets."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

from nats_sinks import NatsEnvelope

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "examples" / "http-nginx-fips-test" / "Dockerfile"
NGINX_CONF = REPO_ROOT / "examples" / "http-nginx-fips-test" / "nginx.conf"
CAPTURE_SERVER = REPO_ROOT / "examples" / "http-nginx-fips-test" / "capture_server.py"
ENTRYPOINT = REPO_ROOT / "examples" / "http-nginx-fips-test" / "entrypoint.sh"
E2E_SCRIPT = REPO_ROOT / "scripts" / "run-http-sink-nginx-e2e.py"


def _load_e2e_script() -> ModuleType:
    """Load the e2e runner as a module for focused behavior checks."""

    spec = importlib.util.spec_from_file_location("run_http_sink_nginx_e2e", E2E_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load HTTP sink NGINX e2e script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_nginx_dockerfile_uses_oracle_linux_9_slim_fips_only() -> None:
    """The test endpoint image should use only the Oracle Linux 9 slim FIPS base."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]

    assert "SPDX-License-Identifier: Apache-2.0" in dockerfile
    assert from_lines == ["FROM container-registry.oracle.com/os/oraclelinux:9-slim-fips"]
    assert (
        'org.opencontainers.image.base.name="container-registry.oracle.com/os/oraclelinux:9-slim-fips"'
        in dockerfile
    )
    assert "microdnf install -y --setopt=install_weak_deps=0 nginx python3" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:8080/health" in dockerfile
    assert "python:" not in dockerfile
    assert "debian:" not in dockerfile
    assert "ubuntu:" not in dockerfile
    assert "alpine:" not in dockerfile


def test_http_nginx_config_exposes_only_health_and_sink_routes() -> None:
    """NGINX should be the endpoint boundary and route only the test paths."""

    config = NGINX_CONF.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in config
    assert "listen 8080;" in config
    assert "location = /health" in config
    assert "proxy_pass http://127.0.0.1:18080/health;" in config
    assert "location = /nats-sink" in config
    assert "limit_except POST PUT PATCH" in config
    assert "proxy_pass http://127.0.0.1:18080/nats-sink;" in config
    assert "fastcgi_temp_path /tmp/nginx/fastcgi;" in config
    assert "uwsgi_temp_path /tmp/nginx/uwsgi;" in config
    assert "scgi_temp_path /tmp/nginx/scgi;" in config
    assert "location /" in config
    assert "return 404;" in config
    assert "server_tokens off;" in config
    assert "access_log /dev/stdout;" in config


def test_http_capture_server_bounds_and_sanitizes_local_request_evidence() -> None:
    """The backend capture helper should keep local evidence bounded and predictable."""

    capture = CAPTURE_SERVER.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in capture
    assert "MAX_BODY_BYTES" in capture
    assert "requests.jsonl" in capture
    assert 'ThreadingHTTPServer(("127.0.0.1", 18080)' in capture
    assert "log_message" in capture
    assert "Suppress default request logging" in capture
    assert "SAFE_HEADERS" in capture
    assert "idempotency-key" in capture
    assert "x-nats-sinks-route" in capture
    assert "os.fsync" in capture
    assert "HTTPStatus.INTERNAL_SERVER_ERROR" in capture
    assert "body_sha256" in capture


def test_http_nginx_entrypoint_starts_capture_before_nginx() -> None:
    """The endpoint should run the capture service behind NGINX."""

    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "SPDX-License-Identifier: Apache-2.0" in entrypoint
    assert (
        "mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi "
        "/tmp/nginx/uwsgi /tmp/nginx/scgi /var/lib/nats-sinks-http"
    ) in entrypoint
    assert "python3 /usr/local/bin/nats-sinks-http-capture &" in entrypoint
    assert "nginx -e /dev/stderr -c /etc/nats-sinks-http/nginx.conf" in entrypoint
    assert "trap stop_capture EXIT INT TERM" in entrypoint


def test_http_nginx_e2e_script_declares_local_contract() -> None:
    """The e2e runner should keep image, path, and port choices explicit."""

    module = _load_e2e_script()

    assert module.DEFAULT_DOCKERFILE == DOCKERFILE
    assert module.DEFAULT_IMAGE_TAG == "nats-sinks-http-nginx-fips-test:local"
    assert module.DEFAULT_CONTAINER_HTTP_PORT == 8080
    assert module.CAPTURE_FILE_IN_CONTAINER == "/var/lib/nats-sinks-http/requests.jsonl"


def test_http_nginx_e2e_script_rejects_unsafe_options(tmp_path: Path) -> None:
    """Operator input should be allow-list checked before Docker is called."""

    module = _load_e2e_script()
    outside_dockerfile = tmp_path / "Dockerfile"
    outside_dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    with pytest.raises(module.HttpNginxE2eError, match="Dockerfile must stay inside"):
        module.validate_args(
            argparse.Namespace(
                dockerfile=outside_dockerfile,
                output_dir=module.DEFAULT_OUTPUT_DIR,
                timeout_seconds=120.0,
                message_count=3,
                image_tag=module.DEFAULT_IMAGE_TAG,
            )
        )

    with pytest.raises(module.HttpNginxE2eError, match="Output directory"):
        module.validate_args(
            argparse.Namespace(
                dockerfile=DOCKERFILE,
                output_dir=tmp_path,
                timeout_seconds=120.0,
                message_count=3,
                image_tag=module.DEFAULT_IMAGE_TAG,
            )
        )

    with pytest.raises(module.HttpNginxE2eError, match="between 30 and 900"):
        module.validate_args(
            argparse.Namespace(
                dockerfile=DOCKERFILE,
                output_dir=module.DEFAULT_OUTPUT_DIR,
                timeout_seconds=1.0,
                message_count=3,
                image_tag=module.DEFAULT_IMAGE_TAG,
            )
        )

    with pytest.raises(module.HttpNginxE2eError, match="between 1 and 100"):
        module.validate_args(
            argparse.Namespace(
                dockerfile=DOCKERFILE,
                output_dir=module.DEFAULT_OUTPUT_DIR,
                timeout_seconds=120.0,
                message_count=101,
                image_tag=module.DEFAULT_IMAGE_TAG,
            )
        )


def test_http_nginx_e2e_script_builds_safe_docker_run_args() -> None:
    """Docker should be invoked through fixed argv lists and loopback binding."""

    module = _load_e2e_script()
    args = module.docker_run_args(
        container_name="nats-sinks-http-nginx-fips-test-abc123",
        host_port=18080,
        image_tag=module.DEFAULT_IMAGE_TAG,
    )

    assert args[:3] == ["docker", "run", "-d"]
    assert "--privileged" not in args
    assert "--network=host" not in args
    assert "--network" not in args
    assert "/var/run/docker.sock" not in " ".join(args)
    assert "--read-only" in args
    assert "--tmpfs" in args
    assert "/var/lib/nats-sinks-http:rw,nosuid,nodev,uid=10001,gid=10001,mode=0750" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--security-opt" in args
    assert "no-new-privileges:true" in args
    assert "-p" in args
    assert "127.0.0.1:18080:8080" in args
    assert args[-1] == module.DEFAULT_IMAGE_TAG


def test_http_nginx_e2e_script_uses_safe_subprocesses() -> None:
    """The Docker runner should avoid shell command construction."""

    script = E2E_SCRIPT.read_text(encoding="utf-8")

    assert "shell=False" in script
    assert "shell=True" not in script
    assert "--preserve-artifacts" in script
    assert '"docker",' in script
    assert '"build",' in script
    assert '"run",' in script
    assert '"exec",' in script
    assert '"cat",' in script
    assert '"rm",' in script
    assert '"-f",' in script


def test_http_nginx_e2e_script_redacts_failed_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed subprocess output should not echo local generated identifiers."""

    module = _load_e2e_script()

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="stdout has local-container-name",
            stderr="stderr has local-container-name",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.HttpNginxE2eError) as exc_info:
        module.run_command(["docker", "bad"], sensitive_values=("local-container-name",))

    message = str(exc_info.value)
    assert "local-container-name" not in message
    assert message.count("<redacted>") == 2


def test_http_nginx_e2e_capture_verification_accepts_expected_records() -> None:
    """Captured HTTP sink envelopes should be validated by idempotency key and payload."""

    module = _load_e2e_script()
    records = [
        {
            "method": "POST",
            "path": "/nats-sink",
            "headers": {
                "idempotency-key": "stream-sequence:HTTP_NGINX_E2E:1",
                "x-nats-sinks-route": "http-nginx-e2e",
            },
            "body": (
                '{"schema":"nats_sinks.http.message.v1",'
                '"subject":"integration.http.nginx",'
                '"payload":{"event_id":"HTTP-NGINX-E2E-0001"}}'
            ),
        }
    ]

    module.verify_capture_records(records, message_count=1)


def test_http_nginx_e2e_capture_verification_fails_closed_on_missing_route() -> None:
    """Missing route evidence should fail rather than silently pass."""

    module = _load_e2e_script()
    records = [
        {
            "method": "POST",
            "path": "/nats-sink",
            "headers": {"idempotency-key": "stream-sequence:HTTP_NGINX_E2E:1"},
            "body": (
                '{"schema":"nats_sinks.http.message.v1",'
                '"subject":"integration.http.nginx",'
                '"payload":{"event_id":"HTTP-NGINX-E2E-0001"}}'
            ),
        }
    ]

    with pytest.raises(module.HttpNginxE2eError, match="route header"):
        module.verify_capture_records(records, message_count=1)


def test_http_nginx_e2e_copy_capture_file_retries_transient_absence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The runner should tolerate a short-lived docker cp evidence race."""

    module = _load_e2e_script()
    calls: list[list[str]] = []

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        sensitive_values: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) == 1:
            raise module.HttpNginxE2eError("request evidence not ready")
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout='{"ok":true}\n', stderr=""
        )

    monkeypatch.setattr(module, "run_command", fake_run_command)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    module.copy_capture_file(
        container_name="nats-sinks-http-test",
        output_file=tmp_path / "requests.jsonl",
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert (tmp_path / "requests.jsonl").read_text(encoding="utf-8") == '{"ok":true}\n'


def test_http_nginx_e2e_message_construction_uses_supported_envelope_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner should construct fake messages before any Docker-only writes."""

    module = _load_e2e_script()
    captured: list[NatsEnvelope] = []

    class FakeHttpSink:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def start(self) -> None:
            return None

        async def write_batch(self, messages: Sequence[NatsEnvelope]) -> None:
            captured.extend(messages)

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(module, "HttpSink", FakeHttpSink)

    module.send_messages(
        endpoint_url="http://127.0.0.1:18080/nats-sink",
        message_count=2,
    )

    assert [message.stream_sequence for message in captured] == [1, 2]
    assert [message.consumer_sequence for message in captured] == [1, 2]
    assert [message.message_id for message in captured] == [
        "http-nginx-e2e-0001",
        "http-nginx-e2e-0002",
    ]


def test_http_nginx_e2e_cleanup_is_default_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup by default keeps short-lived HTTP endpoint tests repeatable."""

    module = _load_e2e_script()
    calls: list[list[str]] = []
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    def fake_run_command(
        args: list[str],
        *,
        timeout: float = 120.0,
        sensitive_values: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    module.cleanup("nats-sinks-http-test", output_dir, preserve=False)

    assert calls == [["docker", "rm", "-f", "nats-sinks-http-test"]]
    assert not output_dir.exists()


def test_http_nginx_e2e_preserve_skips_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The preserve flag should keep local artifacts for explicit debugging."""

    module = _load_e2e_script()
    calls: list[list[str]] = []
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    monkeypatch.setattr(module, "run_command", lambda args, **kwargs: calls.append(args))

    module.cleanup("nats-sinks-http-test", output_dir, preserve=True)

    assert calls == []
    assert output_dir.exists()
