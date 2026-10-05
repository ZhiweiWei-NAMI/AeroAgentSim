"""Formal Inspection Business provider service.

The service is the only authority for the canonical WorkOrder state machine
and the business history hash chain. It runs behind a single JSON-line RPC
surface (``aero_bench.providers.rpc.JsonLineRpcServer``), binds itself to the
digest-pinned ProviderWorkloadContract and provider config bytes, and invents
no physical state, observation, detection, score, or time.

Trust chain for command principals. An Agent reaches the Gateway only with its
run-scoped token (``AgentCredentialStore``), the Gateway attests
``CommandRequest.agent_id`` from that token, and the Harness session is the
only client that performs ``prepare``. The executor issues this workload a
run-scoped session token (``AERO_BENCH_PROVIDER_TOKEN``) at container
creation and injects the same value into the Harness alone, so the first
``prepare`` and every later frame must present it (constant-time compare,
``principal.denied`` otherwise). A peer workload on the provider network
cannot race the legitimate session to the first prepare, bind its own token,
or forge a principal by replaying public identity fields; without a matching
token the service never leaves the unprepared state. The ordering is
absolute: every stateful frame is authenticated before any closed, prepared,
or machine state is consulted, so even a closed runtime answers
``principal.denied`` without the token and only the terminal ``not.ready``
with it.

The ``probe`` operation stays token-free and side-effect-free: it reports only
the public workload-contract identity for the shared readiness probe
(``containers/readiness_probe.py``, the Docker HEALTHCHECK) and mutates no
state.

Boundary. The token authenticates within the executor-created internal
Docker bridge networks, where the plaintext token is reachable only by the
workload, the Harness, and the executor. It is not a defense against a
host-side adversary that can observe or inject packets on that bridge
(host-level packet access such as ``CAP_NET_RAW`` on the host), against a
compromised executor, or against a compromised workload image.

Stable failure classes returned as ``{"error": {"code", "detail"}}``:

- ``request.invalid``: a structurally invalid frame or payload.
- ``identity.mismatch``: a request that disagrees with the pinned workload
  contract, provider config bytes, or service identity.
- ``not.ready``: an operation that requires prepare/reset which has not
  happened.
- ``principal.denied``: a request that cannot present the Harness-granted
  session token, a command whose actor does not match the attested principal,
  or a principal that lacks the authority role required by the tool kind.
- ``query.rejected``: a well-formed query the authoritative state rejects.
- ``artifact.write-failed``: the declared evidence artifact could not be
  written exactly as declared.

Business-level command rejections are not errors: they are ordered command
receipts ending in phase ``failed``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import re
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeVar

from pydantic import TypeAdapter

from aero_bench.config.models import StrictModel
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.providers.inspection_business.config import InspectionBusinessConfig
from aero_bench.providers.inspection_business.protocol import PROTOCOL_VERSION
from aero_bench.providers.rpc import (
    JsonLineRpcServer,
    ProviderRpcError,
    parse_json_object,
)
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    BusinessEnvironmentStageResult,
    CommandRequest,
    ProviderFinalizationRequest,
    SceneContribution,
    SceneState,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    scene_state_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.business import (
    InspectionBusinessService as BusinessStateMachine,
)
from aero_bench.tasks.inspection.business import query_business_state
from aero_bench.tasks.inspection.contracts import (
    BusinessCommandEnvelope,
    BusinessQuery,
    InspectionCommand,
)


StrictModelT = TypeVar("StrictModelT", bound=StrictModel)


STATE_SCHEMA = "inspection.business.state.v1"
ARTIFACT_SCHEMA = "aero-bench.business.state/v1"
ARTIFACT_TYPE = "business.state"
PROVIDER_ADAPTER = "inspection.business"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
PINNED_IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

TOOL_KINDS = {
    "business.claim": "claim",
    "business.start": "start",
    "business.observation_ready": "observation_ready",
    "business.submit": "submit",
    "business.complete": "complete",
    "business.fail": "fail",
    "business.cancel": "cancel",
}

REQUIRED_ARGUMENTS = {
    "claim": frozenset({"work_order_id", "actor_id"}),
    "start": frozenset({"work_order_id", "actor_id"}),
    "observation_ready": frozenset({"work_order_id", "actor_id", "observation_id"}),
    "submit": frozenset(
        {
            "work_order_id",
            "actor_id",
            "observation_id",
            "report_payload_digest",
        }
    ),
    "complete": frozenset({"work_order_id", "actor_id"}),
    "fail": frozenset({"work_order_id", "actor_id", "reason"}),
    "cancel": frozenset({"work_order_id", "actor_id", "reason"}),
}

KIND_AUTHORITY = {
    "claim": "agent",
    "start": "agent",
    "submit": "agent",
    "observation_ready": "observation_provider",
    "complete": "business",
    "fail": "business",
    "cancel": "business",
}

InspectionCommandAdapter: TypeAdapter[InspectionCommand] = TypeAdapter(
    InspectionCommand
)


class InspectionBusinessServiceError(RuntimeError):
    """A service failure carrying its stable error class."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _invalid(detail: str) -> InspectionBusinessServiceError:
    return InspectionBusinessServiceError("request.invalid", detail)


def _identity_mismatch(detail: str) -> InspectionBusinessServiceError:
    return InspectionBusinessServiceError("identity.mismatch", detail)


def _not_ready(detail: str) -> InspectionBusinessServiceError:
    return InspectionBusinessServiceError("not.ready", detail)


def _principal_denied(detail: str) -> InspectionBusinessServiceError:
    return InspectionBusinessServiceError("principal.denied", detail)


def _canonical_json(value: object) -> bytes:
    return canonical_json_bytes(value)


