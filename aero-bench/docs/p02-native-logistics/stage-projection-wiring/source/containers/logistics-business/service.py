"""Logistics Business provider service (development checkpoint).

The service is the only authority for the canonical logistics order ledger
(``LogisticsOrdersService``), the facility-catalogue state, the staged
business-environment receipts, and the append-only hash-chained history. It
runs behind a single JSON-line RPC surface
(``aero_bench.providers.rpc.JsonLineRpcServer``), binds itself to the
digest-pinned ``ProviderWorkloadContract`` and provider config bytes, and
invents no physical state, telemetry, delivery evidence, or time.

Trust chain for command principals. An Agent reaches the Gateway only with its
run-scoped token, the Gateway attests ``CommandRequest.agent_id`` from that
token as a native infrastructure principal, and the Harness session is the
only client that performs ``prepare``. The executor issues this workload a
run-scoped session token at container creation and injects the same value into
the Harness alone, so the first ``prepare`` and every later frame must present
it. A peer workload cannot race the legitimate session to the first prepare,
bind its own token, or forge a principal by replaying public identity fields.
Authentication is constant-time and precedes every lifecycle gate on a
stateful frame.

Boundary and capability scope. This is an incomplete-provider development
checkpoint, not a runnable logistics milestone. Only the non-physical order
transitions the canonical ``OrderActorRole`` permits are applied
(offer/accept/reject/assign/cancel/fail). The physical
pickup/handoff/deliver/charge transitions require the later authoritative
physical-evidence interface and are explicitly refused with an ordered
``received, failed`` receipt describing the missing interface; this service
never accepts an arbitrary ``evidence_ref`` or a synthetic boolean as proof.
``probe`` stays token-free and side-effect-free; the declared provider
capabilities in ``prepare`` must be a subset of the capabilities this service
actually implements, so an unimplemented ``logistics.*`` capability is never
claimed.

Stable failure classes returned as ``{"error": {"code", "detail"}}``:

- ``request.invalid``: a structurally invalid frame or payload.
- ``identity.mismatch``: a request that disagrees with the pinned workload
  contract, provider config bytes, or service identity.
- ``not.ready``: an operation that requires prepare/reset which has not
  happened or a service that is already finalized/closed.
- ``principal.denied``: a request that cannot present the Harness-granted
  session token or whose native principal holds no logistics binding.
- ``query.rejected``: a well-formed query the authoritative state rejects.
- ``artifact.write-failed``: the declared evidence artifact could not be
  written exactly as declared.
- ``operation.unknown``: the operation name is not served.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import hmac
import os
import re
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeVar

from aero_bench.config.models import StrictModel
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.providers.logistics_business.config import (
    LogisticsBusinessConfig,
    LogisticsInfrastructurePrincipal,
)
from aero_bench.providers.logistics_business.protocol import (
    LOGISTICS_ORDER_CREATE_TOOL,
    PROTOCOL_VERSION,
)
from aero_bench.providers.logistics_business.native_parcel import (
    NATIVE_PARCEL_ADAPTER, NATIVE_PARCEL_CAPABILITY, NativeParcelBusinessConfig,
)
from aero_bench.tasks.logistics.native_parcel_rpc import (
    NativeParcelRpcComponent, NativeParcelSceneFrame, PARCEL_SNAPSHOT_OPERATION,
    PARCEL_PICKUP_TOOL, PARCEL_DROPOFF_TOOL,
)
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_CAPABILITY,
    SCHEDULED_ARRIVAL_EVENT_SCHEMA,
    ScheduledOrderApplication,
)
from aero_bench.providers.logistics_business.queries import (
    LogisticsBusinessQuery,
    LogisticsBusinessQueryResult,
    LogisticsFacilityView,
    LogisticsOrderView,
    order_view,
)
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
    SimulationTime,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    scene_state_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facility_presence import (
    assess_facility_presence,
)
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationBatch,
    ObservationIngestResult,
    ObservationIngressError,
    ObservationJournal,
    ObservationJournalRecord,
    append_observations_batch,
    empty_observation_journal,
    observation_ingest_result,
    validate_batch_against_package,
)
from aero_bench.tasks.logistics.order_arrivals import (
    ORDER_INTRODUCING_ROLE,
    ORDER_PACKAGE_ACTOR_ID,
    ORDER_PACKAGE_SOURCE,
    OrderArrivalCommand,
    OrderArrivalEvent,
    OrderArrivalService,
    OrderArrivalSnapshot,
    validate_order_for_arrival,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsHistoryRecord,
    LogisticsLedgerState,
    LogisticsOrdersService,
    LogisticsIdentifier,
    OrderRequest,
    OrderState,
    OrderTransition,
)

StrictModelT = TypeVar("StrictModelT", bound=StrictModel)


STATE_SCHEMA = "logistics.business.state.v1"
TRANSITION_EVENT_SCHEMA = "logistics.business.transition.v1"
ARTIFACT_SCHEMA = "aero-bench.logistics-business-state/v1"
ARTIFACT_TYPE = "logistics.business.state"
PROVIDER_ADAPTER = "logistics.business"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
#: The real authenticated online-order creation tool id served by this
#: workload; the value is the shared on-wire contract.
CREATE_TOOL_ID = LOGISTICS_ORDER_CREATE_TOOL
#: Capabilities this checkpoint service actually implements (business state and
#: order authority without physical evidence).
IMPLEMENTED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "logistics.orders.authority",
        "logistics.facilities.state",
        SCHEDULED_ARRIVAL_CAPABILITY,
    }
)
#: Capabilities whose physical interface is NOT wired yet and therefore must
#: not be claimed by a manifest for this service.
PENDING_CAPABILITIES: frozenset[str] = frozenset(
    {
        "logistics.airspace.events",
        "logistics.delivery.observation",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
LOGISTICS_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
PINNED_IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

TOOL_KINDS = {
    "logistics.order.offer": "offer",
    "logistics.order.accept": "accept",
    "logistics.order.reject": "reject",
    "logistics.order.assign": "assign",
    "logistics.order.cancel": "cancel",
    "logistics.order.fail": "fail",
    "logistics.order.pick_up": "pick_up",
    "logistics.order.handoff": "handoff",
    "logistics.order.deliver": "deliver",
    CREATE_TOOL_ID: "create",
}

#: Physical transitions still require the authoritative physical-evidence
#: interface and are explicitly refused until it is wired.
PHYSICAL_PENDING_EVENTS: frozenset[str] = frozenset(
    {"pick_up", "handoff", "deliver"}
)

REQUIRED_ARGUMENTS = {
    "offer": frozenset(
        {"order_id", "assignee_id", "expected_version", "actor_id"}
    ),
    "accept": frozenset({"order_id", "expected_version", "actor_id"}),
    "reject": frozenset(
        {"order_id", "reason", "expected_version", "actor_id"}
    ),
    "assign": frozenset(
        {
            "order_id",
            "assignee_id",
            "capacity_kg",
            "expected_version",
            "actor_id",
        }
    ),
    "cancel": frozenset(
        {"order_id", "reason", "expected_version", "actor_id"}
    ),
    "fail": frozenset(
        {"order_id", "reason", "expected_version", "actor_id"}
    ),
    "create": frozenset(
        {
            "order_id",
            "origin_facility_id",
            "destination_facility_id",
            "hub_handoff_facility_id",
            "cargo_mass_kg",
            "release_time_s",
            "deadline_s",
            "actor_id",
        }
    ),
}


class LogisticsBusinessServiceError(RuntimeError):
    """A service failure carrying its stable error class."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class _NotYetSupported(Exception):
    """A physical transition whose authoritative interface is still pending."""

    def __init__(self, event: str):
        self.event = event
        super().__init__(f"physical evidence interface pending: {event}")


