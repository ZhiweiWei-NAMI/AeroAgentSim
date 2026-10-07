from __future__ import annotations

import math
from collections.abc import Callable
from enum import Enum
from functools import cache
from pathlib import PurePosixPath
from types import UnionType
from typing import Annotated, Literal, TypeAlias, Union, get_args, get_origin
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExecutionScope: TypeAlias = Literal["executor_validation", "formal_benchmark"]
JsonScalar: TypeAlias = str | int | float | bool | None


class _NormalizedFields(dict):
    """Private marker for a completed declared-field normalization pass."""

    __slots__ = ("model",)

    def __init__(self, value: dict, model: type[BaseModel]) -> None:
        super().__init__(value)
        self.model = model


def _annotation_is_active(annotation: object) -> bool:
    """Whether this branch can change a value or its container identity."""

    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_is_active(get_args(annotation)[0])
    if origin in (tuple, list, set, frozenset, dict):
        return True
    if origin in (Union, UnionType):
        return any(_annotation_is_active(branch) for branch in get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, (Enum, BaseModel))


@cache
def _normalization_is_safe(model: type[BaseModel]) -> bool:
    """Exclude competing active union branches throughout the model tree.

    A single active branch has an equal canonical result on a repeated pass.
    Multiple active branches can alternate conversions, so those trees retain
    every normalization pass. Visit each nested model once to terminate cycles.
    """

    visited: set[type[BaseModel]] = set()

    def safe(annotation: object) -> bool:
        origin = get_origin(annotation)
        if origin is Annotated:
            return safe(get_args(annotation)[0])
        if origin in (Union, UnionType):
            branches = get_args(annotation)
            if sum(_annotation_is_active(branch) for branch in branches) >= 2:
                return False
            return all(safe(branch) for branch in branches)
        if origin in (tuple, list, set, frozenset, dict):
            return all(safe(item) for item in get_args(annotation))
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in visited:
                return True
            visited.add(annotation)
            return all(safe(field.annotation) for field in annotation.model_fields.values())
        return True

    return safe(model)


def _identity_normalization(value: object) -> object:
    return value


_NormalizationPlan: TypeAlias = Callable[[object], object]
_unhashable_normalization_plans: dict[int, tuple[object, _NormalizationPlan]] = {}


def _normalization_plan(annotation: object) -> _NormalizationPlan:
    try:
        hash(annotation)
    except TypeError:
        # Keep the annotation alive so a later object cannot reuse its ID.
        key = id(annotation)
        cached = _unhashable_normalization_plans.get(key)
        if cached is None:
            cached = (annotation, _build_normalization_plan(annotation))
            _unhashable_normalization_plans[key] = cached
        return cached[1]
    return _hashable_normalization_plan(annotation)


@cache
def _hashable_normalization_plan(annotation: object) -> _NormalizationPlan:
    return _build_normalization_plan(annotation)


@cache
def _model_field_plans(model: type[BaseModel]) -> tuple[tuple[str, _NormalizationPlan], ...]:
    # Scalar identity fields can be skipped, but scalar containers still need
    # their structural conversion and fresh-container identity for unions.
    return tuple(
        (name, plan)
        for name, field in model.model_fields.items()
        if (plan := _normalization_plan(field.annotation)) is not _identity_normalization
    )


def _normalize_model_fields(
    value: dict, model: type[BaseModel], strict: bool,
) -> dict:
    normalized = _NormalizedFields(value, model) if strict else dict(value)
    for name, plan in _model_field_plans(model):
        if name in normalized:
            normalized[name] = plan(normalized[name])
    return normalized


