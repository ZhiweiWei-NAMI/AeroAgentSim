from __future__ import annotations

import hashlib
import http.client
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from aero_bench.agent.codex_driver import (
    BridgeToolClient,
    CodexDriverError,
    DriverContext,
    run_driver,
)
from aero_bench.agent.session_contracts import (
    SessionDescriptor,
    ToolCallRequest,
    ToolExecutionResult,
)
from aero_bench.config.models import FileRef
from aero_bench.providers.rpc import ProviderRpcError, parse_json_object
from aero_bench.serialization import canonical_json_bytes


CODEX_BINARY = Path("/opt/codex/codex")
_MAX_AUTH_BYTES = 1024 * 1024
_MAX_BRIDGE_RESPONSE_BYTES = 32 * 1024 * 1024
_SESSION_STARTUP_TIMEOUT_S = 180
_HOST_PATTERN = re.compile(r"^(?:localhost|[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)$")


class DriverEntrypointError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value:
        raise DriverEntrypointError(
            "driver.environment.missing",
            f"required driver environment is missing: {name}",
        )
    return value


def _normalized_existing_path(raw: str, *, label: str, expected_kind: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        raise DriverEntrypointError(
            "driver.environment.invalid", f"{label} must be absolute and normalized"
        )
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DriverEntrypointError(
            "driver.environment.invalid", f"{label} is unavailable"
        ) from error
    if resolved != path:
        raise DriverEntrypointError(
            "driver.environment.invalid", f"{label} contains a symbolic link"
        )
    if expected_kind == "file" and not resolved.is_file():
        raise DriverEntrypointError(
            "driver.environment.invalid", f"{label} must be a file"
        )
    if expected_kind == "directory" and not resolved.is_dir():
        raise DriverEntrypointError(
            "driver.environment.invalid", f"{label} must be a directory"
        )
    return resolved


class BundleFileReader:
    """Read only digest-declared files below one materialized bundle root."""

    def __init__(self, root: Path, *, maximum_bytes: int = 16 * 1024 * 1024):
        self.root = _normalized_existing_path(
            os.fspath(root), label="AERO_BENCH_BUNDLE_DIR", expected_kind="directory"
        )
        self.maximum_bytes = maximum_bytes

    def __call__(self, reference: FileRef) -> bytes:
        relative = PurePosixPath(reference.path)
        candidate = self.root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            stat = resolved.stat()
        except (OSError, RuntimeError) as error:
            raise DriverEntrypointError(
                "driver.bundle.unavailable",
                "declared driver bundle input is unavailable",
            ) from error
        if resolved != candidate or not resolved.is_file():
            raise DriverEntrypointError(
                "driver.bundle.unsafe", "declared driver bundle input path is unsafe"
            )
        if not 1 <= stat.st_size <= self.maximum_bytes:
            raise DriverEntrypointError(
                "driver.bundle.oversize",
                "declared driver bundle input has invalid size",
            )
        try:
            payload = resolved.read_bytes()
        except OSError as error:
            raise DriverEntrypointError(
                "driver.bundle.unavailable",
                "declared driver bundle input could not be read",
            ) from error
        if len(payload) != stat.st_size:
            raise DriverEntrypointError(
                "driver.bundle.changed",
                "declared driver bundle input changed while reading",
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != reference.sha256:
            raise DriverEntrypointError(
                "driver.bundle.changed", "declared driver bundle input digest differs"
            )
        return payload


class _BridgeUnavailable(RuntimeError):
    pass


class BridgeHttpClient(BridgeToolClient):
    """Bearer-authenticated exact HTTP client; tool POSTs are never retried."""

    def __init__(self, *, host: str, port: int, token: str):
        if _HOST_PATTERN.fullmatch(host) is None:
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge host is invalid"
            )
        if not 1024 <= port <= 65535:
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge port is invalid"
            )
        if re.fullmatch(r"[0-9a-f]{64}", token) is None or token == "0" * 64:
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge token is invalid"
            )
        self._host = host
        self._port = port
        self._token = token

    def __repr__(self) -> str:
        return f"BridgeHttpClient(host={self._host!r}, port={self._port!r}, token=<redacted>)"

    def _request(
        self,
        *,
        method: str,
        path: str,
        body: bytes | None,
        timeout_s: float,
        startup_probe: bool,
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection(
            self._host, self._port, timeout=max(0.1, timeout_s)
        )
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._token,
            "Connection": "close",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            declared_length = response.getheader("Content-Length")
            if declared_length is not None:
                if not declared_length.isascii() or not declared_length.isdecimal():
                    raise DriverEntrypointError(
                        "driver.bridge.invalid",
                        "session bridge content length is invalid",
                    )
                if int(declared_length) > _MAX_BRIDGE_RESPONSE_BYTES:
                    raise DriverEntrypointError(
                        "driver.bridge.oversize", "session bridge response is too large"
                    )
            raw = response.read(_MAX_BRIDGE_RESPONSE_BYTES + 1)
            status = response.status
            content_type = response.getheader("Content-Type", "")
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            if startup_probe:
                raise _BridgeUnavailable from error
            raise DriverEntrypointError(
                "driver.bridge.unavailable", "authorized tool bridge request failed"
            ) from error
        finally:
            connection.close()
        if startup_probe and status in {425, 429, 503}:
            raise _BridgeUnavailable
        if status != 200:
            raise DriverEntrypointError(
                "driver.bridge.rejected", "session bridge rejected the request"
            )
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge response media type is invalid"
            )
        if not raw or len(raw) > _MAX_BRIDGE_RESPONSE_BYTES:
            raise DriverEntrypointError(
                "driver.bridge.oversize", "session bridge response size is invalid"
            )
        try:
            value = parse_json_object(raw)
        except ProviderRpcError as error:
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge response is not strict JSON"
            ) from error
        if raw != canonical_json_bytes(value):
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge response is not canonical JSON"
            )
        return value

    def wait_for_session(
        self,
        *,
        timeout_s: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> SessionDescriptor:
        deadline = monotonic() + timeout_s
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise DriverEntrypointError(
                    "driver.bridge.timeout", "session bridge did not become ready"
                )
            try:
                value = self._request(
                    method="GET",
                    path="/session",
                    body=None,
                    timeout_s=min(10.0, remaining),
                    startup_probe=True,
                )
            except _BridgeUnavailable:
                sleep(min(1.0, max(0.0, deadline - monotonic())))
                continue
            try:
                return SessionDescriptor.model_validate(value)
            except Exception as error:
                raise DriverEntrypointError(
                    "driver.bridge.invalid", "session bridge descriptor is invalid"
                ) from error

    def execute_tool(
        self, request: ToolCallRequest, *, timeout_s: int
    ) -> ToolExecutionResult:
        body = canonical_json_bytes(request.model_dump(mode="json"))
        if len(body) > 2 * 1024 * 1024:
            raise DriverEntrypointError(
                "driver.bridge.oversize", "authorized tool request is too large"
            )
        value = self._request(
            method="POST",
            path="/tool",
            body=body,
            timeout_s=timeout_s,
            startup_probe=False,
        )
        try:
            return ToolExecutionResult.model_validate(value)
        except Exception as error:
            raise DriverEntrypointError(
                "driver.bridge.invalid", "session bridge tool result is invalid"
            ) from error


@dataclass(frozen=True, slots=True)
class _BridgeApi:
    AgentDriverConfig: type[Any]
    compile_tools: Callable[[Any, Callable[[FileRef], bytes]], Any]
    build_descriptor: Callable[
        [Any, str, Callable[[FileRef], bytes]], SessionDescriptor
    ]


def _default_bridge_api() -> _BridgeApi:
    from aero_bench.agent import bridge

    return _BridgeApi(
        AgentDriverConfig=bridge.AgentDriverConfig,
        compile_tools=bridge.compile_tools,
        build_descriptor=bridge.build_descriptor,
    )


def _default_contract_model() -> type[Any]:
    from aero_bench.executor.contracts import AgentDriverWorkloadContract

    return AgentDriverWorkloadContract


def _strict_contract(path: Path, contract_model: type[Any]) -> Any:
    try:
        raw = path.read_bytes()
        if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise ValueError("contract framing")
        document = parse_json_object(raw[:-1])
        if raw != canonical_json_bytes(document) + b"\n":
            raise ValueError("contract canonical encoding")
        return contract_model.model_validate(document)
    except Exception as error:
        raise DriverEntrypointError(
            "driver.contract.invalid", "AgentDriverWorkloadContract is invalid"
        ) from error


def _schema_document(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = parse_json_object(payload)
        Draft202012Validator.check_schema(document)
        return document
    except Exception as error:
        raise DriverEntrypointError(
            "driver.bundle.invalid", f"{label} is not a valid JSON Schema"
        ) from error


def _validate_all_declared_inputs(contract: Any, reader: BundleFileReader) -> None:
    context = contract.context
    driver = context.agent.driver
    if driver is None:
        raise DriverEntrypointError(
            "driver.contract.invalid", "driver contract has no AgentDriverSpec"
        )
    reader(context.instruction)
    config_document = parse_json_object(reader(driver.config.file))
    config_schema = _schema_document(
        reader(driver.config.schema_file), label="agent driver config schema"
    )
    try:
        Draft202012Validator(config_schema).validate(config_document)
    except Exception as error:
        raise DriverEntrypointError(
            "driver.bundle.invalid", "agent driver config violates its schema"
        ) from error
    schema_refs: list[FileRef] = []
    for grant in context.agent.tools:
        schema_refs.extend((grant.request_schema, grant.response_schema))
    for grant in context.agent.queries:
        schema_refs.extend((grant.request_schema, grant.response_schema))
    for grant in context.agent.observations:
        schema_refs.append(grant.schema_file)
    for reference in schema_refs:
        _schema_document(reader(reference), label="agent grant schema")


def _artifact_budgets(contract: Any) -> tuple[int, int]:
    driver = contract.context.agent.driver
    assert driver is not None
    by_type = {item.artifact_type: item for item in driver.artifact_requirements}
    expected_paths = {
        "model.session-manifest": "model.session-manifest.json",
        "model.interactions": "model.interactions.jsonl",
    }
    if set(by_type) != set(expected_paths):
        raise DriverEntrypointError(
            "driver.contract.invalid", "driver artifact declarations are incomplete"
        )
    for artifact_type, expected_path in expected_paths.items():
        item = by_type[artifact_type]
        if item.relative_path != expected_path or item.producer_id != driver.driver_id:
            raise DriverEntrypointError(
                "driver.contract.invalid", "driver artifact path or producer is invalid"
            )
    return (
        by_type["model.interactions"].max_size_bytes,
        by_type["model.session-manifest"].max_size_bytes,
    )


def _copy_codex_auth(source: Path, codex_home: Path) -> None:
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(source_fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 1 <= metadata.st_size <= _MAX_AUTH_BYTES
            ):
                raise ValueError("auth file type or size")
            payload = stream.read(_MAX_AUTH_BYTES + 1)
            if len(payload) != metadata.st_size:
                raise ValueError("auth changed")
        parse_json_object(payload)
        destination = codex_home / "auth.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except Exception as error:
        raise DriverEntrypointError(
            "driver.auth.invalid", "executor-supplied Codex auth input is invalid"
        ) from error


def run_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    codex_binary: Path = CODEX_BINARY,
    bridge_api: _BridgeApi | None = None,
    contract_model: type[Any] | None = None,
    driver_runner: Callable[
        [DriverContext, SessionDescriptor, BridgeToolClient], Any
    ] = run_driver,
) -> Any:
    values = os.environ if environment is None else environment
    input_root = _normalized_existing_path(
        _required(values, "AERO_BENCH_INPUT_DIR"),
        label="AERO_BENCH_INPUT_DIR",
        expected_kind="directory",
    )
    contract_path = _normalized_existing_path(
        _required(values, "AERO_BENCH_CONTRACT"),
        label="AERO_BENCH_CONTRACT",
        expected_kind="file",
    )
    bundle_root = _normalized_existing_path(
        _required(values, "AERO_BENCH_BUNDLE_DIR"),
        label="AERO_BENCH_BUNDLE_DIR",
        expected_kind="directory",
    )
    artifact_root = _normalized_existing_path(
        _required(values, "AERO_BENCH_ARTIFACT_DIR"),
        label="AERO_BENCH_ARTIFACT_DIR",
        expected_kind="directory",
    )
    auth_file = _normalized_existing_path(
        _required(values, "AERO_BENCH_CODEX_AUTH_FILE"),
        label="AERO_BENCH_CODEX_AUTH_FILE",
        expected_kind="file",
    )
    if (
        contract_path != input_root / "contract.json"
        or bundle_root != input_root / "bundle"
        or auth_file != input_root / "runtime-secrets" / "codex-auth.json"
    ):
        raise DriverEntrypointError(
            "driver.environment.invalid",
            "driver input paths differ from executor layout",
        )
    api = _default_bridge_api() if bridge_api is None else bridge_api
    model = _default_contract_model() if contract_model is None else contract_model
    contract = _strict_contract(contract_path, model)
    context = contract.context
    driver = context.agent.driver
    if driver is None:
        raise DriverEntrypointError(
            "driver.contract.invalid", "driver contract has no AgentDriverSpec"
        )
    try:
        seed_text = _required(values, "AERO_BENCH_SEED")
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", seed_text) is None:
            raise ValueError("seed")
        seed = int(seed_text)
        port_text = _required(values, "AERO_BENCH_SESSION_PORT")
        if re.fullmatch(r"[1-9][0-9]{3,4}", port_text) is None:
            raise ValueError("port")
        port = int(port_text)
    except ValueError as error:
        raise DriverEntrypointError(
            "driver.environment.invalid", "driver numeric environment is invalid"
        ) from error
    attempt_id = _required(values, "AERO_BENCH_ATTEMPT_ID")
    if (
        _required(values, "AERO_BENCH_ROLE") != "agent_driver"
        or _required(values, "AERO_BENCH_RUN_ID") != context.run_id
        or _required(values, "AERO_BENCH_WORKLOAD_ID") != contract.workload_id
        or contract.workload_id != driver.driver_id
        or seed != context.seed
        or port != driver.bridge_port
    ):
        raise DriverEntrypointError(
            "driver.identity.mismatch",
            "executor environment differs from driver contract",
        )
    reader = BundleFileReader(bundle_root)
    _validate_all_declared_inputs(contract, reader)
    try:
        expected_tools = api.compile_tools(context, reader)
        expected_descriptor = api.build_descriptor(context, attempt_id, reader)
    except Exception as error:
        raise DriverEntrypointError(
            "driver.descriptor.invalid",
            "trusted session descriptor could not be compiled",
        ) from error
    if expected_descriptor.tools != expected_tools:
        raise DriverEntrypointError(
            "driver.descriptor.invalid", "compiled tool catalog differs from descriptor"
        )
    config_document = parse_json_object(reader(driver.config.file))
    try:
        config = api.AgentDriverConfig.model_validate(config_document)
    except Exception as error:
        raise DriverEntrypointError(
            "driver.config.invalid", "agent driver config is invalid"
        ) from error
    client = BridgeHttpClient(
        host=_required(values, "AERO_BENCH_SESSION_HOST"),
        port=port,
        token=_required(values, "AERO_BENCH_SESSION_TOKEN"),
    )
    actual_descriptor = client.wait_for_session(
        timeout_s=min(
            _SESSION_STARTUP_TIMEOUT_S,
            expected_descriptor.policy.session_wall_timeout_s,
        )
    )
    if actual_descriptor != expected_descriptor:
        raise DriverEntrypointError(
            "driver.descriptor.mismatch",
            "session bridge descriptor differs from bundle",
        )
    interactions_budget, manifest_budget = _artifact_budgets(contract)
    codex_binary = _normalized_existing_path(
        os.fspath(codex_binary), label="Codex binary", expected_kind="file"
    )
    with tempfile.TemporaryDirectory(prefix="aero-bench-codex-driver-") as temporary:
        scratch = Path(temporary).resolve(strict=True)
        codex_home = scratch / "codex-home"
        work_root = scratch / "work"
        codex_home.mkdir(mode=0o700)
        work_root.mkdir(mode=0o700)
        _copy_codex_auth(auth_file, codex_home)
        driver_context = DriverContext(
            run_id=context.run_id,
            attempt_id=attempt_id,
            private_artifact_root=artifact_root,
            work_root=work_root,
            codex_binary=codex_binary,
            codex_home=codex_home,
            codex_cli_version=config.codex_cli_version,
            codex_binary_sha256=config.codex_binary_sha256,
            max_interactions_bytes=interactions_budget,
            max_manifest_bytes=manifest_budget,
        )
        return driver_runner(driver_context, expected_descriptor, client)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["driver", "run"]:
        payload = {
            "schema_version": "aero-bench.agent-driver-process/v1",
            "status": "failed",
            "failure_code": "driver.arguments.invalid",
        }
        sys.stderr.buffer.write(canonical_json_bytes(payload) + b"\n")
        sys.stderr.buffer.flush()
        return 2
    try:
        manifest = run_from_environment()
    except (DriverEntrypointError, CodexDriverError) as error:
        payload = {
            "schema_version": "aero-bench.agent-driver-process/v1",
            "status": "failed",
            "failure_code": error.code,
        }
        sys.stderr.buffer.write(canonical_json_bytes(payload) + b"\n")
        sys.stderr.buffer.flush()
        return 2
    except Exception:
        payload = {
            "schema_version": "aero-bench.agent-driver-process/v1",
            "status": "failed",
            "failure_code": "driver.internal.failed",
        }
        sys.stderr.buffer.write(canonical_json_bytes(payload) + b"\n")
        sys.stderr.buffer.flush()
        return 2
    payload = {
        "schema_version": "aero-bench.agent-driver-process/v1",
        "run_id": manifest.run_id,
        "attempt_id": manifest.attempt_id,
        "session_id": manifest.session_id,
        "status": manifest.status,
        "failure_code": manifest.failure_code,
        "log_chain_root": manifest.log_chain_root,
    }
    sys.stdout.buffer.write(canonical_json_bytes(payload) + b"\n")
    sys.stdout.buffer.flush()
    return 0 if manifest.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BridgeHttpClient",
    "BundleFileReader",
    "CODEX_BINARY",
    "DriverEntrypointError",
    "main",
    "run_from_environment",
]