class _ArrivalLedgerView:
    """Read-only canonical ledger projection of the live OrderArrivalService.

    ``LogisticsOrdersService`` is reconstructed inside the accepted causal
    arrival service; the provider never instantiates a second order machine. This
    internal view exposes exactly the canonical ``LogisticsLedgerState``
    aggregate (orders, version, processed transition ids, history) from the
    authoritative arrival snapshot so the existing provider receipts, queries,
    snapshots and finalization continue to read one consistent ledger.
    """

    __slots__ = ("_arrival",)

    def __init__(self, arrival: OrderArrivalService) -> None:
        if not isinstance(arrival, OrderArrivalService):
            raise TypeError("_ArrivalLedgerView requires an OrderArrivalService")
        self._arrival = arrival

    @property
    def ledger(self) -> LogisticsLedgerState:
        return self._arrival.snapshot.ledger


def _invalid(detail: str) -> LogisticsBusinessServiceError:
    return LogisticsBusinessServiceError("request.invalid", detail)


def _identity_mismatch(detail: str) -> LogisticsBusinessServiceError:
    return LogisticsBusinessServiceError("identity.mismatch", detail)


def _not_ready(detail: str) -> LogisticsBusinessServiceError:
    return LogisticsBusinessServiceError("not.ready", detail)


def _principal_denied(detail: str) -> LogisticsBusinessServiceError:
    return LogisticsBusinessServiceError("principal.denied", detail)


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
    value: object,
    *,
    label: str,
    pattern: re.Pattern[str] | None = None,
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