def _build_normalization_plan(annotation: object) -> _NormalizationPlan:
    """Compile the old conversions, including identity-sensitive union order."""

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return _normalization_plan(arguments[0]) if arguments else _identity_normalization
    if origin is tuple:
        variadic = len(arguments) == 2 and arguments[1] is Ellipsis
        plans = tuple(
            _normalization_plan(item)
            for item in (arguments[:1] if variadic else arguments)
        )

        def normalize_tuple(value: object) -> object:
            if isinstance(value, list):
                value = tuple(value)
            if not isinstance(value, tuple) or not arguments:
                return value
            if variadic:
                return tuple(plans[0](item) for item in value)
            if len(arguments) != len(value):
                return value
            return tuple(plan(item) for item, plan in zip(value, plans, strict=True))

        return normalize_tuple
    if origin in (list, set, frozenset):
        if len(arguments) != 1:
            return _identity_normalization
        item_plan = _normalization_plan(arguments[0])

        def normalize_container(value: object) -> object:
            if not isinstance(value, (list, tuple, set, frozenset)):
                return value
            normalized = [item_plan(item) for item in value]
            if origin is list:
                return normalized
            if origin is set:
                return set(normalized)
            return frozenset(normalized)

        return normalize_container
    if origin is dict:
        if len(arguments) != 2:
            return _identity_normalization
        key_plan, item_plan = (_normalization_plan(item) for item in arguments)

        def normalize_dict(value: object) -> object:
            if not isinstance(value, dict):
                return value
            return {key_plan(key): item_plan(item) for key, item in value.items()}

        return normalize_dict
    if origin in (Union, UnionType):
        plans = tuple(_normalization_plan(branch) for branch in arguments)
        if all(plan is _identity_normalization for plan in plans):
            return _identity_normalization

        def normalize_union(value: object) -> object:
            for plan in plans:
                normalized = plan(value)
                if normalized is not value:
                    return normalized
            return value

        return normalize_union
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        def normalize_enum(value: object) -> object:
            if isinstance(value, str) and not isinstance(value, annotation):
                try:
                    return annotation(value)
                except ValueError:
                    return value
            return value

        return normalize_enum
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        strict = issubclass(annotation, StrictModel)

        def normalize_model(value: object) -> object:
            if not isinstance(value, dict):
                return value
            # Resolve field plans lazily: recursive model annotations may refer
            # back to this plan, and model fields are complete at validation.
            return _normalize_model_fields(value, annotation, strict)

        return normalize_model
    return _identity_normalization


