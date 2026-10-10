#!/usr/bin/env python3
"""Run one strict, side-effect-free readiness probe for a workload container."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import socket
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from workload_scenario import WorkloadScenarioError, validate_workload_scenario


MAX_FRAME_BYTES = 8 * 1024 * 1024
PROVIDER_SCHEMA = "aero-bench.provider-probe/v1"
GATEWAY_SCHEMA = "aero-bench.gateway-probe/v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
PINNED_IMAGE = re.compile(
    r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
)
PORT = re.compile(r"^(?:[1-9][0-9]{3,4})$")


class ProbeError(RuntimeError):
    """A non-secret readiness failure suitable for a container healthcheck."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProbeError(f"non-finite JSON constant: {value}")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProbeError("JSON value cannot be canonicalized") from error


def _parse_canonical_frame(frame: bytes, *, label: str) -> dict[str, Any]:
    if len(frame) > MAX_FRAME_BYTES:
        raise ProbeError(f"{label} exceeds the frame limit")
    if not frame.endswith(b"\n"):
        raise ProbeError(f"{label} must end with a newline")
    try:
        value = json.loads(
            frame,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ProbeError) as error:
        raise ProbeError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ProbeError(f"{label} must be a JSON object")
    if _canonical_json(value) + b"\n" != frame:
        raise ProbeError(f"{label} is not canonical JSON")
    return value