def _number(value: object, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{label} must be a finite number")
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise _invalid(f"{label} must be a finite number")
    if positive and numeric <= 0:
        raise _invalid(f"{label} must be positive")
    return numeric


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
        return self.contract.provider.adapter


def _resolve_root_file(
    root: Path, reference: Mapping[str, str], *, label: str
) -> Path:
    relative = PurePosixPath(reference["path"])
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or any(
        parent.is_symlink() for parent in candidate.parents if parent != root.parent
    ):
        raise LogisticsBusinessServiceError(
            "request.invalid",
            f"{label} cannot use a symbolic link: {reference['path']}",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise LogisticsBusinessServiceError(
            "request.invalid", f"{label} is unavailable: {reference['path']}"
        ) from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise LogisticsBusinessServiceError(
            "request.invalid", f"{label} is not a regular file: {reference['path']}"
        )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != reference["sha256"]:
        raise LogisticsBusinessServiceError(
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
        raise LogisticsBusinessServiceError(
            "request.invalid", f"{label} is unavailable: {path}"
        ) from exc
    try:
        value = parse_json_object(raw)
    except (ProviderRpcError, ValueError) as exc:
        raise LogisticsBusinessServiceError(
            "request.invalid", f"{label} is not strict JSON: {path}"
        ) from exc
    if _canonical_json(value) not in (raw, raw.removesuffix(b"\n")):
        raise LogisticsBusinessServiceError(
            "identity.mismatch", f"{label} is not canonical JSON: {path}"
        )
    try:
        model = model_type.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise LogisticsBusinessServiceError(
            "identity.mismatch", f"{label} is not a valid {model_type.__name__}"
        ) from exc
    if _canonical_json(model.model_dump(mode="json")) != _canonical_json(value):
        raise LogisticsBusinessServiceError(
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
    if provider.adapter not in {PROVIDER_ADAPTER, NATIVE_PARCEL_ADAPTER}:
        raise _identity_mismatch(
            "logistics business service requires the logistics.business adapter"
        )
    if provider.port != expected_provider_port:
        raise _identity_mismatch(
            "ProviderWorkloadContract provider port is not the explicit bind port"
        )
    runtime_image = _runtime_image(provider.workload.runtime.image)
    requirements = provider.artifact_requirements
    if len(requirements) != 1:
        raise _identity_mismatch(
            "logistics business provider must declare exactly one formal "
            "evidence ArtifactRequirement"
        )
    requirement = _artifact_requirement(
        requirements[0].model_dump(mode="json"),
        label="logistics business evidence ArtifactRequirement",
    )
    if requirement["artifact_type"] != ARTIFACT_TYPE:
        raise _identity_mismatch(
            f"logistics business ArtifactRequirement must use {ARTIFACT_TYPE!r}"
        )
    if requirement["producer_id"] != provider.provider_id:
        raise _identity_mismatch(
            "logistics business ArtifactRequirement producer_id mismatch"
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


class LogisticsStateWriter:
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
            raise LogisticsBusinessServiceError(
                "request.invalid", "logistics business artifact root is unavailable"
            ) from exc
        if not self.root.is_dir():
            raise _invalid("logistics business artifact root must be a directory")
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise LogisticsBusinessServiceError(
                "artifact.write-failed",
                "logistics business artifact root is not writable",
            )
        self.requirement = _artifact_requirement(
            dict(requirement), label="logistics business evidence ArtifactRequirement"
        )
        self.path = self._resolve_output_path()
        self._reject_undeclared_files()

    def _resolve_output_path(self) -> Path:
        relative_path = self.requirement["relative_path"]
        if not isinstance(relative_path, str):
            raise _invalid(
                "logistics business artifact relative_path must be a string"
            )
        relative = PurePosixPath(relative_path)
        candidate = self.root.joinpath(*relative.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != self.root.parent
        ):
            raise _invalid(
                "logistics business artifact path cannot contain a symbolic link"
            )
        if candidate.exists() and not candidate.is_file():
            raise _invalid("logistics business artifact path must be a regular file")
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
                raise LogisticsBusinessServiceError(
                    "artifact.write-failed",
                    "logistics business artifact root cannot contain symbolic links",
                )
            if candidate.is_dir() and candidate not in allowed_directories:
                raise LogisticsBusinessServiceError(
                    "artifact.write-failed",
                    "logistics business artifact root contains an undeclared "
                    f"directory: {candidate.relative_to(self.root).as_posix()}",
                )
            if candidate.is_file() and candidate != expected:
                raise LogisticsBusinessServiceError(
                    "artifact.write-failed",
                    "logistics business artifact root contains an undeclared "
                    f"file: {candidate.relative_to(self.root).as_posix()}",
                )

    def _create_declared_parents(self) -> None:
        relative_parent = self.path.parent.relative_to(self.root)
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise LogisticsBusinessServiceError(
                        "artifact.write-failed",
                        "logistics business artifact parent path is not a "
                        "regular directory",
                    )
            else:
                current.mkdir()

    def write(self, content: bytes) -> str:
        max_size_bytes = self.requirement["max_size_bytes"]
        if not isinstance(max_size_bytes, int):
            raise _invalid(
                "logistics business artifact max_size_bytes must be an integer"
            )
        if len(content) > max_size_bytes:
            raise LogisticsBusinessServiceError(
                "artifact.write-failed",
                "logistics business artifact exceeds declared max_size_bytes",
            )
        self._reject_undeclared_files()
        self._create_declared_parents()
        if self.path.exists() and self.path.is_symlink():
            raise _invalid(
                "logistics business artifact path cannot be a symbolic link"
            )
        temporary_path: Path | None = None
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".logistics.state.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
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
            raise LogisticsBusinessServiceError(
                "artifact.write-failed",
                "logistics business artifact cannot be atomically written",
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
        self._reject_undeclared_files()
        return hashlib.sha256(content).hexdigest()


class LogisticsBusinessProviderService:
    """Deterministic JSON-line service for the Logistics Business provider.

    Commands carry the Harness authoritative ``SimulationTime`` and never
    advance the clock. Non-physical order transitions are canonical and
    deterministic; physical transitions are explicitly refused. Queries are
    read-only. ``probe`` is token-free and stateless.
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
            raise LogisticsBusinessServiceError(
                "request.invalid", "logistics business bundle root is unavailable"
            ) from exc
        if not self._bundle_root.is_dir():
            raise _invalid("logistics business bundle root must be a directory")
        if not 1024 <= rpc_port <= 65535:
            raise _invalid("logistics business RPC port is outside the user range")
        if workload_identity.provider_port != rpc_port:
            raise _identity_mismatch("workload provider port must equal the RPC port")
        if (
            SHA256_PATTERN.fullmatch(session_token) is None
            or session_token == "0" * 64
        ):
            raise _invalid(
                "logistics business expected session token must be an "
                "executor-issued non-placeholder SHA-256 digest"
            )
        self._rpc_port = rpc_port
        self._workload_identity = workload_identity
        self._writer = LogisticsStateWriter(
            artifact_root, workload_identity.artifact_requirement
        )
        self._config: LogisticsBusinessConfig | None = None
        self._session_token: str = session_token
        self._arrival: OrderArrivalService | None = None
        self._initial_requests: tuple[OrderRequest, ...] = ()
        self._initial_time: SimulationTime = SimulationTime(tick=0, sim_time_ns=0)
        self._current: tuple[int, int] | None = None
        self._scenario_digest: str | None = None
        self._accepted_stage_inputs: list[dict[str, object]] = []
        self._scheduled_applications: list[ScheduledOrderApplication] = []
        #: Immutable observation journal sealed by the declared business state
        #: artifact.  ``None`` until the first reset creates the run's empty
        #: journal; every ingest returns a fresh immutable journal.
        self._observations: ObservationJournal | None = None
        self._native_parcel: NativeParcelRpcComponent | None = None
        self._parcel_scene: SceneState | None = None
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

    @property
    def _machine(self) -> _ArrivalLedgerView | None:
        """Internal read-only ledger view for the canonical aggregate.

        A live ``OrderArrivalService`` owns the ledger; this projection exists
        so every internal receipt/query/snapshot/finalization path reads the
        same canonical ``LogisticsLedgerState`` without constructing a second
        order machine. There is no writable second state to branch on.
        """
        if self._arrival is None:
            return None
        return _ArrivalLedgerView(self._arrival)

    def _probe_response(self) -> dict[str, str]:
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

    def _load_provider_config(self) -> LogisticsBusinessConfig:
        identity = self._workload_identity
        config_path = _resolve_root_file(
            self._bundle_root,
            identity.contract.provider.config.file.model_dump(mode="json"),
            label="provider config file",
        )
        model = (NativeParcelBusinessConfig if identity.adapter == NATIVE_PARCEL_ADAPTER
                 else LogisticsBusinessConfig)
        config = _load_canonical_model_bytes(config_path, model, label="provider config")
        if config.provider_id != identity.provider_id:
            raise _identity_mismatch(
                "provider config provider_id differs from the workload contract"
            )
        return config

    def _parse_prepare(self, payload: Mapping[str, Any]) -> LogisticsBusinessConfig:
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
                    "capabilities",
                }
            ),
            label="logistics business prepare request",
        )
        if (
            _string(raw["provider_id"], label="provider_id", pattern=IDENTIFIER_PATTERN)
            != identity.provider_id
        ):
            raise _identity_mismatch(
                "logistics business provider_id differs from the workload contract"
            )
        if _sha256(raw["run_id"], label="run_id") != identity.run_id:
            raise _identity_mismatch(
                "logistics business run_id differs from the workload contract"
            )
        if _string(raw["protocol_version"], label="protocol_version") != (
            PROTOCOL_VERSION
        ):
            raise _identity_mismatch(
                "logistics business protocol version does not match the service"
            )
        if _runtime_image(raw["runtime_image"]) != identity.runtime_image:
            raise _identity_mismatch(
                "logistics business runtime image differs from the workload contract"
            )
        if _sha256(raw["config_digest"], label="config_digest") != identity.config_digest:
            raise _identity_mismatch(
                "logistics business config digest differs from the workload contract"
            )
        artifact_requirements = raw["artifact_requirements"]
        if not isinstance(artifact_requirements, list) or len(artifact_requirements) != 1:
            raise _identity_mismatch(
                "logistics business artifact requirements are not contract-bound"
            )
        if _canonical_json(artifact_requirements[0]) != _canonical_json(
            identity.artifact_requirement
        ):
            raise _identity_mismatch(
                "logistics business artifact requirement differs from the "
                "workload contract"
            )
        capabilities = raw["capabilities"]
        if not isinstance(capabilities, list):
            raise _identity_mismatch(
                "logistics business capabilities must be a JSON array"
            )
        if tuple(sorted(capabilities)) != tuple(
            sorted(identity.contract.provider.capabilities)
        ):
            raise _identity_mismatch(
                "logistics business capabilities differ from the workload contract"
            )
        declared = frozenset(capabilities)
        supported = (IMPLEMENTED_CAPABILITIES | {NATIVE_PARCEL_CAPABILITY}
                     if identity.adapter == NATIVE_PARCEL_ADAPTER else IMPLEMENTED_CAPABILITIES)
        if not declared <= supported:
            claimed = sorted(declared - supported)
            raise _identity_mismatch(
                "logistics business claims capabilities whose physical interface "
                f"is not yet implemented: {claimed}"
            )
        config = self._load_provider_config()
        if isinstance(config, NativeParcelBusinessConfig):
            if NATIVE_PARCEL_CAPABILITY not in declared:
                raise _identity_mismatch("native parcel authority was not declared")
            if (config.native_parcel.step_ns != identity.contract.clock.step_ns
                    or config.native_parcel.max_steps != identity.contract.clock.max_steps):
                raise _identity_mismatch("native parcel clock differs from immutable workload")
        if config.scheduled_orders and SCHEDULED_ARRIVAL_CAPABILITY not in declared:
            raise _identity_mismatch("scheduled orders lack the declared scheduled-arrivals capability")
        for item in config.scheduled_orders:
            if item.at_tick > identity.contract.clock.max_steps or item.order.release_time_s * 1_000_000_000 < item.at_tick * identity.contract.clock.step_ns:
                raise _identity_mismatch("scheduled order time disagrees with the pinned clock")
        task_package = config.task_package.model_dump(mode="json")
        if (
            _string(raw["task_id"], label="task_id", pattern=IDENTIFIER_PATTERN)
            != config.task_package.task_id
        ):
            raise _identity_mismatch(
                "logistics business task_id differs from the pinned task package"
            )
        if _sha256(raw["package_digest"], label="package_digest") != hashlib.sha256(
            _canonical_json(task_package)
        ).hexdigest():
            raise _identity_mismatch(
                "logistics business package digest differs from the pinned "
                "task package"
            )
        if _canonical_json(raw["task_package"]) != _canonical_json(task_package):
            raise _identity_mismatch(
                "logistics business task package differs from the pinned "
                "provider config"
            )
        return config

    def _require_prepared(self) -> LogisticsBusinessConfig:
        if self._config is None:
            raise _not_ready("logistics business provider has not completed prepare")
        return self._config

    def _require_open(self) -> None:
        if self._closed:
            raise _not_ready("logistics business provider service is closed")

    def _require_session_token(self, payload: Mapping[str, Any]) -> None:
        presented = payload.get("session_token") if isinstance(payload, dict) else None
        if (
            not isinstance(presented, str)
            or SHA256_PATTERN.fullmatch(presented) is None
            or not hmac.compare_digest(presented, self._session_token)
        ):
            raise _principal_denied(
                "logistics business request cannot present the "
                "executor-issued run-scoped session token"
            )

    def _require_identity(self, payload: Mapping[str, Any]) -> None:
        if "provider_id" not in payload or "run_id" not in payload:
            raise _invalid(
                "logistics business request must identify provider and run"
            )
        if payload["provider_id"] != self._workload_identity.provider_id:
            raise _identity_mismatch(
                "logistics business request provider identity mismatch"
            )
        if payload["run_id"] != self._workload_identity.run_id:
            raise _identity_mismatch("logistics business request run identity mismatch")

    def _require_not_finalized(self) -> None:
        if self._finalization_receipt is not None:
            raise _not_ready("logistics business provider is already finalized")

    def _require_arrival(self) -> OrderArrivalService:
        arrival = self._arrival
        if arrival is None or self._current is None:
            raise _not_ready(
                "logistics business provider must be reset before this operation"
            )
        return arrival

    def _require_command_time(self, issued_at: Mapping[str, int]) -> None:
        if self._current is None:
            raise _not_ready(
                "logistics business provider must be reset before commands"
            )
        if not isinstance(issued_at, dict):
            raise _invalid(
                "logistics business command time must be a simulation-time object"
            )
        tick = issued_at.get("tick")
        sim_time_ns = issued_at.get("sim_time_ns")
        if (
            isinstance(tick, bool)
            or not isinstance(tick, int)
            or isinstance(sim_time_ns, bool)
            or not isinstance(sim_time_ns, int)
        ):
            raise _invalid(
                "logistics business command time must carry integer tick and "
                "sim_time_ns"
            )
        current_tick, current_sim_time_ns = self._current
        if tick != current_tick or sim_time_ns != current_sim_time_ns:
            raise _invalid(
                "logistics business command time must equal the current "
                f"provider barrier ({current_tick}, {current_sim_time_ns}); "
                f"got ({tick}, {sim_time_ns})"
            )

    def _binding_for(
        self, principal_id: str, actor_id: str
    ) -> LogisticsInfrastructurePrincipal:
        config = self._require_prepared()
        binding = next(
            (
                item
                for item in config.principal_bindings
                if item.principal_id == principal_id and item.actor_id == actor_id
            ),
            None,
        )
        if binding is None:
            raise _principal_denied(
                f"principal {principal_id!r} is not bound to logistics "
                f"actor {actor_id!r}"
            )
        return binding

    def _canonical_role_for(self, actor_id: str) -> str:
        config = self._require_prepared()
        role = next(
            (
                grant.role
                for grant in config.task_package.actor_grants
                if grant.actor_id == actor_id
            ),
            None,
        )
        if role is None:
            raise _principal_denied(
                f"logistics actor {actor_id!r} has no canonical OrderActorGrant"
            )
        return role

    def _aircraft_unit_for(self, aircraft_id: str):
        config = self._require_prepared()
        unit = next(
            (
                candidate
                for candidate in config.task_package.fleet.expand_aircraft_units()
                if candidate.aircraft_id == aircraft_id
            ),
            None,
        )
        if unit is None:
            raise _invalid(
                "assignment target is not a declared aircraft unit: "
                f"{aircraft_id}"
            )
        return unit

    def _command_from_request(self, request: CommandRequest) -> OrderTransition:
        kind = TOOL_KINDS.get(request.tool_id)
        if kind is None:
            raise _invalid(f"unsupported logistics tool {request.tool_id!r}")
        if kind in PHYSICAL_PENDING_EVENTS:
            raise _NotYetSupported(kind)
        arguments: dict[str, Any] = {}
        for argument in request.arguments:
            if argument.name in arguments:
                raise _invalid(f"duplicate command argument {argument.name!r}")
            arguments[argument.name] = argument.value
        required = REQUIRED_ARGUMENTS[kind]
        if set(arguments) != required:
            raise _invalid(f"{kind} requires exactly {sorted(required)} arguments")

        actor_id = _string(
            arguments["actor_id"],
            label="actor_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        binding = self._binding_for(request.agent_id, actor_id)
        actor_role = self._canonical_role_for(binding.actor_id)
        order_id = _string(
            arguments["order_id"],
            label="order_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        expected_version = _integer(
            arguments["expected_version"], label="expected_version", minimum=0
        )
        time_s = request.issued_at.sim_time_ns / 1_000_000_000.0

        transition: dict[str, object] = {
            "order_id": order_id,
            "transition_id": request.command_id,
            "event": kind,
            "actor_id": binding.actor_id,
            "actor_role": actor_role,
            "time_s": time_s,
            "expected_version": expected_version,
        }
        if kind in {"offer", "assign"}:
            assignee_id = _string(
                arguments["assignee_id"],
                label="assignee_id",
                pattern=LOGISTICS_IDENTIFIER_PATTERN,
            )
            # The assignee must be an actual expanded package AircraftUnit; an
            # undeclared aircraft can never be offered/assigned.
            self._aircraft_unit_for(assignee_id)
            transition["assignee_id"] = assignee_id
        elif kind in {"accept", "reject"}:
            transition["assignee_id"] = binding.actor_id
        elif kind == "fail":
            state = self._current_order_state(order_id)
            transition["assignee_id"] = state.assignee_id
        if kind == "assign":
            capacity_kg = _number(
                arguments["capacity_kg"], label="capacity_kg", positive=True
            )
            aircraft = self._aircraft_unit_for(
                _string(
                    arguments["assignee_id"],
                    label="assignee_id",
                    pattern=LOGISTICS_IDENTIFIER_PATTERN,
                )
            )
            if capacity_kg > aircraft.max_payload_kg:
                raise _invalid(
                    "capacity_kg cannot exceed the assigned aircraft's canonical "
                    f"max_payload_kg: {aircraft.aircraft_id} "
                    f"allows {aircraft.max_payload_kg}, requested {capacity_kg}"
                )
            transition["capacity_kg"] = capacity_kg
        if kind in {"reject", "cancel", "fail"}:
            transition["reason"] = _string(
                arguments["reason"],
                label="reason",
                pattern=LOGISTICS_IDENTIFIER_PATTERN,
            )
        try:
            return OrderTransition.model_validate(transition)
        except (TypeError, ValueError) as exc:
            message = str(exc)
            if isinstance(exc, ValueError) and getattr(exc, "errors", None):
                try:
                    first_message = exc.errors()[0].get("msg", message)
                    if "Value error, " in first_message:
                        first_message = first_message.split("Value error, ", 1)[1]
                    message = first_message
                except (IndexError, AttributeError, LookupError, TypeError):
                    pass
            raise _invalid(message) from exc

    def _create_command_from_request(
        self, request: CommandRequest
    ) -> OrderArrivalCommand:
        """Build the authenticated online-order creation command.

        The actor identity is resolved through the explicit principal binding
        (``agent_id`` attested by the Gateway + canonical ``actor_id``) and the
        actor role is the canonical ``OrderActorGrant`` role — never a
        caller-declared role. ``source_identity`` is bound to the attested
        native principal so a client can never forge a different introducing
        source; the accepted causal arrival layer enforces the business-only
        introduction authority and the exact current native clock.
        """
        self._require_arrival()
        arguments: dict[str, Any] = {}
        for argument in request.arguments:
            if argument.name in arguments:
                raise _invalid(f"duplicate command argument {argument.name!r}")
            arguments[argument.name] = argument.value
        required = REQUIRED_ARGUMENTS["create"]
        if set(arguments) != required:
            raise _invalid(f"create requires exactly {sorted(required)} arguments")

        actor_id = _string(
            arguments["actor_id"],
            label="actor_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        binding = self._binding_for(request.agent_id, actor_id)
        actor_role = self._canonical_role_for(binding.actor_id)
        order_id = _string(
            arguments["order_id"],
            label="order_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        origin_facility_id = _string(
            arguments["origin_facility_id"],
            label="origin_facility_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        destination_facility_id = _string(
            arguments["destination_facility_id"],
            label="destination_facility_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        hub_handoff_facility_id = _string(
            arguments["hub_handoff_facility_id"],
            label="hub_handoff_facility_id",
            pattern=LOGISTICS_IDENTIFIER_PATTERN,
        )
        cargo_mass_kg = _number(
            arguments["cargo_mass_kg"], label="cargo_mass_kg", positive=True
        )
        release_time_s = _number(
            arguments["release_time_s"], label="release_time_s"
        )
        if release_time_s < 0:
            raise _invalid("release_time_s must be nonnegative")
        deadline_s = _number(arguments["deadline_s"], label="deadline_s", positive=True)
        try:
            order = OrderRequest.model_validate(
                {
                    "order_id": order_id,
                    "origin_facility_id": origin_facility_id,
                    "destination_facility_id": destination_facility_id,
                    "hub_handoff_facility_id": hub_handoff_facility_id,
                    "cargo_mass_kg": cargo_mass_kg,
                    "release_time_s": release_time_s,
                    "deadline_s": deadline_s,
                }
            )
        except (TypeError, ValueError) as exc:
            raise _invalid(f"online order request is invalid: {exc}") from exc
        assert self._current is not None
        tick, sim_time_ns = self._current
        return OrderArrivalCommand(
            command_id=request.command_id,
            actor_id=binding.actor_id,
            actor_role=actor_role,
            time=SimulationTime(tick=tick, sim_time_ns=sim_time_ns),
            source_identity=request.agent_id,
            order=order,
        )

    def _current_order_state(self, order_id: str) -> OrderState:
        arrival = self._require_arrival()
        state = next(
            (
                order
                for order in arrival.snapshot.ledger.orders
                if order.order_id == order_id
            ),
            None,
        )
        if state is None:
            raise ValueError(f"unknown logistics order: {order_id}")
        return state

    def _handle_prepare(self, payload: Mapping[str, Any]) -> dict[str, object]:
        config = self._parse_prepare(payload)
        if self._config is not None:
            raise _not_ready("logistics business provider prepare called twice")
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
            label="logistics business reset request",
        )
        if _integer(raw["seed"], label="seed") != self._workload_identity.seed:
            raise _identity_mismatch(
                "reset seed does not match the explicit provider workload contract"
            )
        # The accepted causal arrival service owns the order pool, the native
        # clock and the release gates. Baseline package orders are bound exactly
        # at the reset instant (0, 0) for later replay.
        initial_time = SimulationTime(tick=0, sim_time_ns=0)
        self._arrival = OrderArrivalService(
            catalogue=config.task_package.facilities,
            fleet=config.task_package.fleet,
            actor_grants=config.task_package.actor_grants,
            initial_requests=config.task_package.orders,
            initial_time=initial_time,
        )
        self._current = (0, 0)
        self._initial_requests = config.task_package.orders
        self._initial_time = initial_time
        self._accepted_stage_inputs = []
        self._scheduled_applications = []
        self._observations = empty_observation_journal(
            run_id=self._workload_identity.run_id,
            provider_id=self._workload_identity.provider_id,
        )
        self._native_parcel = (
            NativeParcelRpcComponent(config=config.native_parcel,
                run_id=self._workload_identity.run_id,
                scenario_digest=self._workload_identity.contract.scenario_digest,
                business_provider_id=self._workload_identity.provider_id)
            if isinstance(config, NativeParcelBusinessConfig) else None
        )
        self._parcel_scene = None
        self._finalization_receipt = None
        return {"receipt": self._receipt()}

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
                "logistics business cannot construct its empty SceneContribution"
            ) from exc

    def _parse_stage_request(
        self, raw_request: object
    ) -> tuple[BusinessEnvironmentStageRequest, SceneState, str]:
        if not isinstance(raw_request, dict):
            raise _invalid("logistics business staged request must be a JSON object")
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
                "logistics business BusinessEnvironmentStageRequest is invalid"
            ) from exc
        if (
            canonical_request != request
            or canonical_scene_state != request.scene_state
            or _canonical_json(raw_request)
            != _canonical_json(request.model_dump(mode="json"))
        ):
            raise _invalid(
                "logistics business staged request is not canonically serialized"
            )
        computed_scene_state_digest = scene_state_digest_value(canonical_scene_state)
        if (
            request.scene_state_digest != computed_scene_state_digest
            or canonical_scene_state.scene_state_digest != computed_scene_state_digest
        ):
            raise _invalid(
                "logistics business staged SceneState digest does not match content"
            )
        predecessor_stages = tuple(
            predecessor.stage for predecessor in request.predecessor_barriers
        )
        if predecessor_stages not in {("motion",), ("motion", "network")}:
            raise _invalid(
                "logistics business staged predecessors must be motion or motion "
                "then network"
            )
        motion_barrier = request.predecessor_barriers[0]
        if (
            motion_barrier.stage != "motion"
            or motion_barrier.barrier_digest
            != canonical_scene_state.stage_barrier.barrier_digest
        ):
            raise _invalid(
                "logistics business staged motion predecessor does not bind the "
                "SceneState"
            )
        return canonical_request, canonical_scene_state, computed_scene_state_digest

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
            label="logistics business step_stage request",
        )
        if _string(raw["protocol_version"], label="protocol_version") != (
            PROTOCOL_VERSION
        ):
            raise _identity_mismatch(
                "logistics business staged protocol version does not match the service"
            )
        request, _scene_state, computed_scene_state_digest = self._parse_stage_request(
            raw["request"]
        )
        identity = self._workload_identity
        if request.run_id != identity.run_id:
            raise _identity_mismatch(
                "logistics business staged request run identity mismatch"
            )
        if request.provider_id != identity.provider_id:
            raise _identity_mismatch(
                "logistics business staged request provider identity mismatch"
            )
        if request.scenario_digest != identity.contract.scenario_digest:
            raise _identity_mismatch(
                "logistics business staged request scenario digest differs from "
                "the workload contract"
            )
        if (
            self._scenario_digest is not None
            and request.scenario_digest != self._scenario_digest
        ):
            raise _identity_mismatch(
                "logistics business staged request scenario digest changed mid-session"
            )
        self._require_arrival()
        assert self._current is not None
        target = (request.target.tick, request.target.sim_time_ns)
        if target[0] <= self._current[0] or target[1] <= self._current[1]:
            raise _invalid(
                "logistics business staged target must advance monotonically"
            )
        config = self._require_prepared()
        if config.scheduled_orders and (target[0] > identity.contract.clock.max_steps or target[1] != target[0] * identity.contract.clock.step_ns):
            raise _invalid("scheduled Business stage target differs from the pinned clock")
        applied_ids = {item.requested.event_id for item in self._scheduled_applications}
        if any(item.at_tick < target[0] and item.event_id not in applied_ids for item in config.scheduled_orders):
            raise _invalid("Business stage skipped a scheduled order creation tick")

        # Advance the authoritative native clock on the real stage boundary;
        # only then is a predeclared or online order released at its gate.
        arrival = self._require_arrival()
        try:
            arrival.advance_time(request.target)
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "logistics business cannot advance its arrival clock: "
                f"{str(exc)}"
            ) from exc
        self._current = target
        self._parcel_scene = _scene_state
        for scheduled in config.scheduled_orders:
            if scheduled.at_tick != target[0]:
                continue
            if scheduled.event_id in applied_ids:
                raise _invalid("scheduled order creation was already applied")
            try:
                created = arrival.submit(OrderArrivalCommand(
                    command_id=scheduled.event_id,
                    actor_id=scheduled.actor_id,
                    actor_role="business",
                    time=request.target,
                    source_identity=f"provider:{identity.provider_id}:scheduled",
                    order=scheduled.order,
                ))
            except (TypeError, ValueError) as exc:
                raise _invalid(f"scheduled order creation rejected: {exc}") from exc
            self._scheduled_applications.append(ScheduledOrderApplication(
                schema_version="aero-bench.logistics-scheduled-application/v1",
                requested=scheduled,
                applied_at=request.target,
                arrival=created,
                arrival_snapshot_digest=arrival.snapshot.canonical_digest(),
                motion_barrier_digest=request.predecessor_barriers[0].barrier_digest,
                scene_state_digest=computed_scene_state_digest,
            ))

        if self._scenario_digest is None:
            self._scenario_digest = request.scenario_digest
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
                "logistics business cannot construct its staged StepReceipt"
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
                "logistics business cannot construct its staged result"
            ) from exc
        return {"result": result.model_dump(mode="json")}

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

    def _validate_batch_against_declared_observations(
        self,
        batch: LogisticsObservationBatch,
        config: LogisticsBusinessConfig,
    ) -> None:
        """Bind every observation to the declared digest-bound configuration.

        The provider config's ``observation`` branch (pinned in the config
        digest / RunID file inputs) is the *source declaration* for the native
        provider/vehicle + fleet/asset identity, the pose-reference calibration,
        the presence tolerances and the observation plan.  A payload that
        disagrees with any one of those declarations is rejected here with zero
        journal mutation, even when every content hash was recomputed over the
        forged content.  ``observation`` is ``None`` only for an explicitly
        disabled branch, which rejects every ingest.
        """
        observation_config = config.observation
        if observation_config is None:
            raise _invalid(
                "logistics business observation ingress is disabled by the "
                "declared provider config (observation=null); no observation "
                "batch may be journaled for this run"
            )
        declared_bindings = {
            binding.aircraft_id: binding
            for binding in observation_config.bindings.aircraft
        }
        declared_pose_references = {
            reference.aircraft_id: reference
            for reference in observation_config.pose_references
        }
        declared_pairs = set(observation_config.spec.observation_pairs)
        for observation in batch.observations:
            binding = declared_bindings.get(observation.aircraft_id)
            if binding is None:
                raise ObservationIngressError(
                    f"observation names authored aircraft "
                    f"{observation.aircraft_id!r} that the declared observation "
                    "configuration does not bind to a native provider/vehicle"
                )
            for field, actual, expected in (
                ("provider", observation.provider_id, binding.provider_id),
                (
                    "native_vehicle",
                    observation.native_vehicle_id,
                    binding.vehicle_id,
                ),
                ("fleet_entry", observation.fleet_entry_id, binding.fleet_entry_id),
                ("visual_asset", observation.visual_asset_id, binding.visual_asset_id),
            ):
                if actual != expected:
                    raise ObservationIngressError(
                        f"observation {observation.aircraft_id!r} {field} "
                        f"identity {actual!r} differs from the declared native "
                        f"binding {expected!r}"
                    )
            pair = (
                observation.aircraft_id,
                observation.facility_id,
                observation.pad_index,
            )
            if pair not in declared_pairs:
                raise ObservationIngressError(
                    f"observation plan declares no (aircraft, facility, pad) "
                    f"item {pair!r}; the observation is not part of the "
                    "declared plan"
                )
            if observation.profile.aircraft_id != binding.vehicle_id:
                raise ObservationIngressError(
                    f"observation {observation.aircraft_id!r} presence profile "
                    f"names native vehicle {observation.profile.aircraft_id!r} "
                    f"but the declared native binding names "
                    f"{binding.vehicle_id!r}"
                )
            pose_reference = declared_pose_references.get(observation.aircraft_id)
            if pose_reference is None:
                raise ObservationIngressError(
                    f"observation {observation.aircraft_id!r} has no declared "
                    "pose-reference calibration in the observation configuration"
                )
            if not math.isclose(
                observation.profile.pose_reference_above_contact_m,
                pose_reference.pose_reference_above_contact_m,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ObservationIngressError(
                    f"observation {observation.aircraft_id!r} presence profile "
                    "calibration differs from the declared pose-reference "
                    "calibration (calibrations are never guessed from body "
                    "height)"
                )
            performance = next(
                (
                    profile
                    for profile in config.task_package.performance_profiles
                    if profile.fleet_entry_id == binding.fleet_entry_id
                ),
                None,
            )
            if performance is None:
                raise ObservationIngressError(
                    f"observation {observation.aircraft_id!r} fleet entry "
                    f"{binding.fleet_entry_id!r} has no declared performance "
                    "profile in the canonical task package"
                )
            declared_body = performance.aircraft_body
            for field, actual, expected in (
                (
                    "body_width_m",
                    observation.profile.body_width_m,
                    declared_body.x_m,
                ),
                (
                    "body_depth_m",
                    observation.profile.body_depth_m,
                    declared_body.z_m,
                ),
            ):
                if not math.isclose(
                    actual, expected, rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ObservationIngressError(
                        f"observation {observation.aircraft_id!r} presence "
                        f"profile {field} {actual!r} differs from the declared "
                        f"package profile {expected!r} (body dimensions are "
                        "declared package dimensions, never inferred from "
                        "assets or accepted as arbitrary positive values)"
                    )
            recomputed = assess_facility_presence(
                pad=observation.pad,
                sample=observation.sample,
                profile=observation.profile,
                event=observation.event_binding,
                tolerances=observation_config.tolerances,
            )
            if recomputed != observation.assessment:
                raise ObservationIngressError(
                    f"observation {observation.aircraft_id!r} assessment is not "
                    "reproducible under the declared presence tolerances "
                    "(mismatched tolerances or forged assessment)"
                )

    def _handle_observation_ingest(
        self, payload: Mapping[str, Any]
    ) -> dict[str, object]:
        """Private authenticated ingest of typed logistics physical observations.

        This is the only path that grows the observation journal.  The payload
        must present the Harness session token, the exact workload identity and
        protocol version, and a fully typed, canonical
        :class:`LogisticsObservationBatch` whose records were derived from the
        actual closed motion stage under explicit pose-reference calibrations.

        The batch must be bound to the current authoritative provider barrier
        (``batch.at`` == the current (tick, sim_time_ns)), to the run's scenario
        digest, and to *declared* package facilities/pads/aircraft.  Replay,
        deduplication and reject-conflict are atomic via
        :func:`append_observations_batch`; a foreign, stale, mismatched-config,
        conflicting or duplicate payload leaves the journal untouched and an
        error is returned.
        """
        self._require_identity(payload)
        self._require_not_finalized()
        raw = _strict_object(
            payload,
            required=frozenset(
                {
                    "provider_id",
                    "run_id",
                    "session_token",
                    "protocol_version",
                    "batch",
                }
            ),
            label="logistics business observation ingest request",
        )
        if _string(raw["protocol_version"], label="protocol_version") != (
            PROTOCOL_VERSION
        ):
            raise _identity_mismatch(
                "logistics business observation ingest protocol version mismatch"
            )
        try:
            batch = LogisticsObservationBatch.model_validate(raw["batch"])
            canonical = LogisticsObservationBatch.model_validate(
                batch.model_dump(mode="json")
            )
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "logistics business observation batch is invalid"
            ) from exc
        if canonical != batch or _canonical_json(raw["batch"]) != _canonical_json(
            batch.model_dump(mode="json")
        ):
            raise _invalid(
                "logistics business observation batch is not canonically serialized"
            )
        identity = self._workload_identity
        if batch.run_id != identity.run_id:
            raise _identity_mismatch(
                "logistics business observation batch belongs to another run"
            )
        if batch.scenario_digest != identity.contract.scenario_digest:
            raise _identity_mismatch(
                "logistics business observation batch scenario digest differs "
                "from the workload contract"
            )
        if (
            self._scenario_digest is not None
            and batch.scenario_digest != self._scenario_digest
        ):
            raise _identity_mismatch(
                "logistics business observation scenario digest changed mid-session"
            )
        self._require_arrival()
        assert self._current is not None
        current = self._current
        if (batch.at.tick, batch.at.sim_time_ns) != current:
            raise _invalid(
                "logistics business observation batch time must equal the current "
                f"provider barrier ({current[0]}, {current[1]}); got "
                f"({batch.at.tick}, {batch.at.sim_time_ns})"
            )
        config = self._require_prepared()
        try:
            validate_batch_against_package(batch, package=config.task_package)
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "logistics business observation batch fails declared package "
                f"binding: {exc}"
            ) from exc
        try:
            self._validate_batch_against_declared_observations(batch, config)
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "logistics business observation batch fails the declared "
                f"digest-bound observation configuration: {exc}"
            ) from exc

        try:
            outcome = append_observations_batch(
                journal=self._observations,
                batch=batch,
                provider_id=identity.provider_id,
            )
        except (TypeError, ObservationIngressError, ValueError) as exc:
            raise _invalid(
                "logistics business observation batch is rejected atomically: "
                f"{exc}"
            ) from exc
        # Commit both the immutable journal and parcel stage only after all
        # source validation; duplicate ingest replays neither physical evidence
        # nor custody. The component validates the entire stage atomically.
        if self._native_parcel is not None and not outcome.replayed:
            try:
                self._native_parcel.ingest_closed_stage(batch)
            except ValueError as exc:
                raise _invalid(f"native parcel closed-stage batch rejected: {exc}") from exc
        # Only a fully validated append/replay mutates the journal.
        self._observations = outcome.journal
        result = observation_ingest_result(
            outcome=outcome,
            provider_id=identity.provider_id,
        )
        return {"result": result.model_dump(mode="json")}

    def _handle_command(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_not_finalized()
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "request"}),
            label="logistics business command request",
        )
        try:
            request = CommandRequest.model_validate(raw["request"])
        except (TypeError, ValueError) as exc:
            raise _invalid("logistics business command request is invalid") from exc
        if request.run_id != self._workload_identity.run_id:
            raise _identity_mismatch(
                "logistics business command request belongs to another run"
            )
        if request.tool_id in {PARCEL_PICKUP_TOOL, PARCEL_DROPOFF_TOOL}:
            return self._handle_parcel_command(request)
        try:
            self._require_command_time(request.issued_at.model_dump(mode="json"))
        except LogisticsBusinessServiceError as exc:
            if exc.code in {"principal.denied", "not.ready"}:
                raise
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), exc.detail
                )
            }
        if request.tool_id == CREATE_TOOL_ID:
            return self._handle_create_command(request)

        try:
            transition = self._command_from_request(request)
        except _NotYetSupported as exc:
            return {
                "receipts": self._command_receipts(
                    request,
                    ("received", "failed"),
                    (
                        "physical evidence interface pending: "
                        f"{exc.event} requires the later authoritative "
                        "physical-evidence binding"
                    ),
                )
            }
        except LogisticsBusinessServiceError as exc:
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
        arrival = self._require_arrival()
        try:
            result = arrival.apply_lifecycle(transition)
        except ValueError as exc:
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), str(exc)
                )
            }
        return {
            "receipts": self._command_receipts(
                request,
                ("received", "accepted", "applied", "completed"),
                f"applied:{transition.event}",
            ),
            "history_record": result.history_record.model_dump(mode="json"),
        }

    def _handle_parcel_command(self, request: CommandRequest) -> dict[str, object]:
        if self._native_parcel is None or self._current is None:
            raise _not_ready("native parcel runtime is not explicitly configured")
        ids = self._native_parcel.config.contract.identities
        binding = self._binding_for(request.agent_id, ids.carrier_entity_id)
        if binding.role != "aircraft_agent":
            raise _principal_denied("parcel transition principal has no carrier authority")
        now = SimulationTime(tick=self._current[0], sim_time_ns=self._current[1])
        try:
            record = self._native_parcel.transition(request, now=now)
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
        status = record["outcome"]["status"]
        phases = (("received", "accepted", "applied", "completed")
                  if status == "admitted" else ("received", "failed"))
        return {"receipts":self._command_receipts(request, phases, f"parcel:{status}"),
                "parcel_action_record":record}

    def _handle_parcel_snapshot(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_not_finalized()
        raw = _strict_object(payload, required=frozenset({"provider_id", "run_id",
            "session_token", "at"}), label="native parcel snapshot request")
        self._require_command_time(raw["at"])
        if self._native_parcel is None or self._parcel_scene is None:
            raise _not_ready("native parcel has no current closed physical source")
        frame = NativeParcelSceneFrame(
            schema_version="aero-bench.native-parcel-scene-frame/v1",
            scene_state=self._parcel_scene, parcel=self._native_parcel.projection())
        return {"frame":frame.model_dump(mode="json")}

    def _handle_create_command(self, request: CommandRequest) -> dict[str, object]:
        """Authenticated online-order creation through the accepted arrival layer.

        All validation (exact clock, release gate, reserved namespaces,
        introduction authority, canonical hub routing, declared payload
        capacity) happens *before* the arrival journal mutates, so a rejected
        create leaves the ledger and the journal untouched.
        """
        try:
            command = self._create_command_from_request(request)
            arrival = self._require_arrival()
            event = arrival.submit(command)
        except LogisticsBusinessServiceError as exc:
            if exc.code in {"principal.denied", "not.ready"}:
                raise
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), exc.detail
                )
            }
        except (TypeError, ValueError) as exc:
            return {
                "receipts": self._command_receipts(
                    request, ("received", "failed"), str(exc)
                )
            }
        return {
            "receipts": self._command_receipts(
                request,
                ("received", "accepted", "applied", "completed"),
                "applied:create",
            ),
            "arrival_record": event.model_dump(mode="json"),
        }

    def _handle_query(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "query"}),
            label="logistics business query request",
        )
        try:
            query = LogisticsBusinessQuery.model_validate(raw["query"])
        except (TypeError, ValueError) as exc:
            raise _invalid("logistics business query is invalid") from exc
        if query.run_id != self._workload_identity.run_id:
            raise _identity_mismatch("logistics business query belongs to another run")
        arrival = self._require_arrival()
        self._require_command_time(query.issued_at.model_dump(mode="json"))
        try:
            if query.kind == "order":
                if not any(
                    order.order_id == query.order_id
                    for order in arrival.snapshot.ledger.orders
                ):
                    raise ValueError(
                        f"unknown logistics order: {query.order_id}"
                    )
                if query.order_id not in arrival.released_order_ids():
                    raise ValueError(
                        "logistics order is not yet released: "
                        f"{query.order_id}"
                    )
                state = next(
                    order
                    for order in arrival.available_orders()
                    if order.order_id == query.order_id
                )
                result = LogisticsBusinessQueryResult(
                    run_id=query.run_id,
                    query_id=query.query_id,
                    observed_at=query.issued_at,
                    orders=(order_view(state),),
                )
            elif query.kind == "orders":
                result = LogisticsBusinessQueryResult(
                    run_id=query.run_id,
                    query_id=query.query_id,
                    observed_at=query.issued_at,
                    orders=tuple(
                        order_view(order) for order in arrival.available_orders()
                    ),
                )
            else:
                config = self._require_prepared()
                result = LogisticsBusinessQueryResult(
                    run_id=query.run_id,
                    query_id=query.query_id,
                    observed_at=query.issued_at,
                    facilities=tuple(
                        self._facility_view(item) for item in config.task_package.facilities.facilities
                    ),
                )
        except ValueError as exc:
            raise LogisticsBusinessServiceError("query.rejected", str(exc)) from exc
        return {"result": result.model_dump(mode="json")}

    @staticmethod
    def _facility_view(facility: Any) -> LogisticsFacilityView:
        return LogisticsFacilityView(
            facility_id=facility.facility_id,
            name=facility.name,
            kind=facility.kind,
            placement=facility.placement,
            building_id=facility.building_id,
            position_x=facility.position.x,
            position_z=facility.position.z,
            parking_slots=facility.landing.parking_slots if facility.landing else None,
            movements_per_hour=(
                facility.landing.movements_per_hour if facility.landing else None
            ),
            storage_capacity_kg=facility.cargo.storage_capacity_kg if facility.cargo else None,
            charging_slots=facility.charging.slots if facility.charging else None,
            charging_power_w=facility.charging.power_w if facility.charging else None,
            charging_price_amount=facility.charging.price_amount if facility.charging else None,
            charging_price_currency=(
                facility.charging.price_currency if facility.charging else None
            ),
        )

    def _handle_snapshot(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        self._require_arrival()
        return {"snapshot_digest": self._state_digest()}

    def _handle_finalize(self, payload: Mapping[str, Any]) -> dict[str, object]:
        self._require_identity(payload)
        raw = _strict_object(
            payload,
            required=frozenset({"provider_id", "run_id", "session_token", "request"}),
            label="logistics business finalize request",
        )
        try:
            request = ProviderFinalizationRequest.model_validate(raw["request"])
        except (TypeError, ValueError) as exc:
            raise _invalid(
                "logistics business ProviderFinalizationRequest is invalid"
            ) from exc
        if request.run_id != self._workload_identity.run_id:
            raise _identity_mismatch(
                "logistics business finalization belongs to another run"
            )
        if self._finalization_receipt is not None:
            if self._finalization_receipt["event_chain_root"] != request.event_chain_root:
                raise _not_ready(
                    "logistics business finalization root cannot be changed"
                )
            return {"receipt": dict(self._finalization_receipt)}

        self._require_arrival()
        assert self._current is not None
        if (
            request.terminal_time.tick,
            request.terminal_time.sim_time_ns,
        ) != self._current:
            raise _identity_mismatch(
                "logistics business finalization time differs from provider time"
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

    def _artifact_content(self, *, event_chain_root: str) -> bytes:
        arrival = self._require_arrival()
        config = self._require_prepared()
        snapshot = arrival.snapshot
        ledger = snapshot.ledger
        history = ledger.history
        history_root = history[-1].record_hash if history else "0" * 64
        requirement = self._workload_identity.artifact_requirement
        artifact = {
            "schema_version": ARTIFACT_SCHEMA,
            "source_artifact_id": requirement["artifact_id"],
            "event_chain_root": event_chain_root,
            "logistics_history_root": history_root,
            "history": [record.model_dump(mode="json") for record in history],
            "ledger": ledger.model_dump(mode="json"),
            # The full append-only causal arrival journal (creation + one-time
            # release + lifecycle markers) and the canonical snapshot, including
            # the exact baseline binding (initial_requests / initial_time) a
            # later replay_order_arrivals needs to reconstruct this run.
            "arrival": [event.model_dump(mode="json") for event in snapshot.events],
            "arrival_snapshot": snapshot.model_dump(mode="json"),
            "arrival_snapshot_digest": snapshot.canonical_digest(),
            "initial_requests": [
                request.model_dump(mode="json") for request in self._initial_requests
            ],
            "initial_time": self._initial_time.model_dump(mode="json"),
            # Immutable sealed observation journal (records carry the full
            # source/barrier/evidence refs and digests) plus its canonical
            # digest.  A journal that is parsed from this artifact must
            # reconstruct the identical records and digest.
            "observation_journal": (
                None
                if self._observations is None
                else self._observations.model_dump(mode="json")
            ),
            "observation_journal_digest": (
                None
                if self._observations is None
                else self._observations.canonical_digest()
            ),
            "facilities": config.task_package.facilities.model_dump(mode="json"),
            "principal_bindings": [
                binding.model_dump(mode="json")
                for binding in config.principal_bindings
            ],
        }
        if config.scheduled_orders:
            artifact["scheduled_evidence"] = {
                "run_id": self._workload_identity.run_id,
                "provider_id": self._workload_identity.provider_id,
                "runtime_image": self._workload_identity.runtime_image,
                "config_digest": self._workload_identity.config_digest,
                "seed": self._workload_identity.seed,
                "scenario_digest": self._scenario_digest,
                "current_time": snapshot.time.model_dump(mode="json"),
                "requested": [item.model_dump(mode="json") for item in config.scheduled_orders],
                "applied": [item.model_dump(mode="json") for item in self._scheduled_applications],
                "accepted_stage_inputs": self._accepted_stage_inputs,
                "final_state_digest": self._state_digest(),
            }
        if self._native_parcel is not None:
            artifact["native_parcel"] = self._native_parcel.snapshot()
        return _canonical_json(artifact)

    def _state_digest(self) -> str:
        arrival = self._require_arrival()
        config = self._require_prepared()
        snapshot = arrival.snapshot
        ledger = snapshot.ledger
        history = ledger.history
        assert self._current is not None
        tick, sim_time_ns = self._current
        state = {
            "run_id": self._workload_identity.run_id,
            "provider_id": self._workload_identity.provider_id,
            "config_digest": self._workload_identity.config_digest,
            "seed": self._workload_identity.seed,
            "scenario_digest": self._scenario_digest,
            "current_time": {"tick": tick, "sim_time_ns": sim_time_ns},
            "accepted_stage_inputs": self._accepted_stage_inputs,
            "logistics_history_root": (
                history[-1].record_hash if history else "0" * 64
            ),
            "history_length": len(history),
            "ledger": ledger.model_dump(mode="json"),
            "arrival_snapshot_digest": snapshot.canonical_digest(),
            "arrival_snapshot": snapshot.model_dump(mode="json"),
            "initial_requests": [
                request.model_dump(mode="json")
                for request in self._initial_requests
            ],
            "initial_time": self._initial_time.model_dump(mode="json"),
            "observation_journal": (
                None
                if self._observations is None
                else self._observations.model_dump(mode="json")
            ),
            "observation_journal_digest": (
                None
                if self._observations is None
                else self._observations.canonical_digest()
            ),
            "facilities": config.task_package.facilities.model_dump(mode="json"),
            "principal_bindings": [
                binding.model_dump(mode="json")
                for binding in config.principal_bindings
            ],
        }
        if config.scheduled_orders:
            state["scheduled_applications"] = [item.model_dump(mode="json") for item in self._scheduled_applications]
        if self._native_parcel is not None:
            state["native_parcel"] = self._native_parcel.snapshot()
        return hashlib.sha256(_canonical_json(state)).hexdigest()

    def _write_artifact(self, *, event_chain_root: str) -> str:
        return self._writer.write(
            self._artifact_content(event_chain_root=event_chain_root)
        )

    def _receipt(self) -> dict[str, object]:
        arrival = self._require_arrival()
        assert self._current is not None
        tick, sim_time_ns = self._current
        time_value = {"tick": tick, "sim_time_ns": sim_time_ns}
        digest = self._state_digest()
        history = arrival.snapshot.ledger.history
        latest_stage_input = (
            self._accepted_stage_inputs[-1] if self._accepted_stage_inputs else None
        )
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
                    "name": "logistics_history_root",
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
        scheduled_events = [
            {
                "provider_id": self._workload_identity.provider_id,
                "event_id": f"scheduled.{item.requested.event_id}",
                "time": time_value,
                "payload_schema_id": SCHEDULED_ARRIVAL_EVENT_SCHEMA,
                "payload": [{"name": "application_json", "value": _canonical_json(item.model_dump(mode="json")).decode("utf-8")}],
            }
            for item in self._scheduled_applications if item.applied_at.tick == tick
        ]
        return {
            "run_id": self._workload_identity.run_id,
            "provider_id": self._workload_identity.provider_id,
            "reached": time_value,
            "state_digest": digest,
            "events": [state_event, *scheduled_events],
        }

    async def handle(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        async with self._lock:
            if operation == "probe":
                self._require_open()
                if payload != {}:
                    raise _invalid(
                        "logistics business provider probe request must be empty"
                    )
                return self._probe_response()
            self._require_session_token(payload)
            self._require_open()
            if operation == "prepare":
                return self._handle_prepare(payload)
            if operation == "reset":
                return self._handle_reset(payload)
            if operation == "step_stage":
                return self._handle_step_stage(payload)
            if operation == LOGISTICS_OBSERVATION_INGEST_OPERATION:
                return self._handle_observation_ingest(payload)
            if operation == PARCEL_SNAPSHOT_OPERATION:
                return self._handle_parcel_snapshot(payload)
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
            raise LogisticsBusinessServiceError(
                "operation.unknown",
                f"logistics business operation is not supported: {operation}",
            )

    async def serve_rpc(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            return await self.handle(operation, payload)
        except LogisticsBusinessServiceError as exc:
            return {"error": {"code": exc.code, "detail": exc.detail}}


def _validate_sha256_environment(value: str, *, label: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None or value == "0" * 64:
        raise SystemExit(f"{label} must be a non-placeholder SHA-256 digest")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AERO-BENCH Logistics Business provider"
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
    service = LogisticsBusinessProviderService(
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