def _strict_object(
    value: object,
    *,
    required: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid(f"{label} must be a JSON object")
    actual = set(value)
    missing = required - actual
    extra = actual - required
    if missing or extra:
        raise _invalid(
            f"{label} fields are not exact: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string(
    value: object, *, label: str, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{label} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise _invalid(f"{label} has an invalid value")
    return value


def _integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _invalid(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise _invalid(f"{label} must be >= {minimum}")
    return value


def _sha256(value: object, *, label: str) -> str:
    result = _string(value, label=label, pattern=SHA256_PATTERN)
    if result == "0" * 64:
        raise _invalid(f"{label} cannot be a placeholder digest")
    return result


def _runtime_image(value: object) -> str:
    image = _string(value, label="runtime_image", pattern=PINNED_IMAGE_PATTERN)
    if image.rsplit(":", maxsplit=1)[1] == "0" * 64:
        raise _identity_mismatch("runtime_image cannot use a placeholder digest")
    return image


def _time_before(left: Mapping[str, int], right: Mapping[str, int]) -> bool:
    return left["tick"] < right["tick"] or left["sim_time_ns"] < right["sim_time_ns"]


def _tuple_time_before(left: tuple[int, int], right: Mapping[str, int]) -> bool:
    return left[0] < right["tick"] or left[1] < right["sim_time_ns"]


@dataclass(frozen=True, slots=True)
class WorkloadIdentity:
    """The immutable identity pinned by the contract file and environment."""

    run_id: str
    provider_id: str
    provider_port: int
    runtime_image: str
    config_digest: str
    artifact_requirement: dict[str, str | int | None]
    seed: int
    contract: ProviderWorkloadContract

    @property
    def adapter(self) -> str:
        return PROVIDER_ADAPTER


def _resolve_root_file(root: Path, reference: Mapping[str, str], *, label: str) -> Path:
    relative = PurePosixPath(reference["path"])
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise InspectionBusinessServiceError(
            "request.invalid",
            f"{label} cannot use a symbolic link: {reference['path']}",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise InspectionBusinessServiceError(
            "request.invalid", f"{label} is unavailable: {reference['path']}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise InspectionBusinessServiceError(
            "request.invalid", f"{label} is not a regular file: {reference['path']}"
        )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != reference["sha256"]:
        raise InspectionBusinessServiceError(
            "identity.mismatch",
            f"{label} SHA-256 mismatch for {reference['path']}: "
            f"expected {reference['sha256']}, got {digest}",
        )
    return resolved


def _load_canonical_model_bytes(
    path: Path,
    model_type: type[StrictModelT],
    *,
    label: str,
) -> StrictModelT:
    """Validate a file and require its raw bytes to be exactly canonical."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InspectionBusinessServiceError(
            "request.invalid", f"{label} is unavailable: {path}"
        ) from exc
    try:
        value = parse_json_object(raw)
    except (ProviderRpcError, ValueError) as exc:
        raise InspectionBusinessServiceError(
            "request.invalid", f"{label} is not strict JSON: {path}"
        ) from exc
    if _canonical_json(value) not in (raw, raw.removesuffix(b"\n")):
        raise InspectionBusinessServiceError(
            "identity.mismatch", f"{label} is not canonical JSON: {path}"
        )
    try:
        model = model_type.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise InspectionBusinessServiceError(
            "identity.mismatch", f"{label} is not a valid {model_type.__name__}"
        ) from exc
    if _canonical_json(model.model_dump(mode="json")) != _canonical_json(value):
        raise InspectionBusinessServiceError(
            "identity.mismatch",
            f"{label} does not round-trip its canonical bytes: {path}",
        )
    return model


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
    artifact_id = _string(
        raw["artifact_id"], label=f"{label}.artifact_id", pattern=IDENTIFIER_PATTERN
    )
    artifact_type = _string(
        raw["artifact_type"], label=f"{label}.artifact_type", pattern=IDENTIFIER_PATTERN
    )
    producer_id = _string(
        raw["producer_id"], label=f"{label}.producer_id", pattern=IDENTIFIER_PATTERN
    )
    visibility = _string(raw["visibility"], label=f"{label}.visibility")
    if visibility not in {"public", "private"}:
        raise _invalid(f"{label}.visibility must be public or private")
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
        raise _invalid(f"{label}.relative_path must be normalized and relative")
    max_size_bytes = _integer(
        raw["max_size_bytes"], label=f"{label}.max_size_bytes", minimum=1
    )
    if raw["source_asset_id"] is not None:
        raise _invalid(f"{label}.source_asset_id must be null for a runtime artifact")
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "producer_id": producer_id,
        "visibility": visibility,
        "relative_path": relative_path,
        "max_size_bytes": max_size_bytes,
        "source_asset_id": None,
    }


def _load_workload_identity(
    *,
    contract_path: Path,
    bundle_root: Path,
    expected_run_id: str,
    expected_provider_id: str,
    expected_provider_port: int,
    expected_seed: int,
) -> WorkloadIdentity:
    contract = _load_canonical_model_bytes(
        contract_path, ProviderWorkloadContract, label="AERO_BENCH_CONTRACT"
    )
    if contract.run_id != expected_run_id:
        raise _identity_mismatch(
            "ProviderWorkloadContract run_id differs from AERO_BENCH_RUN_ID"
        )
    if contract.workload_id != expected_provider_id:
        raise _identity_mismatch(
            "ProviderWorkloadContract workload_id differs from AERO_BENCH_WORKLOAD_ID"
        )
    if contract.seed != expected_seed:
        raise _identity_mismatch(
            "ProviderWorkloadContract seed differs from AERO_BENCH_SEED"
        )
    provider = contract.provider
    if provider.provider_id != expected_provider_id:
        raise _identity_mismatch(
            "ProviderWorkloadContract provider_id is not the workload identity"
        )
    if provider.adapter != PROVIDER_ADAPTER:
        raise _identity_mismatch(
            "Inspection Business service requires the inspection.business adapter"
        )
    if provider.port != expected_provider_port:
        raise _identity_mismatch(
            "ProviderWorkloadContract provider port is not the explicit bind port"
        )
    runtime_image = _runtime_image(provider.workload.runtime.image)
    requirements = provider.artifact_requirements
    if len(requirements) != 1:
        raise _identity_mismatch(
            "Inspection Business provider must declare exactly one formal "
            "evidence ArtifactRequirement"
        )
    requirement = _artifact_requirement(
        requirements[0].model_dump(mode="json"),
        label="Inspection Business evidence ArtifactRequirement",
    )
    if requirement["artifact_type"] != ARTIFACT_TYPE:
        raise _identity_mismatch(
            f"Inspection Business ArtifactRequirement must use {ARTIFACT_TYPE!r}"
        )
    if requirement["producer_id"] != provider.provider_id:
        raise _identity_mismatch(
            "Inspection Business ArtifactRequirement producer_id mismatch"
        )
    _resolve_root_file(
        bundle_root,
        provider.config.file.model_dump(mode="json"),
        label="provider config file",
    )
    _resolve_root_file(
        bundle_root,
        provider.config.schema_file.model_dump(mode="json"),
        label="provider config schema file",
    )
    return WorkloadIdentity(
        run_id=expected_run_id,
        provider_id=expected_provider_id,
        provider_port=expected_provider_port,
        runtime_image=runtime_image,
        config_digest=provider.config.file.sha256,
        artifact_requirement=requirement,
        seed=expected_seed,
        contract=contract,
    )


class BusinessStateWriter:
    """Write exactly one declared artifact with atomic replacement.

    The artifact root may contain only the declared file and its declared
    parent directories. Symlinks, special files, and undeclared entries fail
    closed. The declared relative path is normalized and can never escape the
    root.
    """

    def __init__(self, root: Path, requirement: Mapping[str, str | int | None]):
        try:
            self.root = root.resolve(strict=True)
        except OSError as exc:
            raise InspectionBusinessServiceError(
                "request.invalid", "Inspection Business artifact root is unavailable"
            ) from exc
        if not self.root.is_dir():
            raise _invalid("Inspection Business artifact root must be a directory")
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise InspectionBusinessServiceError(
                "artifact.write-failed",
                "Inspection Business artifact root is not writable",
            )
        self.requirement = _artifact_requirement(
            dict(requirement), label="Inspection Business evidence ArtifactRequirement"
        )
        self.path = self._resolve_output_path()
        self._reject_undeclared_files()

    def _resolve_output_path(self) -> Path:
        relative_path = self.requirement["relative_path"]
        if not isinstance(relative_path, str):
            raise _invalid(
                "Inspection Business artifact relative_path must be a string"
            )
        relative = PurePosixPath(relative_path)
        candidate = self.root.joinpath(*relative.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != self.root.parent
        ):
            raise _invalid(
                "Inspection Business artifact path cannot contain a symbolic link"
            )
        if candidate.exists() and not candidate.is_file():
            raise _invalid("Inspection Business artifact path must be a regular file")
        return candidate

    def _reject_undeclared_files(self) -> None:
        expected = self.path
        allowed_directories = {expected.parent, *expected.parent.parents}
        allowed_directories = {
            directory
            for directory in allowed_directories
            if directory == self.root or directory.is_relative_to(self.root)
        }
        for candidate in self.root.rglob("*"):
            if candidate.is_symlink():
                raise InspectionBusinessServiceError(
                    "artifact.write-failed",
                    "Inspection Business artifact root cannot contain symbolic links",
                )
            if candidate.is_dir() and candidate not in allowed_directories:
                raise InspectionBusinessServiceError(
                    "artifact.write-failed",
                    "Inspection Business artifact root contains an undeclared "
                    f"directory: {candidate.relative_to(self.root).as_posix()}",
                )
            if candidate.is_file() and candidate != expected:
                raise InspectionBusinessServiceError(
                    "artifact.write-failed",
                    "Inspection Business artifact root contains an undeclared "
                    f"file: {candidate.relative_to(self.root).as_posix()}",
                )

    def _create_declared_parents(self) -> None:
        relative_parent = self.path.parent.relative_to(self.root)
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise InspectionBusinessServiceError(
                        "artifact.write-failed",
                        "Inspection Business artifact parent path is not a "
                        "regular directory",
                    )
            else:
                current.mkdir()

    def write(self, content: bytes) -> str:
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise _invalid(
                "Inspection Business artifact max_size_bytes must be an integer"
            )
        if len(content) > max_size_bytes:
            raise InspectionBusinessServiceError(
                "artifact.write-failed",
                "Inspection Business artifact exceeds declared max_size_bytes",
            )
        self._reject_undeclared_files()
        self._create_declared_parents()
        if self.path.exists() and self.path.is_symlink():
            raise _invalid(
                "Inspection Business artifact path cannot be a symbolic link"
            )
        temporary_path: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".business.state.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            # The executor (host user) must be able to read and chmod the
            # sealed artifact; mkstemp's 0600 default would prevent that.
            os.fchmod(file_descriptor, 0o644)
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            for directory in (self.path.parent, self.root):
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except OSError as exc:
            raise InspectionBusinessServiceError(
                "artifact.write-failed",
                "Inspection Business artifact cannot be atomically written",
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        self._reject_undeclared_files()
        return hashlib.sha256(content).hexdigest()


class InspectionBusinessProviderService:
    """Deterministic JSON-line service for the Inspection Business provider.

    The service wraps the canonical WorkOrder state machine and Command/Query
    contracts from the repository package. It invents no physical state,
    observations, detections, scores, or time: commands carry the Harness
    authoritative SimulationTime and never advance time; queries are
    read-only; state transitions and business history are canonical and
    deterministic.
    """

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
            raise InspectionBusinessServiceError(
                "request.invalid", "Inspection Business bundle root is unavailable"
            ) from exc
        if not self._bundle_root.is_dir():
            raise _invalid("Inspection Business bundle root must be a directory")
        if not 1024 <= rpc_port <= 65535:
            raise _invalid("Inspection Business RPC port is outside the user range")
        if workload_identity.provider_port != rpc_port:
            raise _identity_mismatch("workload provider port must equal the RPC port")
        if SHA256_PATTERN.fullmatch(session_token) is None or session_token == "0" * 64:
            raise _invalid(
                "Inspection Business expected session token must be an "
                "executor-issued non-placeholder SHA-256 digest"
            )
        self._rpc_port = rpc_port
        self._workload_identity = workload_identity
        self._writer = BusinessStateWriter(
            artifact_root, workload_identity.artifact_requirement
        )
        self._config: InspectionBusinessConfig | None = None
        self._session_token: str = session_token
        self._machine: BusinessStateMachine | None = None
        self._current: tuple[int, int] | None = None
        # The external provider receives resolved scenario identity only on its
        # first staged barrier input. It must remain fixed for this run even
        # across a state-machine reset.
        self._scenario_digest: str | None = None
        self._accepted_stage_inputs: list[dict[str, object]] = []
        self._published_history_length = 0
        self._finalization_receipt: dict[str, object] | None = None
        self._shutdown_requested = asyncio.Event()
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def shutdown_requested(self) -> asyncio.Event:
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def close_runtime(self) -> None:
        self._closed = True

    def _probe_response(self) -> dict[str, str]:
        """The exact shared readiness identity frame.

        ``containers/readiness_probe.py`` recomputes the expected frame from
        the workload contract and environment and requires this response to
        equal it field for field, so the Docker HEALTHCHECK proves the
        service serves this run's real identity.
        """

        identity = self._workload_identity
        return {
            "schema_version": PROVIDER_PROBE_SCHEMA,
            "status": "accepting",
            "run_id": identity.run_id,
            "provider_id": identity.provider_id,
            "adapter": identity.adapter,
            "runtime_image": identity.runtime_image,
            "config_digest": identity.config_digest,
        }

    def _load_provider_config(self) -> InspectionBusinessConfig:
        identity = self._workload_identity
        config_path = _resolve_root_file(
            self._bundle_root,
            identity.contract.provider.config.file.model_dump(mode="json"),
            label="provider config file",
        )
        config = _load_canonical_model_bytes(
            config_path, InspectionBusinessConfig, label="provider config"
        )
        if config.provider_id != identity.provider_id:
            raise _identity_mismatch(
                "provider config provider_id differs from the workload contract"
            )
        return config

    def _parse_prepare(self, payload: Mapping[str, Any]) -> InspectionBusinessConfig:
        # Authentication already ran in handle; this parse only validates the
        # authenticated frame's shape and binding and mutates nothing.
        identity = self._workload_identity
        raw = _strict_object(
            payload,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "protocol_version",
                    "runtime_image",
                    "config_digest",
                    "artifact_requirements",
                    "session_token",
                    "task_id",
                    "package_digest",
                    "task_package",
                }
            ),
            label="Inspection Business prepare request",
        )
        if (
            _string(raw["provider_id"], label="provider_id", pattern=IDENTIFIER_PATTERN)
            != identity.provider_id
        ):
            raise _identity_mismatch(
                "Inspection Business provider_id differs from the workload contract"
            )
        if _sha256(raw["run_id"], label="run_id") != identity.run_id:
            raise _identity_mismatch(
                "Inspection Business run_id differs from the workload contract"
            )
        if (
            _string(raw["protocol_version"], label="protocol_version")
            != PROTOCOL_VERSION
        ):
            raise _identity_mismatch(
                "Inspection Business protocol version does not match the service"
            )
        if _runtime_image(raw["runtime_image"]) != identity.runtime_image:
            raise _identity_mismatch(
                "Inspection Business runtime image differs from the workload contract"
            )
        if _sha256(raw["config_digest"], label="config_digest") != (
            identity.config_digest
        ):
            raise _identity_mismatch(
                "Inspection Business config digest differs from the workload contract"
            )
        artifact_requirements = raw["artifact_requirements"]
        if (
            not isinstance(artifact_requirements, list)
            or len(artifact_requirements) != 1
        ):
            raise _identity_mismatch(
                "Inspection Business artifact requirements are not contract-bound"
            )
        if _canonical_json(artifact_requirements[0]) != _canonical_json(
            identity.artifact_requirement
        ):
            raise _identity_mismatch(
                "Inspection Business artifact requirement differs from the "
                "workload contract"
            )
        config = self._load_provider_config()
        task_package = config.task_package.model_dump(mode="json")
        if (
            _string(raw["task_id"], label="task_id", pattern=IDENTIFIER_PATTERN)
            != config.task_package.task_id
        ):
            raise _identity_mismatch(
                "Inspection Business task_id differs from the pinned task package"
            )
        if (
            _sha256(raw["package_digest"], label="package_digest")
            != hashlib.sha256(_canonical_json(task_package)).hexdigest()
        ):
            raise _identity_mismatch(
                "Inspection Business package digest differs from the pinned "
                "task package"
            )
        if _canonical_json(raw["task_package"]) != _canonical_json(task_package):
            raise _identity_mismatch(
                "Inspection Business task package differs from the pinned "
                "provider config"
            )
        return config

    def _require_prepared(self) -> InspectionBusinessConfig:
        if self._config is None:
            raise _not_ready("Inspection Business provider has not completed prepare")
        return self._config

    def _require_open(self) -> None:
        if self._closed:
            raise _not_ready("Inspection Business provider service is closed")

    def _require_session_token(self, payload: Mapping[str, Any]) -> None:
        presented = payload.get("session_token") if isinstance(payload, dict) else None
        if not isinstance(presented, str) or not hmac.compare_digest(
            presented, self._session_token
        ):
            # Constant-time compare before any closed, prepared, or machine
            # state is consulted: a frame without the executor-issued token
            # forges no principal and discloses no runtime lifecycle state.
            raise _principal_denied(
                "Inspection Business request cannot present the "
                "executor-issued run-scoped session token"
            )

    def _require_identity(self, payload: Mapping[str, Any]) -> None:
        if "provider_id" not in payload or "run_id" not in payload:
            raise _invalid("Inspection Business request must identify provider and run")
        if payload["provider_id"] != self._workload_identity.provider_id:
            raise _identity_mismatch(
                "Inspection Business request provider identity mismatch"
            )
        if payload["run_id"] != self._workload_identity.run_id:
            raise _identity_mismatch(
                "Inspection Business request run identity mismatch"
            )

    def _require_not_finalized(self) -> None:
        if self._finalization_receipt is not None:
            raise _not_ready("Inspection Business provider is already finalized")

    def _require_machine(self) -> BusinessStateMachine:
        machine = self._machine
        if machine is None or self._current is None:
            raise _not_ready(
                "Inspection Business provider must be reset before this operation"
            )
        return machine

    def _require_command_time(self, issued_at: Mapping[str, int]) -> None:
        if self._current is None:
            raise _not_ready(
                "Inspection Business provider must be reset before commands"
            )
        if _tuple_time_before(self._current, issued_at):
            raise _invalid(
                "Inspection Business command time exceeds the provider barrier"
            )

    def _authority_role(self, kind: str, agent_id: str) -> str:
        package = self._config.task_package if self._config is not None else None
        if package is None:
            raise _not_ready("Inspection Business provider has not completed prepare")
        grant = next(
            (actor for actor in package.actors if actor.actor_id == agent_id), None
        )
        if grant is None:
            raise _principal_denied(
                f"principal {agent_id!r} holds no inspection actor grant"
            )
        if KIND_AUTHORITY[kind] != grant.role:
            raise _principal_denied(
                f"principal {agent_id!r} holds role {grant.role!r} and cannot "
                f"issue {kind!r}"
            )
        return grant.role

    def _command_from_request(self, request: CommandRequest) -> BusinessCommandEnvelope:
        kind = TOOL_KINDS.get(request.tool_id)
        if kind is None:
            raise _invalid(f"unsupported business tool {request.tool_id!r}")
        arguments: dict[str, Any] = {}
        for argument in request.arguments:
            if argument.name in arguments:
                raise _invalid(f"duplicate command argument {argument.name!r}")
            arguments[argument.name] = argument.value
        required = REQUIRED_ARGUMENTS[kind]
        if set(arguments) != required:
            raise _invalid(f"{kind} requires exactly {sorted(required)} arguments")
        work_order_id = arguments["work_order_id"]
        actor_id = arguments["actor_id"]
        if not isinstance(work_order_id, str) or not isinstance(actor_id, str):
            raise _invalid("work_order_id and actor_id must be strings")
        if actor_id != request.agent_id:
            raise _principal_denied(
                f"command actor {actor_id!r} does not match the attested "
                f"principal {request.agent_id!r}"
            )
        self._authority_role(kind, request.agent_id)
        command_raw: dict[str, Any] = {
            "kind": kind,
            "command_id": request.command_id,
            "work_order_id": work_order_id,
            "actor_id": actor_id,
            "issued_at": request.issued_at.model_dump(mode="json"),
        }
        if kind in {"observation_ready", "submit"}:
            observation_id = arguments["observation_id"]
            if not isinstance(observation_id, str):
                raise _invalid("observation_id must be a string")
            command_raw["observation_id"] = observation_id
        if kind == "submit":
            report_payload_digest = arguments["report_payload_digest"]
            if (
                not isinstance(report_payload_digest, str)
                or SHA256_PATTERN.fullmatch(report_payload_digest) is None
            ):
                raise _invalid("report_payload_digest must be a SHA-256 digest")
            command_raw["report_payload_digest"] = report_payload_digest
        if kind in {"fail", "cancel"}:
            reason = arguments["reason"]
            if not isinstance(reason, str):
                raise _invalid("reason must be a string")
            command_raw["reason"] = reason
        try:
            command = InspectionCommandAdapter.validate_python(command_raw)
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "command arguments do not form a valid business command"
            ) from exc
        return BusinessCommandEnvelope(run_id=request.run_id, command=command)

    def _artifact_content(self, *, event_chain_root: str) -> bytes:
        machine = self._require_machine()
        history = machine.history
        history_root = history[-1].record_hash if history else "0" * 64
        requirement = self._workload_identity.artifact_requirement
        artifact = {
            "source_artifact_id": requirement["artifact_id"],
            "event_chain_root": event_chain_root,
            "business_history_root": history_root,
            "history": [record.model_dump(mode="json") for record in history],
            "state": machine.state.model_dump(mode="json"),
        }
        return _canonical_json(artifact)

    def _write_artifact(self, *, event_chain_root: str) -> str:
        return self._writer.write(
            self._artifact_content(event_chain_root=event_chain_root)
        )

    def _state_digest(self) -> str:
        machine = self._require_machine()
        history = machine.history
        assert self._current is not None
        tick, sim_time_ns = self._current
        snapshot = {
            "run_id": self._workload_identity.run_id,
            "provider_id": self._workload_identity.provider_id,
            "config_digest": self._workload_identity.config_digest,
            "seed": self._workload_identity.seed,
            "scenario_digest": self._scenario_digest,
            "current_time": {"tick": tick, "sim_time_ns": sim_time_ns},
            "accepted_stage_inputs": self._accepted_stage_inputs,
            "business_history_root": (history[-1].record_hash if history else "0" * 64),
            "history_length": len(history),
            "state": machine.state.model_dump(mode="json"),
        }
        return hashlib.sha256(_canonical_json(snapshot)).hexdigest()

    def _receipt(self) -> dict[str, object]:
        machine = self._require_machine()
        assert self._current is not None
        tick, sim_time_ns = self._current
        time_value = {"tick": tick, "sim_time_ns": sim_time_ns}
        digest = self._state_digest()
        history = machine.history
        latest_stage_input = (
            self._accepted_stage_inputs[-1] if self._accepted_stage_inputs else None
        )
        # The strict business-state artifact schema cannot gain provider-specific
        # fields. These authoritative event fields are instead sealed by the
        # final artifact's event_chain_root and expose the accepted causal input.
        stage_input_predecessors: object = (
            latest_stage_input["predecessor_barriers"]
            if latest_stage_input is not None
            else []
        )
        state_event: dict[str, object] = {
            "provider_id": self._workload_identity.provider_id,
            "event_id": f"state.{tick}",
            "time": time_value,
            "payload_schema_id": STATE_SCHEMA,
            "payload": [
                {"name": "state_digest", "value": digest},
                {
                    "name": "business_history_root",
                    "value": (history[-1].record_hash if history else "0" * 64),
                },
                {"name": "history_length", "value": len(history)},
                {"name": "current_time_ns", "value": sim_time_ns},
                {
                    "name": "stage_input_scenario_digest",
                    "value": (
                        latest_stage_input["scenario_digest"]
                        if latest_stage_input is not None
                        else None
                    ),
                },
                {
                    "name": "stage_input_scene_state_digest",
                    "value": (
                        latest_stage_input["scene_state_digest"]
                        if latest_stage_input is not None
                        else None
                    ),
                },
                {
                    "name": "stage_input_motion_barrier_digest",
                    "value": (
                        latest_stage_input["motion_barrier_digest"]
                        if latest_stage_input is not None
                        else None
                    ),
                },
                {
                    "name": "stage_input_predecessor_barriers",
                    "value": _canonical_json(stage_input_predecessors).decode("utf-8"),
                },
                {
                    "name": "stage_input_count",
                    "value": len(self._accepted_stage_inputs),
                },
            ],
        }
        events: list[dict[str, object]] = [state_event]

        statuses = [item.status.value for item in machine.state.work_orders]
        business_state = statuses[0] if len(set(statuses)) == 1 else "mixed"
        observed_count = sum(
            item.observation_id is not None for item in machine.state.work_orders
        )
        observation_state = (
            "ready"
            if observed_count == len(machine.state.work_orders)
            else "partial"
            if observed_count
            else "pending"
        )
        completed_count = sum(
            item.status.value == "completed" for item in machine.state.work_orders
        )
        submitted_count = sum(
            item.status.value in {"submitted", "completed"}
            for item in machine.state.work_orders
        )
        network_state = (
            "delivered"
            if completed_count == len(machine.state.work_orders)
            else "submitted"
            if submitted_count
            else "pending"
        )
        progress_by_state = {
            "created": 0.0,
            "claimed": 0.15,
            "in_progress": 0.3,
            "observation_ready": 0.55,
            "submitted": 0.8,
            "completed": 1.0,
            "failed": 1.0,
            "cancelled": 1.0,
        }
        status = {
            "business_state": business_state,
            "observation_state": observation_state,
            "network_state": network_state,
            "task_progress_ratio": sum(progress_by_state[item] for item in statuses)
            / len(statuses),
        }
        events.append(
            {
                "provider_id": self._workload_identity.provider_id,
                "event_id": "public.status",
                "time": time_value,
                "payload_schema_id": "public.status.v3",
                "payload": [
                    {
                        "name": "status",
                        "value": _canonical_json(status).decode("utf-8"),
                    }
                ],
            }
        )

        requirement = self._workload_identity.artifact_requirement
        if requirement["visibility"] == "public":
            work_orders = {
                item.work_order_id: item for item in machine.state.work_orders
            }
            for record in history[self._published_history_length :]:
                transition = record.transition
                work_order = work_orders[transition.work_order_id]
                evidence = [
                    {
                        "artifact_id": requirement["artifact_id"],
                        "selector": f"history/{record.sequence}",
                    }
                ]
                events.append(
                    {
                        "provider_id": self._workload_identity.provider_id,
                        "event_id": "public.event",
                        "time": time_value,
                        "payload_schema_id": "public.event.v3",
                        "payload": [
                            {"name": "severity", "value": "info"},
                            {"name": "entity_id", "value": work_order.target_id},
                            {"name": "state", "value": transition.event},
                            {
                                "name": "evidence",
                                "value": _canonical_json(evidence).decode("utf-8"),
                            },
                        ],
                    }
                )
        self._published_history_length = len(history)
        return {
            "run_id": self._workload_identity.run_id,
            "provider_id": self._workload_identity.provider_id,
            "reached": time_value,
            "state_digest": digest,
            "events": events,
        }

    def _command_receipts(
        self,
        request: CommandRequest,
        phases: tuple[str, ...],
        detail: str | None = None,
    ) -> list[dict[str, object]]:
        return [
            {
                "run_id": request.run_id,
                "command_id": request.command_id,
                "provider_id": self._workload_identity.provider_id,
                "phase": phase,
                "time": request.issued_at.model_dump(mode="json"),
                "detail": detail,
            }
            for phase in phases
        ]

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        async with self._lock:
            if operation == "probe":
                # Token-free and stateless: the shared readiness probe is an
                # unauthenticated identity report and mutates no state.
                self._require_open()
                if payload != {}:
                    raise _invalid(
                        "Inspection Business provider probe request must be empty"
                    )
                return self._probe_response()
            # Authentication precedes every lifecycle gate on a stateful
            # frame: without the executor-issued token the caller cannot
            # distinguish a closed runtime from an unprepared one, and with
            # it a closed runtime answers only the terminal not.ready. This
            # is the only credential check a stateful frame performs.
            self._require_session_token(payload)
            self._require_open()
            if operation == "prepare":
                return await self._handle_prepare(payload)
            if operation == "reset":
                return self._handle_reset(payload)
            if operation == "step_stage":
                return self._handle_step_stage(payload)
            if operation == "command":
                return self._handle_command(payload)
            if operation == "query":
                return self._handle_query(payload)
            if operation == "snapshot":
                return self._handle_snapshot(payload)
            if operation == "finalize":
                return self._handle_finalize(payload)
            if operation == "shutdown":
                return self._handle_shutdown(payload)
            raise InspectionBusinessServiceError(
                "operation.unknown",
                f"Inspection Business operation is not supported: {operation}",
            )

    async def serve_rpc(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """The JsonLineRpcServer-facing handler.

        Typed service failures become canonical error envelopes carrying their
        stable failure class, so the peer never sees a bare connection drop.
        """

        try:
            return await self.handle(operation, payload)
        except InspectionBusinessServiceError as exc:
            return {"error": {"code": exc.code, "detail": exc.detail}}

    async def _handle_prepare(self, payload: Mapping[str, Any]) -> dict[str, object]:
        # Parse before the double-prepare guard: authentication already ran
        # in handle and the parse mutates nothing, so a correctly
        # authenticated duplicate prepare still reports not.ready.
        config = self._parse_prepare(payload)
        if self._config is not None:
            raise _not_ready("Inspection Business provider prepare called twice")
        self._config = config
        identity = self._workload_identity
        return {
            "status": "ready",
            "provider_id": identity.provider_id,
            "protocol_version": PROTOCOL_VERSION,
            "runtime_image": identity.runtime_image,
            "config_digest": identity.config_digest,
        }

    def _handle_reset(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_not_finalized()
        config = self._require_prepared()
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "seed"}),
            label="Inspection Business reset request",
        )
        if _integer(raw["seed"], label="seed") != self._workload_identity.seed:
            raise _identity_mismatch(
                "reset seed does not match the explicit provider workload contract"
            )
        self._machine = BusinessStateMachine(
            config.task_package, run_id=self._workload_identity.run_id
        )
        self._current = (0, 0)
        self._accepted_stage_inputs = []
        self._published_history_length = 0
        self._finalization_receipt = None
        return {"receipt": self._receipt()}

    def _parse_stage_request(
        self, raw_request: object
    ) -> tuple[BusinessEnvironmentStageRequest, SceneState, str]:
        """Parse a canonical business/environment input without mutating state."""

        if not isinstance(raw_request, dict):
            raise _invalid("Inspection Business staged request must be a JSON object")
        try:
            request = BusinessEnvironmentStageRequest.model_validate(raw_request)
            canonical_request = BusinessEnvironmentStageRequest.model_validate(
                request.model_dump(mode="json")
            )
            canonical_scene_state = SceneState.model_validate(
                request.scene_state.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise _invalid(
                "Inspection Business BusinessEnvironmentStageRequest is invalid"
            ) from exc
        if (
            canonical_request != request
            or canonical_scene_state != request.scene_state
            or _canonical_json(raw_request)
            != _canonical_json(request.model_dump(mode="json"))
        ):
            raise _invalid(
                "Inspection Business staged request is not canonically serialized"
            )
        computed_scene_state_digest = scene_state_digest_value(canonical_scene_state)
        if (
            request.scene_state_digest != computed_scene_state_digest
            or canonical_scene_state.scene_state_digest != computed_scene_state_digest
        ):
            raise _invalid(
                "Inspection Business staged SceneState digest does not match content"
            )
        predecessor_stages = tuple(
            predecessor.stage for predecessor in request.predecessor_barriers
        )
        if predecessor_stages not in {("motion",), ("motion", "network")}:
            raise _invalid(
                "Inspection Business staged predecessors must be motion or motion then "
                "network"
            )
        motion_barrier = request.predecessor_barriers[0]
        if (
            motion_barrier.stage != "motion"
            or motion_barrier.barrier_digest
            != canonical_scene_state.stage_barrier.barrier_digest
        ):
            raise _invalid(
                "Inspection Business staged motion predecessor does not bind the "
                "SceneState"
            )
        return canonical_request, canonical_scene_state, computed_scene_state_digest

    def _stage_input_record(
        self,
        request: BusinessEnvironmentStageRequest,
        *,
        scene_state_digest: str,
    ) -> dict[str, object]:
        return {
            "run_id": request.run_id,
            "scenario_digest": request.scenario_digest,
            "target": request.target.model_dump(mode="json"),
            "scene_state_digest": scene_state_digest,
            "motion_barrier_digest": request.predecessor_barriers[0].barrier_digest,
            "predecessor_barriers": [
                predecessor.model_dump(mode="json")
                for predecessor in request.predecessor_barriers
            ],
        }

    def _empty_scene_contribution(
        self, request: BusinessEnvironmentStageRequest
    ) -> SceneContribution:
        """This provider has no declared entity ownership to contribute."""

        fields: dict[str, object] = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": request.run_id,
            "scenario_digest": request.scenario_digest,
            "at": request.target,
            "stage": "business_environment",
            "provider_id": self._workload_identity.provider_id,
            "samples": (),
            "attribute_updates": (),
            "payload_digest": "0" * 64,
            "contribution_digest": "0" * 64,
        }
        payload_candidate = SceneContribution.model_construct(**fields)
        fields["payload_digest"] = scene_contribution_payload_digest_value(
            payload_candidate
        )
        contribution_candidate = SceneContribution.model_construct(**fields)
        fields["contribution_digest"] = scene_contribution_digest_value(
            contribution_candidate
        )
        try:
            return SceneContribution(**fields)
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "Inspection Business cannot construct its empty SceneContribution"
            ) from exc

    def _handle_step_stage(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_not_finalized()
        raw = _strict_object(
            payload,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "protocol_version",
                    "session_token",
                    "request",
                }
            ),
            label="Inspection Business step_stage request",
        )
        if _string(raw["protocol_version"], label="protocol_version") != PROTOCOL_VERSION:
            raise _identity_mismatch(
                "Inspection Business staged protocol version does not match the service"
            )
        request, _scene_state, computed_scene_state_digest = self._parse_stage_request(
            raw["request"]
        )
        identity = self._workload_identity
        if request.run_id != identity.run_id:
            raise _identity_mismatch(
                "Inspection Business staged request run identity mismatch"
            )
        if request.provider_id != identity.provider_id:
            raise _identity_mismatch(
                "Inspection Business staged request provider identity mismatch"
            )
        if (
            self._scenario_digest is not None
            and request.scenario_digest != self._scenario_digest
        ):
            raise _identity_mismatch(
                "Inspection Business staged request scenario digest changed mid-session"
            )
        self._require_machine()
        assert self._current is not None
        target = (request.target.tick, request.target.sim_time_ns)
        if target[0] <= self._current[0] or target[1] <= self._current[1]:
            raise _invalid(
                "Inspection Business staged target must advance monotonically"
            )

        # All validation above is side-effect-free. Commit the exact accepted
        # causal input atomically with the provider's authoritative time change.
        if self._scenario_digest is None:
            self._scenario_digest = request.scenario_digest
        self._current = target
        self._accepted_stage_inputs.append(
            self._stage_input_record(
                request,
                scene_state_digest=computed_scene_state_digest,
            )
        )
        try:
            receipt = StepReceipt.model_validate(self._receipt())
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "Inspection Business cannot construct its staged StepReceipt"
            ) from exc
        contribution = self._empty_scene_contribution(request)
        try:
            result = BusinessEnvironmentStageResult(
                schema_version="aero-bench.provider-stage-result/v1",
                run_id=request.run_id,
                scenario_digest=request.scenario_digest,
                provider_id=identity.provider_id,
                target=request.target,
                stage="business_environment",
                step_receipt=receipt,
                step_receipt_digest=step_receipt_digest_value(receipt),
                contribution=contribution,
                input_scene_state_digest=computed_scene_state_digest,
                predecessor_barriers=request.predecessor_barriers,
            )
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "Inspection Business cannot construct its staged result"
            ) from exc
        return {"result": result.model_dump(mode="json")}

    def _handle_command(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_not_finalized()
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "request"}),
            label="Inspection Business command request",
        )
        try:
            request = CommandRequest.model_validate(raw["request"])
        except (TypeError, ValueError) as exc:
            raise _invalid("Inspection Business command request is invalid") from exc
        if request.run_id != self._workload_identity.run_id:
            raise _identity_mismatch(
                "Inspection Business command request belongs to another run"
            )
        machine = self._require_machine()
        try:
            envelope = self._command_from_request(request)
            self._require_command_time(request.issued_at.model_dump(mode="json"))
            result = machine.apply(envelope)
        except InspectionBusinessServiceError as exc:
            if exc.code in {"principal.denied", "not.ready"}:
                raise
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), exc.detail
                )
            }
        except ValueError as exc:
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), str(exc)
                )
            }
        response = {
            "receipts": self._command_receipts(
                request,
                ("received", "accepted", "applied", "completed"),
                f"applied:{envelope.command.kind}",
            ),
            "history_record": result.history_record.model_dump(mode="json"),
        }
        if envelope.command.kind == "complete":
            response["state_receipt"] = self._receipt()
        return response

    def _handle_query(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "query"}),
            label="Inspection Business query request",
        )
        try:
            query = BusinessQuery.model_validate(raw["query"])
        except (TypeError, ValueError) as exc:
            raise _invalid("Inspection Business query is invalid") from exc
        if query.run_id != self._workload_identity.run_id:
            raise _identity_mismatch("Inspection Business query belongs to another run")
        machine = self._require_machine()
        self._require_command_time(query.issued_at.model_dump(mode="json"))
        try:
            result = query_business_state(machine.state, query)
        except ValueError as exc:
            raise InspectionBusinessServiceError("query.rejected", str(exc)) from exc
        return {"result": result.model_dump(mode="json")}

    def _handle_snapshot(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_machine()
        return {"snapshot_digest": self._state_digest()}

    def _handle_finalize(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "request"}),
            label="Inspection Business finalize request",
        )
        try:
            request = ProviderFinalizationRequest.model_validate(raw["request"])
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "Inspection Business ProviderFinalizationRequest is invalid"
            ) from exc
        if request.run_id != self._workload_identity.run_id:
            raise _identity_mismatch(
                "Inspection Business finalization belongs to another run"
            )
        if self._finalization_receipt is not None:
            if (
                self._finalization_receipt["event_chain_root"]
                != request.event_chain_root
            ):
                raise _not_ready(
                    "Inspection Business finalization root cannot be changed"
                )
            return {"receipt": dict(self._finalization_receipt)}

        self._require_machine()
        assert self._current is not None
        if (
            request.terminal_time.tick,
            request.terminal_time.sim_time_ns,
        ) != self._current:
            raise _identity_mismatch(
                "Inspection Business finalization time differs from provider time"
            )
        digest = self._write_artifact(event_chain_root=request.event_chain_root)
        size_bytes = self._writer.path.stat().st_size
        requirement = self._workload_identity.artifact_requirement
        receipt: dict[str, object] = {
            "schema_version": "aero-bench.provider-finalization-receipt/v1",
            "run_id": self._workload_identity.run_id,
            "provider_id": self._workload_identity.provider_id,
            "event_chain_root": request.event_chain_root,
            "artifacts": [
                {
                    "artifact_id": requirement["artifact_id"],
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            ],
        }
        self._finalization_receipt = receipt
        return {"receipt": dict(receipt)}

    def _handle_shutdown(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self.request_shutdown()
        return {"status": "stopped"}


def _validate_sha256_environment(value: str, *, label: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None or value == "0" * 64:
        raise SystemExit(f"{label} must be a non-placeholder SHA-256 digest")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AERO-BENCH Inspection Business provider"
    )
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


def _environment_integer(name: str) -> int:
    value = _required_environment(name)
    try:
        return int(value, 10)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a decimal integer") from exc


def _identity_from_environment() -> WorkloadIdentity:
    bind_port = _environment_port("AERO_BENCH_PROVIDER_PORT")
    bundle_root = Path(_required_environment("AERO_BENCH_BUNDLE_DIR"))
    return _load_workload_identity(
        contract_path=Path(_required_environment("AERO_BENCH_CONTRACT")),
        bundle_root=bundle_root,
        expected_run_id=_validate_sha256_environment(
            _required_environment("AERO_BENCH_RUN_ID"), label="AERO_BENCH_RUN_ID"
        ),
        expected_provider_id=_string(
            _required_environment("AERO_BENCH_WORKLOAD_ID"),
            label="AERO_BENCH_WORKLOAD_ID",
            pattern=IDENTIFIER_PATTERN,
        ),
        expected_provider_port=bind_port,
        expected_seed=_environment_integer("AERO_BENCH_SEED"),
    )


def _serve() -> int:
    bind_host = _string(
        _required_environment("AERO_BENCH_PROVIDER_BIND_HOST"),
        label="AERO_BENCH_PROVIDER_BIND_HOST",
        pattern=HOST_PATTERN,
    )
    bind_port = _environment_port("AERO_BENCH_PROVIDER_PORT")
    identity = _identity_from_environment()
    session_token = _validate_sha256_environment(
        _required_environment("AERO_BENCH_PROVIDER_TOKEN"),
        label="AERO_BENCH_PROVIDER_TOKEN",
    )
    service = InspectionBusinessProviderService(
        bundle_root=Path(_required_environment("AERO_BENCH_BUNDLE_DIR")),
        artifact_root=Path(_required_environment("AERO_BENCH_ARTIFACT_DIR")),
        rpc_port=bind_port,
        workload_identity=identity,
        session_token=session_token,
    )
    server = JsonLineRpcServer(service.serve_rpc)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, service.request_shutdown)
            except (NotImplementedError, RuntimeError):
                pass
        handle = await server.start(host=bind_host, port=bind_port)
        try:
            await service.shutdown_requested.wait()
        finally:
            await server.graceful_close()
            await handle.wait_closed()

    asyncio.run(run())
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mode == ["provider", "serve"]:
        return _serve()
    raise SystemExit("service mode must be 'provider serve'")


if __name__ == "__main__":
    raise SystemExit(main())