def _exact_object(value: object, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProbeError(f"{label} fields are not exact")
    return value


def _string(
    value: object, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise ProbeError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ProbeError(f"{label} has an invalid value")
    return value


def _digest(value: object, *, label: str) -> str:
    result = _string(value, label=label, pattern=SHA256)
    if result == "0" * 64:
        raise ProbeError(f"{label} cannot be a placeholder digest")
    return result


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProbeError(f"{label} must be an integer >= {minimum}")
    return value


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value:
        raise ProbeError(f"missing required environment value: {name}")
    return value


def _port(name: str) -> int:
    value = _required_environment(name)
    if PORT.fullmatch(value) is None:
        raise ProbeError(f"{name} must be a canonical decimal port")
    port = int(value, 10)
    if not 1024 <= port <= 65535:
        raise ProbeError(f"{name} is outside the allowed port range")
    return port


def _safe_contract_path() -> Path:
    value = _required_environment("AERO_BENCH_CONTRACT")
    path = Path(value)
    if not path.is_absolute():
        raise ProbeError("AERO_BENCH_CONTRACT must be absolute")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProbeError("AERO_BENCH_CONTRACT is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or not resolved.is_file():
        raise ProbeError("AERO_BENCH_CONTRACT must be a regular non-symlink file")
    return resolved


def _safe_bundle_root() -> Path:
    value = _required_environment("AERO_BENCH_BUNDLE_DIR")
    path = Path(value)
    if not path.is_absolute():
        raise ProbeError("AERO_BENCH_BUNDLE_DIR must be absolute")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProbeError("AERO_BENCH_BUNDLE_DIR is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or not resolved.is_dir():
        raise ProbeError(
            "AERO_BENCH_BUNDLE_DIR must be a regular non-symlink directory"
        )
    return resolved


def _relative_file_ref(value: object, *, label: str) -> tuple[str, str]:
    reference = _exact_object(value, {"path", "sha256"}, label=label)
    path_text = _string(reference["path"], label=f"{label}.path")
    path = PurePosixPath(path_text)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or str(path) != path_text
        or "\\" in path_text
    ):
        raise ProbeError(f"{label}.path must be normalized and relative")
    return path_text, _digest(reference["sha256"], label=f"{label}.sha256")


def _verify_file_ref(root: Path, value: object, *, label: str) -> str:
    relative, expected_digest = _relative_file_ref(value, label=label)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ProbeError(f"{label} is unavailable") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ProbeError(f"{label} is not a regular file below the bundle")
    if any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise ProbeError(f"{label} cannot contain a symbolic link")
    try:
        actual_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as error:
        raise ProbeError(f"{label} cannot be read") from error
    if actual_digest != expected_digest:
        raise ProbeError(f"{label} digest does not match its declaration")
    return expected_digest


def _load_contract() -> dict[str, Any]:
    path = _safe_contract_path()
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ProbeError("AERO_BENCH_CONTRACT cannot be read") from error
    return _parse_canonical_frame(raw, label="AERO_BENCH_CONTRACT")


def _provider_identity(contract: dict[str, Any], port: int) -> dict[str, str]:
    expected_run_id = _digest(
        _required_environment("AERO_BENCH_RUN_ID"), label="run_id"
    )
    expected_provider_id = _string(
        _required_environment("AERO_BENCH_WORKLOAD_ID"),
        label="workload_id",
        pattern=IDENTIFIER,
    )
    _exact_object(
        contract,
        {
            "schema_version",
            "role",
            "run_id",
            "seed",
            "workload_id",
            "clock",
            "provider",
            "scenario_digest",
            "scenario",
            "scenario_assets",
        },
        label="ProviderWorkloadContract",
    )
    if (
        contract["schema_version"] != "aero-bench.workload-contract/v5"
        or contract["role"] != "provider"
    ):
        raise ProbeError("AERO_BENCH_CONTRACT is not a provider workload contract")
    if _digest(contract["run_id"], label="contract.run_id") != expected_run_id:
        raise ProbeError("provider run identity mismatch")
    if (
        _string(
            contract["workload_id"], label="contract.workload_id", pattern=IDENTIFIER
        )
        != expected_provider_id
    ):
        raise ProbeError("provider workload identity mismatch")
    provider = _exact_object(
        contract["provider"],
        {
            "provider_id",
            "adapter",
            "port",
            "workload",
            "config",
            "protocol_schema",
            "capabilities",
            "artifact_requirements",
        },
        label="ProviderWorkloadContract.provider",
    )
    provider_id = _string(
        provider["provider_id"], label="provider_id", pattern=IDENTIFIER
    )
    if provider_id != expected_provider_id:
        raise ProbeError("provider_id identity mismatch")
    if _integer(provider["port"], label="provider.port", minimum=1024) != port:
        raise ProbeError("provider port identity mismatch")
    adapter = _string(provider["adapter"], label="provider.adapter", pattern=IDENTIFIER)
    capabilities = provider["capabilities"]
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or any(
            not isinstance(capability, str)
            or IDENTIFIER.fullmatch(capability) is None
            for capability in capabilities
        )
    ):
        raise ProbeError("provider capabilities are invalid")
    seed = _integer(contract["seed"], label="contract.seed")
    try:
        scenario = validate_workload_scenario(
            contract["scenario"],
            expected_seed=seed,
            expected_digest=contract["scenario_digest"],
            role="provider",
            workload_id=provider_id,
            projected_assets=contract["scenario_assets"],
            provider_capabilities=tuple(capabilities),
        )
    except WorkloadScenarioError as error:
        raise ProbeError("provider ResolvedScenario is invalid") from error
    if scenario.scenario_digest != contract["scenario_digest"]:
        raise ProbeError("provider scenario identity mismatch")
    workload = _exact_object(
        provider["workload"],
        {"runtime", "resources", "implementation"},
        label="provider.workload",
    )
    runtime = _exact_object(
        workload["runtime"], {"image", "command"}, label="provider.runtime"
    )
    runtime_image = _string(runtime["image"], label="runtime.image")
    if PINNED_IMAGE.fullmatch(runtime_image) is None or runtime_image.rsplit(
        ":", maxsplit=1
    )[1] == "0" * 64:
        raise ProbeError("provider runtime image is not an immutable non-placeholder digest")
    if (
        not isinstance(runtime["command"], list)
        or not runtime["command"]
        or any(not isinstance(item, str) or not item for item in runtime["command"])
    ):
        raise ProbeError("provider runtime command is invalid")
    config = _exact_object(
        provider["config"], {"file", "schema_file"}, label="provider.config"
    )
    bundle_root = _safe_bundle_root()
    config_digest = _verify_file_ref(
        bundle_root, config["file"], label="provider.config.file"
    )
    _verify_file_ref(
        bundle_root, config["schema_file"], label="provider.config.schema_file"
    )
    _verify_file_ref(
        bundle_root, provider["protocol_schema"], label="provider.protocol_schema"
    )
    return {
        "schema_version": PROVIDER_SCHEMA,
        "status": "accepting",
        "run_id": expected_run_id,
        "provider_id": provider_id,
        "adapter": adapter,
        "runtime_image": runtime_image,
        "config_digest": config_digest,
    }


def _harness_identity(contract: dict[str, Any], port: int) -> str:
    expected_run_id = _digest(
        _required_environment("AERO_BENCH_RUN_ID"), label="run_id"
    )
    _exact_object(
        contract,
        {
            "schema_version",
            "role",
            "workload_id",
            "run",
            "scenario_digest",
            "scenario_assets",
        },
        label="HarnessWorkloadContract",
    )
    if (
        contract["schema_version"] != "aero-bench.harness-workload-contract/v6"
        or contract["role"] != "harness"
    ):
        raise ProbeError("AERO_BENCH_CONTRACT is not a HarnessWorkloadContract")
    if contract["workload_id"] != "harness":
        raise ProbeError("harness workload identity mismatch")
    run = contract["run"]
    if (
        not isinstance(run, dict)
        or _digest(run.get("run_id"), label="contract.run.run_id") != expected_run_id
    ):
        raise ProbeError("harness run identity mismatch")
    if run.get("schema_version") != "aero-bench.resolved-run/v5":
        raise ProbeError("harness ResolvedRun schema version is unsupported")
    scenario_value = run.get("scenario")
    seed = _integer(run.get("seed"), label="contract.run.seed")
    try:
        scenario = validate_workload_scenario(
            scenario_value,
            expected_seed=seed,
            expected_digest=contract["scenario_digest"],
            role="harness",
            workload_id="harness",
            projected_assets=contract["scenario_assets"],
        )
    except WorkloadScenarioError as error:
        raise ProbeError("harness ResolvedScenario is invalid") from error
    if scenario.scenario_digest != contract["scenario_digest"]:
        raise ProbeError("harness scenario identity mismatch")
    environment = run.get("environment")
    if not isinstance(environment, dict):
        raise ProbeError("harness environment contract is invalid")
    gateway = environment.get("gateway")
    if (
        not isinstance(gateway, dict)
        or _integer(gateway.get("port"), label="contract.gateway.port", minimum=1024)
        != port
    ):
        raise ProbeError("harness gateway port identity mismatch")
    return expected_run_id


def _read_one_frame(connection: socket.socket) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = connection.recv(min(65_536, MAX_FRAME_BYTES + 1 - size))
        if not chunk:
            raise ProbeError("probe endpoint closed without a response")
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_FRAME_BYTES:
            raise ProbeError("probe response exceeds the frame limit")
        joined = b"".join(chunks)
        newline = joined.find(b"\n")
        if newline >= 0:
            if newline != len(joined) - 1:
                raise ProbeError("probe endpoint returned more than one frame")
            return joined


def _probe_once(*, role: str, port: int, timeout_seconds: float) -> dict[str, Any]:
    request = _canonical_json({"operation": "probe"}) + b"\n"
    with socket.create_connection(
        ("127.0.0.1", port), timeout=timeout_seconds
    ) as connection:
        connection.settimeout(timeout_seconds)
        connection.sendall(request)
        frame = _read_one_frame(connection)
    response = _parse_canonical_frame(frame, label="probe response")
    if "error" in response:
        raise ProbeError("probe endpoint returned an error")
    if role == "provider":
        expected = _provider_identity(_load_contract(), port)
        if response != expected:
            raise ProbeError("provider probe identity or schema mismatch")
        return response
    expected_run_id = _harness_identity(_load_contract(), port)
    expected = {
        "schema_version": GATEWAY_SCHEMA,
        "status": "ready",
        "run_id": expected_run_id,
        "current": response.get("current"),
    }
    _exact_object(
        response,
        {"schema_version", "status", "run_id", "current"},
        label="gateway probe response",
    )
    if (
        response["schema_version"] != GATEWAY_SCHEMA
        or response["status"] != "ready"
        or response["run_id"] != expected_run_id
    ):
        raise ProbeError("gateway probe identity or schema mismatch")
    current = _exact_object(
        response["current"], {"tick", "sim_time_ns"}, label="gateway current"
    )
    _integer(current["tick"], label="gateway current.tick")
    _integer(current["sim_time_ns"], label="gateway current.sim_time_ns")
    return response


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AERO-BENCH one-shot readiness probe")
    parser.add_argument("--timeout-seconds", default="3", dest="timeout_seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _arguments(argv)
        timeout_seconds = float(arguments.timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ProbeError("timeout must be a finite positive number")
        role = _required_environment("AERO_BENCH_ROLE")
        if role == "provider":
            port = _port("AERO_BENCH_PROVIDER_PORT")
        elif role == "harness":
            port = _port("AERO_BENCH_GATEWAY_PORT")
        else:
            raise ProbeError("AERO_BENCH_ROLE must be provider or harness")
        _probe_once(role=role, port=port, timeout_seconds=timeout_seconds)
    except (OSError, ProbeError, ValueError) as error:
        print(f"readiness probe failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
