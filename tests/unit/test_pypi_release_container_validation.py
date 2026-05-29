# SPDX-FileCopyrightText: 2026 Johan Louwers <louwersj@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for the local PyPI artifact validation container harness."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "run-pypi-release-container-validation.py"


def _load_script() -> ModuleType:
    """Load the validation script as a module for focused behavior checks."""

    spec = importlib.util.spec_from_file_location("run_pypi_release_container_validation", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load PyPI artifact validation script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_spec_supports_latest_explicit_versions_and_extras() -> None:
    """The pip package spec should stay explicit and shell-independent."""

    module = _load_script()

    assert module.package_spec("latest", ()) == "nats-sinks"
    assert module.package_spec("0.4.1", ()) == "nats-sinks==0.4.1"
    assert module.package_spec("0.4.1", ("crypto", "mysql")) == "nats-sinks[crypto,mysql]==0.4.1"


def test_argument_validation_rejects_unsafe_versions_and_extras(tmp_path: Path) -> None:
    """Package spec input should be allow-listed before it reaches pip."""

    module = _load_script()

    with pytest.raises(module.PyPiArtifactValidationError, match="version"):
        module.validate_version("0.4.1; echo unsafe")
    with pytest.raises(module.PyPiArtifactValidationError, match="Unsupported package extra"):
        module.parse_extras("mysql,unsafe")

    args = argparse.Namespace(
        version="latest",
        extras="",
        report_dir=tmp_path,
        timeout_seconds=900.0,
        image_tag=None,
    )
    with pytest.raises(module.PyPiArtifactValidationError, match="inside the repository"):
        module.validate_args(args)


def test_generated_dockerfile_uses_oracle_linux_slim_base_image() -> None:
    """The post-release validation image should use only the approved Oracle base."""

    module = _load_script()
    dockerfile = module.render_dockerfile()
    from_lines = [
        line.strip() for line in dockerfile.splitlines() if line.strip().startswith("FROM ")
    ]

    assert from_lines == ["FROM container-registry.oracle.com/os/oraclelinux:9-slim"]
    assert "SPDX-License-Identifier: Apache-2.0" in dockerfile
    assert "python3.11" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK NONE" in dockerfile
    assert "python:" not in dockerfile
    assert "debian:" not in dockerfile
    assert "ubuntu:" not in dockerfile
    assert "alpine:" not in dockerfile


def test_generated_validator_checks_expected_artifact_surfaces() -> None:
    """The in-container validator should test the public CLI and import surfaces."""

    module = _load_script()
    validator = module.render_validator_script()

    assert "pip" in validator
    assert "nats-sink" in validator
    assert "nats-sink-metrics" in validator
    assert "nats-sink-observe" in validator
    assert "test-sink" in validator
    assert "validate" in validator
    assert "JsonFileMetrics" in validator
    assert "nats_sinks.__file__" in validator
    assert "Path('/tmp/nats-sinks-artifact/venv').resolve()" in validator


def test_generated_import_snippet_embeds_venv_path_for_isolated_interpreter() -> None:
    """The `python -c` import smoke check cannot rely on wrapper-only variables."""

    module = _load_script()
    validator = module.render_validator_script()
    import_snippet = validator.split("import_code =", maxsplit=1)[1].split(
        "item, completed = check",
        maxsplit=1,
    )[0]

    assert "venv_path = Path('/tmp/nats-sinks-artifact/venv').resolve()" in import_snippet
    assert "venv_path = VENV_DIR.resolve()" not in import_snippet


def test_container_run_command_does_not_mount_the_source_tree() -> None:
    """Only the generated validator file should be bind-mounted into the container."""

    module = _load_script()
    validator = REPO_ROOT / ".local" / "pypi-release-validation" / "work" / "abc" / "validator.py"

    command = module.validation_container_command(
        image_tag="nats-sinks-pypi-release-validation:test",
        container_name="nats-sinks-pypi-release-validation-test",
        validator=validator,
        package_spec_value="nats-sinks==0.4.1",
        version="0.4.1",
        extras=("crypto",),
    )
    joined = " ".join(str(part) for part in command)

    assert "--read-only" in command
    assert "--cap-drop" in command
    assert "no-new-privileges:true" in command
    assert str(REPO_ROOT / "src") not in joined
    assert str(REPO_ROOT / "tests") not in joined
    assert f"type=bind,source={validator},target=/tmp/validator.py,readonly" in command


def test_container_tmpfs_allows_native_python_wheels_to_load() -> None:
    """The validation venv needs executable tmpfs support for native wheels."""

    module = _load_script()
    command = module.validation_container_command(
        image_tag="nats-sinks-pypi-release-validation:test",
        container_name="nats-sinks-pypi-release-validation-test",
        validator=(
            REPO_ROOT / ".local" / "pypi-release-validation" / "work" / "abc" / "validator.py"
        ),
        package_spec_value="nats-sinks==0.4.1",
        version="0.4.1",
        extras=(),
    )

    tmpfs_index = command.index("--tmpfs") + 1
    assert "exec" in command[tmpfs_index].split(",")


def test_run_command_uses_safe_subprocess_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker commands should be executed as argv lists without a shell."""

    module = _load_script()
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    completed = module.run_command(["docker", "version"], timeout=60.0)

    assert completed.returncode == 0
    assert observed["args"] == (["docker", "version"],)
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 60.0


def test_sanitization_redacts_secrets_and_repo_paths() -> None:
    """Sanitized reports should not leak secrets or local checkout paths."""

    module = _load_script()
    text = f"password=abc token=def path={REPO_ROOT}/src ok"

    sanitized = module.sanitize_text(text)

    assert "abc" not in sanitized
    assert "def" not in sanitized
    assert str(REPO_ROOT) not in sanitized
    assert "password=<redacted>" in sanitized
    assert "token=<redacted>" in sanitized
    assert "<repo>/src" in sanitized


def test_report_writer_sanitizes_container_output(tmp_path: Path) -> None:
    """The local report should keep evidence useful without storing secrets."""

    module = _load_script()
    report_path = module.write_report(
        report_dir=tmp_path,
        version="0.4.1",
        extras=("mysql",),
        package_spec_value="nats-sinks[mysql]==0.4.1",
        image_tag="nats-sinks-pypi-release-validation:test",
        container_report={
            "status": "failed",
            "metadata": {"module_path": f"{REPO_ROOT}/src/nats_sinks/__init__.py"},
            "checks": [{"stderr_tail": "password=abc123"}],
        },
    )

    content = report_path.read_text(encoding="utf-8")

    assert "abc123" not in content
    assert str(REPO_ROOT) not in content
    assert "<repo>" in content
    assert "password=<redacted>" in content


def test_cleanup_removes_short_lived_objects_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup should remove containers, images, and generated validator files by default."""

    module = _load_script()
    removed_commands: list[list[str]] = []
    removed_paths: list[Path] = []

    def fake_remove(args: list[str], *, timeout: float) -> None:
        removed_commands.append(args)
        assert timeout == 30.0

    def fake_rmtree(path: Path, *, ignore_errors: bool) -> None:
        removed_paths.append(path)
        assert ignore_errors is True

    monkeypatch.setattr(module, "remove_docker_object", fake_remove)
    monkeypatch.setattr(module.shutil, "rmtree", fake_rmtree)

    module.cleanup(
        container_name="validation-container",
        image_tag="validation-image:test",
        work_dir=REPO_ROOT / ".local" / "pypi-release-validation" / "work" / "abc",
        preserve=False,
        timeout=30.0,
    )

    assert ["docker", "rm", "-f", "validation-container"] in removed_commands
    assert ["docker", "image", "rm", "-f", "validation-image:test"] in removed_commands
    assert removed_paths == [REPO_ROOT / ".local" / "pypi-release-validation" / "work" / "abc"]


def test_parse_container_report_converts_non_json_output_to_failure() -> None:
    """Unexpected container output should become a structured failure report."""

    module = _load_script()
    completed = subprocess.CompletedProcess(
        args=["docker"],
        returncode=1,
        stdout="not json",
        stderr="token=secret-value",
    )

    report = module.parse_container_report(completed)

    assert report["status"] == "failed"
    assert report["checks"][0]["status"] == "failed"
    assert "token=<redacted>" in report["checks"][0]["stderr_tail"]
    assert "secret-value" not in report["checks"][0]["stderr_tail"]
