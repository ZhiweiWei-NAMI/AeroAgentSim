"""Wire contracts for immutable city draft compilation, separate from runs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from aero_bench.config.models import FileRef, Identifier, Sha256, StrictModel
from aero_bench.authoring.workspace import CityWorkspaceDraft


class CityCompileRequest(StrictModel):
    schema_version: Literal["aero-bench.city-compile-request/v1"]
    registration_id: Identifier
    registration_sha256: Sha256
    draft: CityWorkspaceDraft


class CompilationBlocker(StrictModel):
    code: Identifier
    field: Annotated[str, Field(pattern=r"^/(?:[A-Za-z0-9_~.-]+/)*[A-Za-z0-9_~.-]+$")]
    message: Annotated[str, Field(min_length=1)]


class CompiledRunIdentity(StrictModel):
    run_id: Sha256
    scenario_digest: Sha256
    world_id: Identifier
    world_digest: Sha256
    executor_kind: Literal["docker_reference", "kubernetes_cluster"]
    feasible: bool


class CityCompilationResult(StrictModel):
    schema_version: Literal["aero-bench.city-compilation-result/v1"]
    compilation_id: Sha256
    draft_sha256: Sha256
    registration_id: Identifier
    registration_sha256: Sha256
    status: Literal["blocked", "compiled"]
    blockers: tuple[CompilationBlocker, ...]
    suite: FileRef | None
    runs: tuple[CompiledRunIdentity, ...]
    # Resolution is not execution or independent verification.
    executed: Literal[False] = False
    verified: Literal[False] = False

    @model_validator(mode="after")
    def result_state_is_consistent(self) -> CityCompilationResult:
        if self.status == "blocked":
            if not self.blockers or self.runs or self.suite is not None:
                raise ValueError("blocked compilation cannot contain executable output")
        elif self.blockers or not self.runs or self.suite is None:
            raise ValueError("compiled result requires a suite and resolved runs without blockers")
        return self


class PublicSceneRegistration(StrictModel):
    registration_id: Identifier
    registration_sha256: Sha256
    scene_path: str
    scene_url: str
    scene_schema_version: Literal["aero-bench.public-scenario/v1"]
    scene_sha256: Sha256
    scene_size_bytes: Annotated[int, Field(gt=0)]
    world_id: Identifier
    world_digest: Sha256
    profile_id: Identifier
    editable_execution_fields: tuple[Literal["/seed", "/deployment/executor", "/events"], ...]
    retained_authoring_fields: tuple[Literal["/name", "/environment", "/stateKeyframes"], ...]
    reference_draft: CityWorkspaceDraft


class SceneRegistrationCatalog(StrictModel):
    schema_version: Literal["aero-bench.city-scene-registration-catalog/v1"]
    registrations: tuple[PublicSceneRegistration, ...]


class AuthoringApiError(StrictModel):
    code: Identifier
    message: Annotated[str, Field(min_length=1)]


class AuthoringApiErrorResponse(StrictModel):
    schema_version: Literal["aero-bench.authoring-error/v1"]
    error: AuthoringApiError