def _normalize_declared_value(value: object, annotation: object) -> object:
    """Normalize JSON-shaped values without coercing declared scalar types.

    Strict Pydantic validation treats a Python ``str`` as different from a
    ``str, Enum`` member, although the member is necessarily represented by
    that string on the JSON wire.  Convert only exact declared enum *values*
    and recurse through declared containers/models; integers, floats, booleans,
    identifiers, and unknown enum values remain untouched for strict rejection.
    """

    return _normalization_plan(annotation)(value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_declared_json_values(cls, value: object) -> object:
        """Normalize structural JSON values at the strict model boundary.

        Pydantic strict mode correctly rejects scalar coercion but Python
        mappings decoded from JSON also need their exact enum values converted
        to members before validation.  The normalization is structural and
        never accepts aliases, legacy field names, or scalar coercion.
        """

        if not isinstance(value, dict):
            return value
        if (
            isinstance(value, _NormalizedFields)
            and value.model is cls
            and _normalization_is_safe(cls)
        ):
            return value
        return _normalize_model_fields(value, cls, True)


def _relative_bundle_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or value.strip() != value
        or str(path) != value
        or "\\" in value
        or "://" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("path must be normalized and relative to the bundle")
    return value


def _non_placeholder_digest(value: str) -> str:
    if value == "0" * 64:
        raise ValueError("sha256 must be a real digest, not a placeholder")
    return value


class FileRef(StrictModel):
    path: Annotated[str, Field(min_length=1)]
    sha256: Sha256

    _validate_path = field_validator("path")(_relative_bundle_path)
    _validate_digest = field_validator("sha256")(_non_placeholder_digest)


class RuntimeImage(StrictModel):
    image: Annotated[
        str,
        Field(
            pattern=(
                r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
            )
        ),
    ]
    command: tuple[Annotated[str, Field(min_length=1)], ...] = Field(min_length=1)

    @field_validator("image")
    @classmethod
    def non_placeholder_image_digest(cls, value: str) -> str:
        digest = value.rsplit(":", maxsplit=1)[1]
        _non_placeholder_digest(digest)
        return value

    @property
    def is_local_id(self) -> bool:
        """Whether this is an immutable Docker-local config ID."""

        return self.image.startswith("sha256:")

    @property
    def immutable_digest(self) -> str:
        """Return the validated digest portion of either supported reference."""

        return self.image.rsplit(":", maxsplit=1)[1]


class ResourceBudget(StrictModel):
    cpu_millicores: Annotated[int, Field(gt=0)]
    memory_mib: Annotated[int, Field(ge=64)]
    gpu_count: Annotated[int, Field(ge=0)]


class ImplementationIdentity(StrictModel):
    component_id: Identifier
    kind: Literal["mechanical_fixture", "production"]
    source_uri: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    source_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
    version: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")]

    @model_validator(mode="after")
    def production_identity_is_not_a_placeholder(self) -> "ImplementationIdentity":
        if set(self.source_revision) == {"0"}:
            raise ValueError("implementation source_revision cannot be a placeholder")
        hostname = (urlparse(self.source_uri).hostname or "").lower()
        reserved_hosts = {
            "example.com",
            "example.net",
            "example.org",
            "localhost",
        }
        if self.kind == "production" and (
            hostname in reserved_hosts or hostname.endswith(".invalid")
        ):
            raise ValueError("production implementation source_uri cannot be reserved")
        return self


class RuntimeSpec(StrictModel):
    runtime: RuntimeImage
    resources: ResourceBudget
    implementation: ImplementationIdentity


class NamedValue(StrictModel):
    name: Identifier
    value: JsonScalar

    @field_validator("value")
    @classmethod
    def finite_float(cls, value: JsonScalar) -> JsonScalar:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parameter value must be finite")
        return value


class SchemaBoundFile(StrictModel):
    file: FileRef
    schema_file: FileRef


class TaskPackageRef(StrictModel):
    package_id: Identifier
    config: SchemaBoundFile


class FeasibilityAssessment(StrictModel):
    package_id: Identifier
    feasible: bool
    success_upper_bound: float
    failed_conditions: tuple[Identifier, ...]
    bounds: tuple[NamedValue, ...]

    @field_validator("success_upper_bound")
    @classmethod
    def bounded_success(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("success_upper_bound must be finite and in [0, 1]")
        return value

    @model_validator(mode="after")
    def assessment_is_consistent(self) -> "FeasibilityAssessment":
        names = [item.name for item in self.bounds]
        if len(names) != len(set(names)):
            raise ValueError("feasibility bound names must be unique")
        if self.feasible != (self.success_upper_bound > 0.0):
            raise ValueError("feasible must agree with success_upper_bound")
        if self.feasible and self.failed_conditions:
            raise ValueError("feasible assessment cannot list failed conditions")
        if not self.feasible and not self.failed_conditions:
            raise ValueError("infeasible assessment must list failed conditions")
        return self


class ArtifactRequirement(StrictModel):
    artifact_id: Identifier
    artifact_type: Identifier
    producer_id: Identifier
    visibility: Literal["public", "private"]
    relative_path: Annotated[str, Field(min_length=1)]
    max_size_bytes: Annotated[int, Field(gt=0)]
    source_asset_id: Identifier | None

    _validate_path = field_validator("relative_path")(_relative_bundle_path)

    @model_validator(mode="after")
    def source_matches_producer(self) -> "ArtifactRequirement":
        if self.producer_id == "bundle" and self.source_asset_id is None:
            raise ValueError("bundle artifacts require source_asset_id")
        if self.producer_id != "bundle" and self.source_asset_id is not None:
            raise ValueError("runtime artifacts cannot name source_asset_id")
        return self


class AssetAudience(StrictModel):
    role: Literal["agent", "provider", "verifier"]
    workload_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_workload_ids(self) -> "AssetAudience":
        if len(self.workload_ids) != len(set(self.workload_ids)):
            raise ValueError("asset audience workload_ids must be unique")
        return self


class AssetRef(StrictModel):
    asset_id: Identifier
    file: FileRef
    classification: Literal["public", "private"]
    audiences: tuple[AssetAudience, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_audiences(self) -> "AssetRef":
        roles = [audience.role for audience in self.audiences]
        if len(roles) != len(set(roles)):
            raise ValueError("an asset may declare each audience role only once")
        if self.classification == "private" and "agent" in roles:
            raise ValueError("private assets cannot be visible to agents")
        return self


class ToolGrant(StrictModel):
    tool_id: Identifier
    provider_id: Identifier
    request_schema: FileRef
    response_schema: FileRef
    timeout_ms: Annotated[int, Field(gt=0)]
    idempotent: bool


class ObservationGrant(StrictModel):
    observation_id: Identifier
    provider_id: Identifier
    schema_file: FileRef
    timeout_ms: Annotated[int, Field(gt=0)]


class QueryGrant(StrictModel):
    """One explicit, read-only Provider query authorization."""

    query_type: Identifier
    provider_id: Identifier
    request_schema: FileRef
    response_schema: FileRef
    timeout_ms: Annotated[int, Field(gt=0)]


class AgentDriverSpec(StrictModel):
    """An explicitly isolated model session and trusted interaction recorder."""

    driver_id: Identifier
    workload: RuntimeSpec
    config: SchemaBoundFile
    bridge_port: Annotated[int, Field(ge=1024, le=65535)]
    artifact_requirements: tuple[ArtifactRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def driver_identity_and_outputs(self) -> "AgentDriverSpec":
        if self.workload.implementation.component_id != self.driver_id:
            raise ValueError("driver implementation component_id must equal driver_id")
        if any(
            item.producer_id != self.driver_id or item.visibility != "private"
            for item in self.artifact_requirements
        ):
            raise ValueError("driver artifacts must be private and driver-owned")
        ids = [item.artifact_id for item in self.artifact_requirements]
        paths = [item.relative_path for item in self.artifact_requirements]
        if len(set(ids)) != len(ids) or len(set(paths)) != len(paths):
            raise ValueError("driver artifact IDs and paths must be unique")
        if {item.artifact_type for item in self.artifact_requirements} != {
            "model.session-manifest", "model.interactions"
        } or len(self.artifact_requirements) != 2:
            raise ValueError("driver must declare exactly its manifest and interaction log")
        return self


class AgentSpec(StrictModel):
    schema_version: Literal["aero-bench.agent/v2"]
    agent_id: Identifier
    workload: RuntimeSpec
    tools: tuple[ToolGrant, ...]
    queries: tuple[QueryGrant, ...]
    observations: tuple[ObservationGrant, ...]
    artifact_requirements: tuple[ArtifactRequirement, ...]
    driver: AgentDriverSpec | None = None

    @model_validator(mode="after")
    def unique_grants(self) -> "AgentSpec":
        if self.workload.implementation.component_id != self.agent_id:
            raise ValueError("agent implementation component_id must equal agent_id")
        if self.driver is not None and self.driver.driver_id == self.agent_id:
            raise ValueError("agent and driver identities must be distinct")
        tool_ids = [grant.tool_id for grant in self.tools]
        query_types = [grant.query_type for grant in self.queries]
        observation_ids = [grant.observation_id for grant in self.observations]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool_id values must be unique per agent")
        if len(query_types) != len(set(query_types)):
            raise ValueError("query_type values must be unique per agent")
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("observation_id values must be unique per agent")
        artifact_ids = [
            requirement.artifact_id for requirement in self.artifact_requirements
        ]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("agent artifact requirements must have unique artifact_id")
        artifact_paths = [
            requirement.relative_path for requirement in self.artifact_requirements
        ]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError(
                "agent artifact requirements must have unique relative_path"
            )
        if any(
            requirement.producer_id != self.agent_id
            for requirement in self.artifact_requirements
        ):
            raise ValueError("agent artifact producer_id must equal agent_id")
        return self


class ProviderRef(StrictModel):
    provider_id: Identifier
    adapter: Identifier
    port: Annotated[int, Field(ge=1024, le=65535)]
    workload: RuntimeSpec
    config: SchemaBoundFile
    protocol_schema: FileRef
    capabilities: tuple[Identifier, ...] = Field(min_length=1)
    artifact_requirements: tuple[ArtifactRequirement, ...]

    @model_validator(mode="after")
    def unique_contract_fields(self) -> "ProviderRef":
        if self.workload.implementation.component_id != self.adapter:
            raise ValueError("provider implementation component_id must equal adapter")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("provider capabilities must be unique")
        artifact_ids = [item.artifact_id for item in self.artifact_requirements]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(
                "provider artifact requirements must have unique artifact_id"
            )
        artifact_paths = [item.relative_path for item in self.artifact_requirements]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError(
                "provider artifact requirements must have unique relative_path"
            )
        if any(
            requirement.producer_id != self.provider_id
            for requirement in self.artifact_requirements
        ):
            raise ValueError("provider artifact producer_id must equal provider_id")
        return self


class ClockSpec(StrictModel):
    authority: Literal["provider_barrier"]
    step_ns: Annotated[int, Field(gt=0)]
    max_steps: Annotated[int, Field(gt=0)]
    provider_timeout_ms: Annotated[int, Field(gt=0)]


class GatewaySpec(StrictModel):
    protocol_schema: FileRef
    port: Annotated[int, Field(ge=1024, le=65535)]


class EnvironmentSpec(StrictModel):
    schema_version: Literal["aero-bench.environment/v1"]
    environment_id: Identifier
    harness: RuntimeSpec
    clock: ClockSpec
    gateway: GatewaySpec
    providers: tuple[ProviderRef, ...] = Field(min_length=1)
    harness_artifact_requirements: tuple[ArtifactRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_provider_ids(self) -> "EnvironmentSpec":
        if self.harness.implementation.component_id != "aero-bench.harness":
            raise ValueError(
                "harness implementation component_id must be aero-bench.harness"
            )
        provider_ids = [provider.provider_id for provider in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider_id values must be unique")
        if any(
            requirement.producer_id != "harness"
            for requirement in self.harness_artifact_requirements
        ):
            raise ValueError("harness artifact producer_id must be harness")
        artifact_ids = [
            requirement.artifact_id
            for requirement in self.harness_artifact_requirements
        ]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(
                "harness artifact requirements must have unique artifact_id"
            )
        artifact_paths = [
            requirement.relative_path
            for requirement in self.harness_artifact_requirements
        ]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError(
                "harness artifact requirements must have unique relative_path"
            )
        event_logs = [
            requirement
            for requirement in self.harness_artifact_requirements
            if requirement.artifact_type == "event.log"
        ]
        if len(event_logs) != 1 or event_logs[0].visibility != "private":
            raise ValueError(
                "harness must produce exactly one private authoritative event.log"
            )
        return self


class GoalSpec(StrictModel):
    goal_id: Identifier
    verifier_id: Identifier
    metric_id: Identifier
    operator: Literal["ge", "gt", "le", "lt", "eq"]
    threshold: float
    evidence: Literal["authoritative_state", "event_log", "artifact"]
    parameters: tuple[NamedValue, ...]

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("goal threshold must be finite")
        return value

    @model_validator(mode="after")
    def unique_parameters(self) -> "GoalSpec":
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("goal parameter names must be unique")
        return self


class VerifierSpec(StrictModel):
    verifier_id: Identifier
    workload: RuntimeSpec
    config: SchemaBoundFile
    artifact_requirements: tuple[ArtifactRequirement, ...]
    output_artifacts: tuple[ArtifactRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_artifact_requirements(self) -> "VerifierSpec":
        if self.workload.implementation.component_id != self.verifier_id:
            raise ValueError(
                "verifier implementation component_id must equal verifier_id"
            )
        artifact_ids = [item.artifact_id for item in self.artifact_requirements]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError(
                "verifier artifact requirements must have unique artifact_id"
            )
        artifact_paths = [item.relative_path for item in self.artifact_requirements]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError(
                "verifier artifact requirements must have unique relative_path"
            )
        output_ids = [item.artifact_id for item in self.output_artifacts]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("verifier output artifacts must have unique artifact_id")
        output_paths = [item.relative_path for item in self.output_artifacts]
        if len(output_paths) != len(set(output_paths)):
            raise ValueError("verifier output artifacts must have unique relative_path")
        if any(
            requirement.producer_id != self.verifier_id
            for requirement in self.output_artifacts
        ):
            raise ValueError("verifier output producer_id must equal verifier_id")
        return self


class TaskSpec(StrictModel):
    schema_version: Literal["aero-bench.task/v1"]
    task_id: Identifier
    package: TaskPackageRef
    instruction: FileRef
    required_capabilities: tuple[Identifier, ...]
    required_tools: tuple[Identifier, ...]
    assets: tuple[AssetRef, ...]
    goals: tuple[GoalSpec, ...] = Field(min_length=1)
    verifier: VerifierSpec

    @model_validator(mode="after")
    def task_contract_is_consistent(self) -> "TaskSpec":
        asset_ids = [asset.asset_id for asset in self.assets]
        goal_ids = [goal.goal_id for goal in self.goals]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id values must be unique")
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("goal_id values must be unique")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required capabilities must be unique")
        if len(self.required_tools) != len(set(self.required_tools)):
            raise ValueError("required tools must be unique")
        if any(goal.verifier_id != self.verifier.verifier_id for goal in self.goals):
            raise ValueError("each goal must name the declared verifier")
        return self


StrictJsonScalar: TypeAlias = StrictStr | StrictInt | StrictFloat | StrictBool | None
MatrixValue: TypeAlias = StrictJsonScalar | FileRef


class MatrixAxis(StrictModel):
    axis_id: Identifier
    target: Annotated[
        str, Field(pattern=r"^/(task|environment|agents)(?:/[A-Za-z0-9_~.-]+)+$")
    ]
    values: tuple[MatrixValue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_values(self) -> "MatrixAxis":
        serialized = tuple(
            value.model_dump_json() if isinstance(value, FileRef) else repr(value)
            for value in self.values
        )
        if len(serialized) != len(set(serialized)):
            raise ValueError("matrix values must be unique")
        return self


class CaseSpec(StrictModel):
    case_id: Identifier
    task: FileRef
    environment: FileRef
    agents: tuple[FileRef, ...] = Field(min_length=1)
    world_package: FileRef
    launch_site_ids: tuple[Identifier | None, ...] = Field(min_length=1)
    seeds: tuple[
        Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)],
        ...,
    ] = Field(min_length=1)
    axes: tuple[MatrixAxis, ...]

    @model_validator(mode="after")
    def unique_matrix_identity(self) -> "CaseSpec":
        if None in self.launch_site_ids and self.launch_site_ids != (None,):
            raise ValueError("a no-launch case requires exactly one explicit null selection")
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("seed values must be unique")
        if len(self.launch_site_ids) != len(set(self.launch_site_ids)):
            raise ValueError("launch_site_id values must be unique")
        axis_ids = [axis.axis_id for axis in self.axes]
        targets = [axis.target for axis in self.axes]
        if len(axis_ids) != len(set(axis_ids)):
            raise ValueError("matrix axis_id values must be unique")
        if len(targets) != len(set(targets)):
            raise ValueError("matrix targets must be unique")
        return self


class SuiteSpec(StrictModel):
    schema_version: Literal["aero-bench.suite/v2"]
    suite_id: Identifier
    aggregator_id: Identifier
    execution_scope: ExecutionScope
    cases: tuple[CaseSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> "SuiteSpec":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique")
        return self
