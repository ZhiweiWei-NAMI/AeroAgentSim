"""Read-only world-scene projection service.

Static scene declarations are owned exclusively by the scenario compiler. The
service accepts only ``aero-bench.workload-contract/v5`` and validates its exact
``ResolvedScenario/v2`` plus workload-authorized asset projection before it can
serve. It does not parse a source WorldPackage, invent scene content, own static
entities, or act as a motion authority. Barrier receipts attest only that the
immutable compiler projection remains bound to the run's scenario digest.

The executor issues this workload a run-scoped session token
(`AERO_BENCH_PROVIDER_TOKEN`) at container creation and injects the same value
into the legitimate session alone. The service pins that token at startup,
before any state exists, and requires it on the first `prepare` and every later
frame (constant-time compare, `principal.denied` otherwise). The token-free
`probe` operation is side-effect-free and reports only public workload identity.

The token authenticates within executor-created internal networks. It does not
defend against a compromised host, executor, or workload image.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import re
import signal
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from aero_bench.providers.rpc import (
    JsonLineRpcServer,
    ProviderRpcError,
    parse_json_object,
)
from aero_bench.runtime.contracts import (
    ProviderFinalizationRequest,
    SimulationTime,
)
from aero_bench.serialization import canonical_json_bytes
from workload_scenario import (
    ValidatedWorkloadScenario,
    WorkloadScenarioError,
    validate_workload_scenario,
)


PROTOCOL_VERSION = "aero-bench.world-scene-rpc/v2"
PROVIDER_ADAPTER = "world.scene.rpc"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
STATE_SCHEMA = "world.scene.projection.v2"
EVIDENCE_SCHEMA = "aero-bench.world-scene-evidence/v2"
EVIDENCE_ARTIFACT_TYPE = "world.scene.evidence"
FINALIZATION_BINDING_SCHEMA = "aero-bench.provider-artifact-finalization/v1"
SCENE_VERSION = "0.3.0-world-scene.2"
STATIC_AUTHORITY = "scenario.compiler"
MAX_FRAME_BYTES = JsonLineRpcServer.MAX_FRAME_BYTES
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
NONNEGATIVE_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")


class WorldSceneServiceError(ValueError):
    """A request-domain failure carrying its stable error class.

    Request failures are converted by `WorldSceneService.serve_rpc` into
    canonical error envelopes for the shared RPC server. Startup failures
    are raised before serving.
    """

    def __init__(self, detail: str, *, code: str = "request.invalid"):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _principal_denied(detail: str) -> WorldSceneServiceError:
    return WorldSceneServiceError(detail, code="principal.denied")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: object) -> str:
    return _digest_bytes(canonical_json_bytes(value))


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorldSceneServiceError(f"{label} must be a JSON object")
    actual = set(value)
    missing = required - actual
    extra = actual - required
    if missing or extra:
        raise WorldSceneServiceError(
            f"{label} fields are not exact: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string(
    value: object, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise WorldSceneServiceError(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise WorldSceneServiceError(f"{label} has an invalid value")
    return value


def _integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorldSceneServiceError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise WorldSceneServiceError(f"{label} must be >= {minimum}")
    return value


def _sha256(value: object, *, label: str) -> str:
    result = _string(value, label=label, pattern=SHA256)
    if result == "0" * 64:
        raise WorldSceneServiceError(f"{label} cannot be a placeholder digest")
    return result


def _identifier(value: object, *, label: str) -> str:
    return _string(value, label=label, pattern=IDENTIFIER)


def _file_ref(value: object, *, label: str) -> dict[str, str]:
    raw = _strict_object(value, required=frozenset({"path", "sha256"}), label=label)
    path = _string(raw["path"], label=f"{label}.path")
    normalized = PurePosixPath(path)
    if (
        normalized.is_absolute()
        or normalized == PurePosixPath(".")
        or ".." in normalized.parts
        or str(normalized) != path
        or "\\" in path
    ):
        raise WorldSceneServiceError(f"{label}.path must be normalized and relative")
    return {"path": path, "sha256": _sha256(raw["sha256"], label=f"{label}.sha256")}


def _artifact_requirement(value: object, *, label: str) -> dict[str, str | int | None]:
    raw = _strict_object(
        value,
        required=frozenset(
            {
                "artifact_id",
                "artifact_type",
                "producer_id",
                "visibility",
                "relative_path",
                "max_size_bytes",
                "source_asset_id",
            }
        ),
        label=label,
    )
    artifact_id = _identifier(raw["artifact_id"], label=f"{label}.artifact_id")
    artifact_type = _identifier(raw["artifact_type"], label=f"{label}.artifact_type")
    producer_id = _identifier(raw["producer_id"], label=f"{label}.producer_id")
    visibility = _string(raw["visibility"], label=f"{label}.visibility")
    if visibility not in {"public", "private"}:
        raise WorldSceneServiceError(f"{label}.visibility must be public or private")
    relative_path = _string(raw["relative_path"], label=f"{label}.relative_path")
    normalized = PurePosixPath(relative_path)
    if (
        normalized.is_absolute()
        or normalized == PurePosixPath(".")
        or ".." in normalized.parts
        or relative_path.strip() != relative_path
        or str(normalized) != relative_path
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        raise WorldSceneServiceError(
            f"{label}.relative_path must be normalized and relative"
        )
    max_size_bytes = _integer(
        raw["max_size_bytes"], label=f"{label}.max_size_bytes", minimum=1
    )
    source_asset_id = raw["source_asset_id"]
    if source_asset_id is not None:
        source_asset_id = _identifier(source_asset_id, label=f"{label}.source_asset_id")
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer_id,
        "visibility": visibility,
        "relative_path": relative_path,
        "max_size_bytes": max_size_bytes,
        "source_asset_id": source_asset_id,
    }


@dataclass(frozen=True, slots=True)
class CompilerSceneProjection:
    scenario: ValidatedWorkloadScenario
    static_entity_ids: tuple[str, ...]
    dynamic_entity_ids: tuple[str, ...]
    weather_sample_ids: tuple[str, ...]
    building_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedScene:
    provider_id: str
    run_id: str
    runtime_image: str
    config_digest: str
    artifact_requirement: dict[str, str | int | None]
    projection: CompilerSceneProjection


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    run_id: str
    seed: int
    provider_id: str
    provider_port: int
    runtime_image: str
    config_digest: str
    scenario_digest: str
    step_ns: int
    max_steps: int
    artifact_requirement: dict[str, str | int | None]
    source_revision: str
    projection: CompilerSceneProjection

    @property
    def adapter(self) -> str:
        return PROVIDER_ADAPTER


def _projection_ids(
    values: object,
    *,
    id_field: str,
    label: str,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, list) or (require_nonempty and not values):
        raise WorldSceneServiceError(f"{label} must be an array")
    identifiers: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise WorldSceneServiceError(f"{label}[{index}] must be an object")
        identifiers.append(
            _identifier(item.get(id_field), label=f"{label}[{index}].{id_field}")
        )
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        raise WorldSceneServiceError(f"{label} identities must be sorted and unique")
    return tuple(identifiers)


def _compiler_scene_projection(
    scenario: ValidatedWorkloadScenario,
) -> CompilerSceneProjection:
    raw_entities = scenario.scenario.get("entities")
    if not isinstance(raw_entities, list) or not raw_entities:
        raise WorldSceneServiceError("ResolvedScenario.entities must be non-empty")
    entity_fields = frozenset(
        {
            "entity_id",
            "kind",
            "owner_kind",
            "owner_id",
            "source_provider_id",
            "authority_kind",
            "state",
            "model_asset_id",
            "initial_pose",
            "selected_launch_override",
        }
    )
    entity_ids: list[str] = []
    static_ids: list[str] = []
    dynamic_ids: list[str] = []
    for index, value in enumerate(raw_entities):
        entity = _strict_object(
            value,
            required=entity_fields,
            label=f"ResolvedScenario.entities[{index}]",
        )
        entity_id = _identifier(
            entity["entity_id"],
            label=f"ResolvedScenario.entities[{index}].entity_id",
        )
        entity_ids.append(entity_id)
        state = entity["state"]
        if state == "static":
            if (
                entity["owner_kind"] != "scenario"
                or entity["owner_id"] != STATIC_AUTHORITY
                or entity["source_provider_id"] is not None
                or entity["authority_kind"] != "scenario_static"
            ):
                raise WorldSceneServiceError(
                    f"static entity {entity_id!r} is not compiler-owned"
                )
            static_ids.append(entity_id)
        elif state == "dynamic":
            owner_id = _identifier(
                entity["owner_id"],
                label=f"ResolvedScenario.entities[{index}].owner_id",
            )
            if (
                entity["owner_kind"] != "provider"
                or entity["source_provider_id"] != owner_id
                or entity["authority_kind"] == "scenario_static"
            ):
                raise WorldSceneServiceError(
                    f"dynamic entity {entity_id!r} has invalid provider ownership"
                )
            dynamic_ids.append(entity_id)
        else:
            raise WorldSceneServiceError(
                f"ResolvedScenario entity {entity_id!r} has unsupported state"
            )
    if entity_ids != sorted(entity_ids) or len(entity_ids) != len(set(entity_ids)):
        raise WorldSceneServiceError(
            "ResolvedScenario entity identities must be sorted and unique"
        )

    weather_ids = _projection_ids(
        scenario.scenario.get("weather"),
        id_field="sample_id",
        label="ResolvedScenario.weather",
        require_nonempty=True,
    )
    building_ids = _projection_ids(
        scenario.scenario.get("buildings"),
        id_field="building_id",
        label="ResolvedScenario.buildings",
    )
    return CompilerSceneProjection(
        scenario=scenario,
        static_entity_ids=tuple(static_ids),
        dynamic_entity_ids=tuple(dynamic_ids),
        weather_sample_ids=weather_ids,
        building_ids=building_ids,
    )


class EvidenceWriter:
    def __init__(self, root: Path, requirement: Mapping[str, str | int | None]):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorldSceneServiceError(
                "world-scene artifact root must be a directory"
            )
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise WorldSceneServiceError("world-scene artifact root is not writable")
        self.requirement = _artifact_requirement(
            requirement, label="world-scene evidence ArtifactRequirement"
        )
        self.path = self._resolve_output_path()
        self._reject_undeclared_files()

    def _resolve_output_path(self) -> Path:
        relative_path = self.requirement["relative_path"]
        if not isinstance(relative_path, str):
            raise WorldSceneServiceError(
                "world-scene artifact relative_path must be a string"
            )
        relative = PurePosixPath(relative_path)
        candidate = self.root.joinpath(*relative.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != self.root.parent
        ):
            raise WorldSceneServiceError(
                "world-scene artifact path cannot contain a symbolic link"
            )
        if candidate.exists() and not candidate.is_file():
            raise WorldSceneServiceError(
                "world-scene artifact path must be a regular file"
            )
        return candidate

    def _reject_undeclared_files(self) -> None:
        expected = self.path
        allowed_directories = {
            directory
            for directory in {expected.parent, *expected.parent.parents}
            if directory == self.root or directory.is_relative_to(self.root)
        }
        for candidate in self.root.rglob("*"):
            if candidate.is_symlink():
                raise WorldSceneServiceError(
                    "world-scene artifact root cannot contain symbolic links"
                )
            if candidate.is_dir() and candidate not in allowed_directories:
                raise WorldSceneServiceError(
                    "world-scene artifact root contains an undeclared directory: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )
            if candidate.is_file() and candidate != expected:
                raise WorldSceneServiceError(
                    "world-scene artifact root contains an undeclared file: "
                    f"{candidate.relative_to(self.root).as_posix()}"
                )

    def _create_declared_parents(self) -> None:
        relative_parent = self.path.parent.relative_to(self.root)
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise WorldSceneServiceError(
                        "world-scene artifact parent path is not a regular directory"
                    )
            else:
                current.mkdir()

    def reset(self) -> None:
        self._reject_undeclared_files()
        self._create_declared_parents()
        if self.path.exists() and self.path.is_symlink():
            raise WorldSceneServiceError(
                "world-scene evidence path cannot be a symbolic link"
            )
        with self.path.open("wb"):
            pass

    def append(self, record: Mapping[str, object]) -> str:
        encoded = canonical_json_bytes(dict(record)) + b"\n"
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise WorldSceneServiceError(
                "world-scene artifact max_size_bytes must be an integer"
            )
        self._reject_undeclared_files()
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size + len(encoded) > max_size_bytes:
            raise WorldSceneServiceError(
                "world-scene evidence artifact exceeds declared max_size_bytes"
            )
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        final_size = self.path.stat().st_size
        if final_size > max_size_bytes:
            raise WorldSceneServiceError(
                "world-scene evidence artifact exceeded declared max_size_bytes "
                "after append"
            )
        return _digest_bytes(self.path.read_bytes())

    def flush(self) -> None:
        if not self.path.exists() or self.path.is_symlink():
            return
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise WorldSceneServiceError(
                "world-scene artifact max_size_bytes must be an integer"
            )
        if self.path.stat().st_size > max_size_bytes:
            raise WorldSceneServiceError(
                "world-scene evidence artifact exceeds declared max_size_bytes"
            )
        with self.path.open("rb") as stream:
            os.fsync(stream.fileno())

    def finalize(self, finalization_binding: Mapping[str, object]) -> tuple[str, int]:
        self._reject_undeclared_files()
        if not self.path.exists() or self.path.is_symlink() or not self.path.is_file():
            raise WorldSceneServiceError("world-scene evidence artifact is unavailable")
        if not self.path.stat().st_size:
            raise WorldSceneServiceError(
                "world-scene evidence artifact must not be empty"
            )
        self.append(finalization_binding)
        self.flush()
        content = self.path.read_bytes()
        return _digest_bytes(content), len(content)


def _resolve_root_file(root: Path, reference: Mapping[str, str], *, label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    candidate = root.joinpath(*relative.parts)
    for parent in (root, *candidate.parents):
        if parent == candidate:
            break
        if parent.is_symlink():
            raise WorldSceneServiceError(
                f"{label} path contains a symbolic link: {reference['path']}"
            )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise WorldSceneServiceError(
            f"{label} is unavailable: {reference['path']}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise WorldSceneServiceError(
            f"{label} is not a regular file: {reference['path']}"
        )
    digest = _digest_bytes(resolved.read_bytes())
    if digest != reference["sha256"]:
        raise WorldSceneServiceError(
            f"{label} SHA-256 mismatch for {reference['path']}: "
            f"expected {reference['sha256']}, got {digest}"
        )
    return resolved


def _load_workload_identity(
    *,
    contract_path: Path,
    bundle_root: Path,
    expected_run_id: str,
    expected_seed: int,
    expected_provider_id: str,
    expected_provider_port: int,
) -> WorkloadIdentity:
    try:
        contract_mode = contract_path.lstat().st_mode
        bundle_mode = bundle_root.lstat().st_mode
        contract_bytes = contract_path.read_bytes()
        raw = parse_json_object(contract_bytes)
    except OSError as exc:
        raise WorldSceneServiceError("AERO_BENCH_CONTRACT is unavailable") from exc
    if (
        not contract_path.is_absolute()
        or not bundle_root.is_absolute()
        or stat.S_ISLNK(contract_mode)
        or not stat.S_ISREG(contract_mode)
        or stat.S_ISLNK(bundle_mode)
        or not stat.S_ISDIR(bundle_mode)
    ):
        raise WorldSceneServiceError(
            "AERO_BENCH_CONTRACT and AERO_BENCH_BUNDLE_DIR must be regular paths"
        )
    bundle_root = bundle_root.resolve(strict=True)
    if canonical_json_bytes(raw) + b"\n" != contract_bytes:
        raise WorldSceneServiceError("AERO_BENCH_CONTRACT must be canonical JSON")
    contract = _strict_object(
        raw,
        required=frozenset(
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
            }
        ),
        label="ProviderWorkloadContract",
    )
    if contract["schema_version"] != "aero-bench.workload-contract/v5":
        raise WorldSceneServiceError("ProviderWorkloadContract schema is unsupported")
    if contract["role"] != "provider":
        raise WorldSceneServiceError(
            "AERO_BENCH_CONTRACT is not a provider workload contract"
        )
    contract_run_id = _sha256(contract["run_id"], label="contract.run_id")
    if contract_run_id != expected_run_id:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract run_id differs from AERO_BENCH_RUN_ID"
        )
    contract_seed = _integer(contract["seed"], label="contract.seed", minimum=0)
    if contract_seed != expected_seed:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract seed differs from AERO_BENCH_SEED"
        )
    contract_workload_id = _string(
        contract["workload_id"], label="contract.workload_id", pattern=IDENTIFIER
    )
    if contract_workload_id != expected_provider_id:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract workload_id differs from AERO_BENCH_WORKLOAD_ID"
        )
    clock = _strict_object(
        contract["clock"],
        required=frozenset(
            {"authority", "step_ns", "max_steps", "provider_timeout_ms"}
        ),
        label="ProviderWorkloadContract.clock",
    )
    if clock["authority"] != "provider_barrier":
        raise WorldSceneServiceError("ProviderWorkloadContract clock authority is invalid")
    step_ns = _integer(clock["step_ns"], label="contract.clock.step_ns", minimum=1)
    max_steps = _integer(
        clock["max_steps"], label="contract.clock.max_steps", minimum=1
    )
    _integer(
        clock["provider_timeout_ms"],
        label="contract.clock.provider_timeout_ms",
        minimum=1,
    )

    provider = _strict_object(
        contract["provider"],
        required=frozenset(
            {
                "provider_id",
                "adapter",
                "port",
                "workload",
                "config",
                "protocol_schema",
                "capabilities",
                "artifact_requirements",
            }
        ),
        label="ProviderWorkloadContract.provider",
    )
    provider_id = _identifier(
        provider["provider_id"], label="contract.provider.provider_id"
    )
    if provider_id != expected_provider_id:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract provider_id is not the workload identity"
        )
    if provider["adapter"] != PROVIDER_ADAPTER:
        raise WorldSceneServiceError(
            "world-scene service requires the world.scene.rpc provider adapter"
        )
    provider_port = _integer(
        provider["port"], label="contract.provider.port", minimum=1024
    )
    if provider_port > 65535 or provider_port != expected_provider_port:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract provider port is not the explicit bind port"
        )
    raw_capabilities = provider["capabilities"]
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract capabilities must be a non-empty array"
        )
    capabilities = tuple(
        _identifier(
            capability,
            label=f"contract.provider.capabilities[{index}]",
        )
        for index, capability in enumerate(raw_capabilities)
    )
    if (
        len(capabilities) != len(set(capabilities))
        or capabilities != tuple(sorted(capabilities))
    ):
        raise WorldSceneServiceError(
            "ProviderWorkloadContract capabilities must be sorted and unique"
        )
    try:
        scenario = validate_workload_scenario(
            contract["scenario"],
            expected_seed=contract_seed,
            expected_digest=contract["scenario_digest"],
            role="provider",
            workload_id=provider_id,
            projected_assets=contract["scenario_assets"],
            provider_capabilities=capabilities,
        )
    except WorkloadScenarioError as exc:
        raise WorldSceneServiceError(
            "ProviderWorkloadContract scenario is invalid"
        ) from exc
    scenario_provider = next(
        item
        for item in scenario.scenario["providers"]
        if item["provider_id"] == provider_id
    )
    roles = tuple(scenario_provider["roles"])
    if not roles or any(
        role not in {"motion", "traffic", "sensor", "wireless_network"}
        for role in roles
    ):
        raise WorldSceneServiceError(
            "world-scene workload cannot claim a static-scene runtime authority"
        )
    projection = _compiler_scene_projection(scenario)

    workload = _strict_object(
        provider["workload"],
        required=frozenset({"runtime", "resources", "implementation"}),
        label="ProviderWorkloadContract.provider.workload",
    )
    runtime = _strict_object(
        workload["runtime"],
        required=frozenset({"image", "command"}),
        label="ProviderWorkloadContract.provider.workload.runtime",
    )
    runtime_image = _string(
        runtime["image"], label="runtime.image", pattern=PINNED_IMAGE
    )
    if runtime_image.rsplit(":", maxsplit=1)[1] == "0" * 64:
        raise WorldSceneServiceError("runtime image cannot use a placeholder digest")
    if (
        not isinstance(runtime["command"], list)
        or not runtime["command"]
        or any(not isinstance(item, str) or not item for item in runtime["command"])
    ):
        raise WorldSceneServiceError("provider workload runtime command is invalid")
    implementation = _strict_object(
        workload["implementation"],
        required=frozenset(
            {"component_id", "kind", "source_uri", "source_revision", "version"}
        ),
        label="ProviderWorkloadContract.provider.workload.implementation",
    )
    if (
        _identifier(
            implementation["component_id"],
            label="implementation.component_id",
        )
        != PROVIDER_ADAPTER
    ):
        raise WorldSceneServiceError(
            "implementation component_id must equal the world.scene.rpc adapter"
        )
    if implementation["kind"] != "production":
        raise WorldSceneServiceError(
            "world-scene workload implementation must be production"
        )
    if implementation["version"] != SCENE_VERSION:
        raise WorldSceneServiceError(
            "world-scene workload version differs from this service build"
        )
    source_revision = _string(
        implementation["source_revision"],
        label="implementation.source_revision",
        pattern=REVISION,
    )
    if set(source_revision) == {"0"}:
        raise WorldSceneServiceError(
            "implementation source_revision cannot be a placeholder"
        )

    config_bound = _strict_object(
        provider["config"],
        required=frozenset({"file", "schema_file"}),
        label="ProviderWorkloadContract.provider.config",
    )
    config_file = _file_ref(config_bound["file"], label="contract.provider.config.file")
    config_path = _resolve_root_file(
        bundle_root, config_file, label="provider config file"
    )
    _resolve_root_file(
        bundle_root,
        _file_ref(
            config_bound["schema_file"], label="contract.provider.config.schema_file"
        ),
        label="provider config schema file",
    )
    _resolve_root_file(
        bundle_root,
        _file_ref(
            provider["protocol_schema"], label="contract.provider.protocol_schema"
        ),
        label="provider protocol schema",
    )
    try:
        raw_config = parse_json_object(config_path.read_bytes())
    except (OSError, ProviderRpcError) as exc:
        raise WorldSceneServiceError(
            "world-scene provider config is not strict JSON"
        ) from exc
    provider_config = _strict_object(
        raw_config,
        required=frozenset({"schema_version", "provider_id", "scene"}),
        label="world-scene provider config",
    )
    if provider_config["schema_version"] != "aero-bench.world-scene/v2":
        raise WorldSceneServiceError("world-scene provider config schema is unsupported")
    if _identifier(
        provider_config["provider_id"], label="world-scene config provider_id"
    ) != provider_id:
        raise WorldSceneServiceError(
            "world-scene provider config identity differs from workload"
        )
    scene_identity = _strict_object(
        provider_config["scene"],
        required=frozenset({"version", "commit"}),
        label="world-scene provider config scene",
    )
    if scene_identity["version"] != SCENE_VERSION:
        raise WorldSceneServiceError(
            "world-scene config version differs from this service build"
        )
    if (
        _string(scene_identity["commit"], label="scene.commit", pattern=COMMIT)
        != source_revision
    ):
        raise WorldSceneServiceError(
            "world-scene config commit differs from workload implementation"
        )

    requirements = provider["artifact_requirements"]
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise WorldSceneServiceError(
            "world-scene provider must declare exactly one projection evidence "
            "ArtifactRequirement"
        )
    requirement = _artifact_requirement(
        requirements[0], label="world-scene evidence ArtifactRequirement"
    )
    if requirement["artifact_type"] != EVIDENCE_ARTIFACT_TYPE:
        raise WorldSceneServiceError(
            f"world-scene evidence ArtifactRequirement must use "
            f"{EVIDENCE_ARTIFACT_TYPE!r}"
        )
    if requirement["producer_id"] != provider_id:
        raise WorldSceneServiceError(
            "world-scene evidence ArtifactRequirement producer_id mismatch"
        )
    if (
        requirement["visibility"] != "private"
        or requirement["source_asset_id"] is not None
    ):
        raise WorldSceneServiceError(
            "world-scene evidence ArtifactRequirement must be a private runtime artifact"
        )

    return WorkloadIdentity(
        run_id=expected_run_id,
        seed=contract_seed,
        provider_id=provider_id,
        provider_port=provider_port,
        runtime_image=runtime_image,
        config_digest=config_file["sha256"],
        scenario_digest=scenario.scenario_digest,
        step_ns=step_ns,
        max_steps=max_steps,
        artifact_requirement=requirement,
        source_revision=source_revision,
        projection=projection,
    )


class WorldSceneService:
    """Strict JSON-line mirror of one compiler-owned scenario projection."""

    def __init__(
        self,
        *,
        bundle_root: Path,
        artifact_root: Path,
        rpc_port: int,
        workload_identity: WorkloadIdentity,
        session_token: str,
    ):
        try:
            self._bundle_root = bundle_root.resolve(strict=True)
        except OSError as exc:
            raise WorldSceneServiceError(
                "world-scene bundle root is unavailable"
            ) from exc
        if not self._bundle_root.is_dir():
            raise WorldSceneServiceError("world-scene bundle root must be a directory")
        if not 1024 <= rpc_port <= 65535:
            raise ValueError("world-scene RPC port is outside the user-service range")
        if workload_identity.provider_port != rpc_port:
            raise ValueError("workload provider port must equal the RPC port")
        if SHA256.fullmatch(session_token) is None or session_token == "0" * 64:
            raise ValueError(
                "world-scene expected session token must be an executor-issued "
                "non-placeholder SHA-256 digest"
            )
        self._rpc_port = rpc_port
        self._workload_identity = workload_identity
        self._session_token = session_token
        self._evidence = EvidenceWriter(
            artifact_root, workload_identity.artifact_requirement
        )
        self._scene: PreparedScene | None = None
        self._current: tuple[int, int] | None = None
        self._finalization_receipt: dict[str, object] | None = None
        self._shutdown_requested = asyncio.Event()
        self._lock = asyncio.Lock()
        self._closed = False
        self._shutdown_started = False

    @property
    def shutdown_requested(self) -> asyncio.Event:
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def close_runtime(self) -> None:
        self._evidence.flush()
        self._closed = True

    def _scene_state(self, *, target: tuple[int, int]) -> dict[str, Any]:
        scene = self._scene
        if scene is None:
            raise WorldSceneServiceError(
                "world-scene provider has not completed prepare"
            )
        projection = scene.projection
        scenario = projection.scenario.scenario
        return {
            "schema_version": STATE_SCHEMA,
            "projection_kind": "resolved_scenario",
            "static_authority": STATIC_AUTHORITY,
            "scenario_digest": projection.scenario.scenario_digest,
            "world_id": scenario["world_id"],
            "world_digest": scenario["world_digest"],
            "source_asset_digest": scenario["source_asset_digest"],
            "scenario_asset_digest": scenario["scenario_asset_digest"],
            "selected_launch_site_id": scenario["selected_launch_site_id"],
            "weather": {
                "mode": "compiler_projection",
                "sample_ids": list(projection.weather_sample_ids),
                "declared_count": len(projection.weather_sample_ids),
            },
            "scene": {
                "entity_count": len(projection.static_entity_ids)
                + len(projection.dynamic_entity_ids),
                "static_entity_count": len(projection.static_entity_ids),
                "dynamic_entity_count": len(projection.dynamic_entity_ids),
                "building_count": len(projection.building_ids),
            },
            "reached": {"tick": target[0], "sim_time_ns": target[1]},
        }

    def _record(
        self,
        *,
        operation: str,
        target: tuple[int, int],
        state: dict[str, Any],
    ) -> tuple[str, str]:
        scene = self._scene
        if scene is None:
            raise WorldSceneServiceError(
                "world-scene provider has not completed prepare"
            )
        state_digest = _digest_json(state)
        evidence_digest = self._evidence.append(
            {
                "schema_version": EVIDENCE_SCHEMA,
                "provider_id": scene.provider_id,
                "run_id": scene.run_id,
                "operation": operation,
                "tick": target[0],
                "sim_time_ns": target[1],
                "scene_state": state,
                "scene_state_sha256": state_digest,
                "projection_kind": "resolved_scenario",
                "static_authority": STATIC_AUTHORITY,
                "scenario_digest": scene.projection.scenario.scenario_digest,
                "world_id": state["world_id"],
                "world_digest": state["world_digest"],
                "source_asset_digest": state["source_asset_digest"],
                "scenario_asset_digest": state["scenario_asset_digest"],
                "runtime_image": scene.runtime_image,
                "config_digest": scene.config_digest,
                "artifact_id": scene.artifact_requirement["artifact_id"],
                "artifact_type": scene.artifact_requirement["artifact_type"],
            }
        )
        return state_digest, evidence_digest

    def _receipt(self, *, target: tuple[int, int], operation: str) -> dict[str, object]:
        scene = self._scene
        if scene is None:
            raise WorldSceneServiceError(
                "world-scene provider has not completed prepare"
            )
        state = self._scene_state(target=target)
        state_digest, evidence_digest = self._record(
            operation=operation, target=target, state=state
        )
        time_value = {"tick": target[0], "sim_time_ns": target[1]}
        return {
            "run_id": scene.run_id,
            "provider_id": scene.provider_id,
            "reached": time_value,
            "state_digest": state_digest,
            "events": [
                {
                    "provider_id": scene.provider_id,
                    "event_id": f"state.{target[0]}",
                    "time": time_value,
                    "payload_schema_id": STATE_SCHEMA,
                    "payload": [
                        {
                            "name": "scenario_digest",
                            "value": state["scenario_digest"],
                        },
                        {
                            "name": "static_authority",
                            "value": state["static_authority"],
                        },
                        {"name": "world_id", "value": state["world_id"]},
                        {"name": "world_digest", "value": state["world_digest"]},
                        {
                            "name": "source_asset_digest",
                            "value": state["source_asset_digest"],
                        },
                        {
                            "name": "scenario_asset_digest",
                            "value": state["scenario_asset_digest"],
                        },
                        {
                            "name": "weather_sample_ids_json",
                            "value": canonical_json_bytes(
                                state["weather"]["sample_ids"]
                            ).decode("utf-8"),
                        },
                        {
                            "name": "weather_mode",
                            "value": state["weather"]["mode"],
                        },
                        {"name": "scene_state_sha256", "value": state_digest},
                        {
                            "name": "entity_count",
                            "value": state["scene"]["entity_count"],
                        },
                        {
                            "name": "building_count",
                            "value": state["scene"]["building_count"],
                        },
                        {
                            "name": "evidence_path",
                            "value": scene.artifact_requirement["relative_path"],
                        },
                        {"name": "evidence_sha256", "value": evidence_digest},
                        {"name": "simulation_time_ns", "value": target[1]},
                    ],
                }
            ],
        }

    def _require_not_finalized(self) -> None:
        if self._finalization_receipt is not None:
            raise WorldSceneServiceError("world-scene provider is already finalized")

    def _require_identity(self, payload: Mapping[str, Any]) -> PreparedScene:
        presented = payload.get("session_token")
        if not isinstance(presented, str) or not hmac.compare_digest(
            presented, self._session_token
        ):
            # Constant-time compare before any state or identity check: a
            # peer that cannot present the executor-issued token forges no
            # principal and learns no identity detail.
            raise _principal_denied(
                "world-scene request cannot present the executor-issued "
                "run-scoped session token"
            )
        scene = self._scene
        if scene is None:
            raise WorldSceneServiceError(
                "world-scene provider has not completed prepare"
            )
        provider_id = _identifier(payload.get("provider_id"), label="provider_id")
        if provider_id != scene.provider_id:
            raise WorldSceneServiceError("world-scene provider identity mismatch")
        run_id = _sha256(payload.get("run_id"), label="run_id")
        if run_id != scene.run_id:
            raise WorldSceneServiceError("world-scene run identity mismatch")
        return scene

    def _parse_prepare(self, payload: Mapping[str, Any]) -> PreparedScene:
        presented = payload.get("session_token")
        if not isinstance(presented, str) or not hmac.compare_digest(
            presented, self._session_token
        ):
            # Constant-time compare before any shape, identity, or state
            # check: a racing peer or wrong token forges no principal, binds
            # no state, and learns no identity detail.
            raise _principal_denied(
                "world-scene prepare cannot present the executor-issued "
                "run-scoped session token"
            )
        raw = _strict_object(
            payload,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "runtime_image",
                    "config_digest",
                    "artifact_requirements",
                    "scenario_digest",
                    "session_token",
                }
            ),
            label="world-scene prepare request",
        )
        identity = self._workload_identity
        provider_id = _identifier(raw["provider_id"], label="provider_id")
        if provider_id != identity.provider_id:
            raise WorldSceneServiceError(
                "world-scene provider_id differs from the workload contract"
            )
        run_id = _sha256(raw["run_id"], label="run_id")
        if run_id != identity.run_id:
            raise WorldSceneServiceError(
                "world-scene run_id differs from the workload contract"
            )
        runtime_image = _string(
            raw["runtime_image"], label="runtime_image", pattern=PINNED_IMAGE
        )
        if runtime_image != identity.runtime_image:
            raise WorldSceneServiceError(
                "world-scene runtime image differs from the workload contract"
            )
        config_digest = _sha256(raw["config_digest"], label="config_digest")
        if config_digest != identity.config_digest:
            raise WorldSceneServiceError(
                "world-scene config digest differs from the workload contract"
            )
        scenario_digest = _sha256(
            raw["scenario_digest"], label="scenario_digest"
        )
        if scenario_digest != identity.scenario_digest:
            raise WorldSceneServiceError(
                "world-scene scenario digest differs from the workload contract"
            )
        requirements_raw = raw["artifact_requirements"]
        if not isinstance(requirements_raw, list) or len(requirements_raw) != 1:
            raise WorldSceneServiceError(
                "world-scene artifact requirements are not contract-bound"
            )
        requirement = _artifact_requirement(
            requirements_raw[0], label="world-scene declared ArtifactRequirement"
        )
        if requirement != identity.artifact_requirement:
            raise WorldSceneServiceError(
                "world-scene artifact requirement differs from the workload contract"
            )
        return PreparedScene(
            provider_id=provider_id,
            run_id=run_id,
            runtime_image=runtime_image,
            config_digest=config_digest,
            artifact_requirement=requirement,
            projection=identity.projection,
        )

    async def serve_rpc(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """The JsonLineRpcServer-facing handler.

        Typed service failures become canonical error envelopes carrying
        their stable failure class, so a denied peer receives
        `principal.denied` and never a bare connection drop.
        """

        try:
            return await self.handle(operation, payload)
        except WorldSceneServiceError as exc:
            return {"error": {"code": exc.code, "detail": exc.detail}}

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        async with self._lock:
            if self._closed:
                raise WorldSceneServiceError("world-scene provider service is closed")
            if self._shutdown_started and operation != "shutdown":
                raise WorldSceneServiceError(
                    "world-scene provider shutdown is already in progress"
                )
            if operation == "probe":
                if payload != {}:
                    raise WorldSceneServiceError(
                        "world-scene provider probe request must be empty"
                    )
                return {
                    "schema_version": PROVIDER_PROBE_SCHEMA,
                    "status": "accepting",
                    "run_id": self._workload_identity.run_id,
                    "provider_id": self._workload_identity.provider_id,
                    "adapter": self._workload_identity.adapter,
                    "runtime_image": self._workload_identity.runtime_image,
                    "config_digest": self._workload_identity.config_digest,
                }
            if operation == "prepare":
                # Parse before the double-prepare guard: the token compare
                # inside the parse is constant-time and stateless, so a wrong
                # token is principal.denied whenever it arrives, while a
                # correctly authenticated duplicate prepare still reports the
                # double-prepare error. The parse itself binds nothing.
                scene = await asyncio.to_thread(self._parse_prepare, payload)
                if self._scene is not None:
                    raise WorldSceneServiceError(
                        "world-scene provider prepare called twice"
                    )
                self._scene = scene
                scenario = self._scene.projection.scenario.scenario
                return {
                    "status": "ready",
                    "provider_id": self._scene.provider_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "runtime_image": self._scene.runtime_image,
                    "scenario_schema_version": scenario["schema_version"],
                    "scenario_digest": self._scene.projection.scenario.scenario_digest,
                    "world_schema_version": scenario["world_schema_version"],
                    "world_id": scenario["world_id"],
                    "world_digest": scenario["world_digest"],
                    "source_asset_digest": scenario["source_asset_digest"],
                    "scenario_asset_digest": scenario["scenario_asset_digest"],
                    "static_authority": STATIC_AUTHORITY,
                    "scene_version": SCENE_VERSION,
                    "scene_commit": self._workload_identity.source_revision,
                }
            if operation == "reset":
                self._require_identity(payload)
                self._require_not_finalized()
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "session_token", "seed"}
                    ),
                    label="world-scene reset request",
                )
                seed = _integer(raw["seed"], label="seed", minimum=0)
                if seed != self._workload_identity.seed:
                    raise WorldSceneServiceError(
                        "world-scene reset seed differs from ResolvedScenario"
                    )
                self._evidence.reset()
                self._current = (0, 0)
                return {
                    "receipt": await asyncio.to_thread(
                        self._receipt, target=(0, 0), operation="reset"
                    )
                }
            if operation == "step_to":
                scene = self._require_identity(payload)
                self._require_not_finalized()
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "session_token", "request"}
                    ),
                    label="world-scene step_to request",
                )
                request = _strict_object(
                    raw["request"],
                    required=frozenset({"run_id", "target"}),
                    label="world-scene StepRequest",
                )
                if _sha256(request["run_id"], label="request.run_id") != scene.run_id:
                    raise WorldSceneServiceError(
                        "world-scene StepRequest run identity mismatch"
                    )
                try:
                    target_time = SimulationTime.model_validate(request["target"])
                except ValueError as exc:
                    raise WorldSceneServiceError(
                        f"world-scene step target is not a SimulationTime: {exc}"
                    ) from exc
                target = (target_time.tick, target_time.sim_time_ns)
                if self._current is None:
                    raise WorldSceneServiceError(
                        "world-scene reset must precede step_to"
                    )
                expected = (
                    self._current[0] + 1,
                    self._current[1] + self._workload_identity.step_ns,
                )
                if (
                    target != expected
                    or target[0] > self._workload_identity.max_steps
                ):
                    raise WorldSceneServiceError(
                        "world-scene step target must equal the next contract clock barrier"
                    )
                self._current = target
                return {
                    "receipt": await asyncio.to_thread(
                        self._receipt, target=target, operation="step_to"
                    )
                }
            if operation == "snapshot":
                self._require_identity(payload)
                self._require_not_finalized()
                _strict_object(
                    payload,
                    required=frozenset({"provider_id", "run_id", "session_token"}),
                    label="world-scene snapshot request",
                )
                if self._current is None:
                    raise WorldSceneServiceError(
                        "world-scene reset must precede snapshot"
                    )
                state = await asyncio.to_thread(self._scene_state, target=self._current)
                digest, _ = await asyncio.to_thread(
                    self._record,
                    operation="snapshot",
                    target=self._current,
                    state=state,
                )
                return {"snapshot_digest": digest}
            if operation == "finalize":
                scene = self._require_identity(payload)
                raw = _strict_object(
                    payload,
                    required=frozenset(
                        {"provider_id", "run_id", "session_token", "request"}
                    ),
                    label="world-scene finalize request",
                )
                try:
                    request = ProviderFinalizationRequest.model_validate(raw["request"])
                except (TypeError, ValueError) as exc:
                    raise WorldSceneServiceError(
                        "world-scene ProviderFinalizationRequest is invalid"
                    ) from exc
                if request.run_id != scene.run_id:
                    raise WorldSceneServiceError(
                        "world-scene finalization belongs to another run"
                    )
                target = (
                    request.terminal_time.tick,
                    request.terminal_time.sim_time_ns,
                )
                if self._current is None or target != self._current:
                    raise WorldSceneServiceError(
                        "world-scene finalization time differs from provider time"
                    )
                if self._finalization_receipt is not None:
                    if (
                        self._finalization_receipt["event_chain_root"]
                        != request.event_chain_root
                    ):
                        raise WorldSceneServiceError(
                            "world-scene finalization root cannot be changed"
                        )
                    return {"receipt": dict(self._finalization_receipt)}
                finalization_binding = {
                    "schema_version": FINALIZATION_BINDING_SCHEMA,
                    "run_id": scene.run_id,
                    "provider_id": scene.provider_id,
                    "terminal_event": request.terminal_event,
                    "terminal_time": request.terminal_time.model_dump(mode="json"),
                    "event_chain_root": request.event_chain_root,
                }
                artifact_sha256, size_bytes = await asyncio.to_thread(
                    self._evidence.finalize, finalization_binding
                )
                receipt: dict[str, object] = {
                    "schema_version": "aero-bench.provider-finalization-receipt/v1",
                    "run_id": scene.run_id,
                    "provider_id": scene.provider_id,
                    "event_chain_root": request.event_chain_root,
                    "artifacts": [
                        {
                            "artifact_id": scene.artifact_requirement["artifact_id"],
                            "sha256": artifact_sha256,
                            "size_bytes": size_bytes,
                        }
                    ],
                }
                self._finalization_receipt = receipt
                return {"receipt": dict(receipt)}
            if operation == "shutdown":
                self._require_identity(payload)
                _strict_object(
                    payload,
                    required=frozenset({"provider_id", "run_id", "session_token"}),
                    label="world-scene shutdown request",
                )
                self._shutdown_started = True
                return {"status": "stopped"}
            raise WorldSceneServiceError(
                f"world-scene operation is not supported: {operation}"
            )


def _shutdown_observer(service: WorldSceneService):
    """Stop serving once a shutdown response has drained to its client."""

    def observe(operation: str, response: Mapping[str, Any], drained: bool) -> None:
        if (
            drained
            and operation == "shutdown"
            and isinstance(response, dict)
            and response.get("status") == "stopped"
        ):
            service.request_shutdown()

    return observe


async def serve(service: WorldSceneService, *, host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, service.request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
    rpc_server = JsonLineRpcServer(
        service.serve_rpc, response_drained=_shutdown_observer(service)
    )
    try:
        await rpc_server.start(host=host, port=port)
        await service.shutdown_requested.wait()
        try:
            await rpc_server.graceful_close(timeout_seconds=5.0)
        except ProviderRpcError:
            await rpc_server.force_close()
    finally:
        await asyncio.to_thread(service.close_runtime)
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(signum)
            except (NotImplementedError, RuntimeError):
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AERO-BENCH world-scene provider")
    parser.add_argument("mode", nargs="+", help="provider serve")
    return parser


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise SystemExit(f"{name} is required")
    return value


def _environment_port(name: str) -> int:
    value = _required_environment(name)
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer port") from exc
    if not 1024 <= port <= 65535:
        raise SystemExit(f"{name} must be between 1024 and 65535")
    return port


def _environment_session_token() -> str:
    """The executor-issued, run-scoped credential pinned before serving."""

    value = _required_environment("AERO_BENCH_PROVIDER_TOKEN")
    if SHA256.fullmatch(value) is None or value == "0" * 64:
        raise SystemExit(
            "AERO_BENCH_PROVIDER_TOKEN must be a non-placeholder SHA-256 digest"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mode != ["provider", "serve"]:
        raise SystemExit("service mode must be 'provider serve'")
    bind_host = _string(
        _required_environment("AERO_BENCH_PROVIDER_BIND_HOST"),
        label="AERO_BENCH_PROVIDER_BIND_HOST",
        pattern=HOST,
    )
    bind_port = _environment_port("AERO_BENCH_PROVIDER_PORT")
    bundle_root = Path(_required_environment("AERO_BENCH_BUNDLE_DIR"))
    artifact_root = Path(_required_environment("AERO_BENCH_ARTIFACT_DIR"))
    contract_path = Path(_required_environment("AERO_BENCH_CONTRACT"))
    expected_run_id = _sha256(
        _required_environment("AERO_BENCH_RUN_ID"),
        label="AERO_BENCH_RUN_ID",
    )
    expected_provider_id = _string(
        _required_environment("AERO_BENCH_WORKLOAD_ID"),
        label="AERO_BENCH_WORKLOAD_ID",
        pattern=IDENTIFIER,
    )
    seed_text = _required_environment("AERO_BENCH_SEED")
    if NONNEGATIVE_DECIMAL.fullmatch(seed_text) is None:
        raise SystemExit("AERO_BENCH_SEED must be a canonical nonnegative integer")
    workload_identity = _load_workload_identity(
        contract_path=contract_path,
        bundle_root=bundle_root,
        expected_run_id=expected_run_id,
        expected_seed=int(seed_text, 10),
        expected_provider_id=expected_provider_id,
        expected_provider_port=bind_port,
    )
    service = WorldSceneService(
        bundle_root=bundle_root,
        artifact_root=artifact_root,
        rpc_port=bind_port,
        workload_identity=workload_identity,
        session_token=_environment_session_token(),
    )
    asyncio.run(serve(service, host=bind_host, port=bind_port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
