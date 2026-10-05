from __future__ import annotations

import json
import os
import re

import pytest

from tests.providers import container_lifecycle


CONTAINER_LABEL = "world-scene-selfcheck"
DOCKER_TIMEOUT_SECONDS = 60.0
RUN_TIMEOUT_SECONDS = 120.0
EXIT_DEADLINE_SECONDS = 60.0
FORMAL_VALIDATION_ENV = "AERO_BENCH_WORLD_SCENE_FORMAL"
DIGEST_IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _skip_or_block(reason: str) -> None:
    if os.environ.get(FORMAL_VALIDATION_ENV) == "1":
        pytest.fail(f"BLOCKED: {reason}")
    pytest.skip(reason)


def test_world_scene_optional_image_runs_current_projection_selfcheck() -> None:
    """The optional image proves source service mechanics, not registry authority."""

    image = os.environ.get("AERO_BENCH_WORLD_SCENE_IMAGE")
    if image is None:
        _skip_or_block("AERO_BENCH_WORLD_SCENE_IMAGE must name the current image")
    if DIGEST_IMAGE_PATTERN.fullmatch(image) is None:
        _skip_or_block("AERO_BENCH_WORLD_SCENE_IMAGE must be digest-pinned")
    try:
        container_lifecycle.docker("info", timeout=DOCKER_TIMEOUT_SECONDS)
    except (OSError, container_lifecycle.DockerCommandError):
        _skip_or_block("Docker daemon is unavailable")
    inspected = container_lifecycle.docker(
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        image,
        timeout=DOCKER_TIMEOUT_SECONDS,
        check=False,
    )
    if inspected.returncode != 0:
        _skip_or_block(f"world-scene integration image is unavailable: {image}")

    with container_lifecycle.owned_container(
        CONTAINER_LABEL,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=256m",
        image,
        "selfcheck",
        run_timeout=RUN_TIMEOUT_SECONDS,
        command_timeout=DOCKER_TIMEOUT_SECONDS,
    ) as container_name:
        exit_code = container_lifecycle.wait_for_exit(
            container_name,
            deadline_seconds=EXIT_DEADLINE_SECONDS,
            timeout=DOCKER_TIMEOUT_SECONDS,
        )
        logs = container_lifecycle.docker(
            "logs", container_name, timeout=DOCKER_TIMEOUT_SECONDS
        )
        assert exit_code == 0, logs.stderr or logs.stdout
        lines = [line for line in logs.stdout.splitlines() if line.strip()]
        assert lines, "world-scene selfcheck produced no result"
        result = json.loads(lines[-1])
        assert result == {
            "config_schema": "aero-bench.world-scene/v2",
            "evidence_sha256": result["evidence_sha256"],
            "projection_kind": "resolved_scenario",
            "protocol_version": "aero-bench.world-scene-rpc/v2",
            "scenario_digest": result["scenario_digest"],
            "scenario_schema": "aero-bench.resolved-scenario/v4",
            "static_authority": "scenario.compiler",
            "status": "read-only-scenario-projection-rpc-ok",
            "steps": 3,
            "workload_contract_schema": "aero-bench.workload-contract/v5",
        }
        assert SHA256_PATTERN.fullmatch(result["evidence_sha256"])
        assert SHA256_PATTERN.fullmatch(result["scenario_digest"])
