"""Public contracts for audited, offline SUMO authoring previews.

These contracts do not describe a formal Provider execution or verifier verdict.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from aero_bench.authoring.workspace import CityWorkspaceDraft
from aero_bench.config.models import Identifier, Sha256, StrictModel


ScenePath = Annotated[
    str, Field(pattern=r"^/city-presentation/[A-Za-z0-9_-]+\.json$")
]


class TrafficPreviewProfile(StrictModel):
    profile_id: Identifier
    profile_sha256: Sha256
    scene_path: ScenePath
    scene_sha256: Sha256
    source_license_status: Annotated[
        Literal["documented"],
        Field(description=(
            "Status of the pinned source-dataset licence record; "
            "this does not describe viewer-model licences."
        )),
    ]
    preview_scope: Literal["offline-engineering-preview"]


class TrafficPreviewProfileCatalog(StrictModel):
    schema_version: Literal["aero-bench.traffic-preview-profile-catalog/v1"]
    profiles: tuple[TrafficPreviewProfile, ...]


class TrafficPreviewRequest(StrictModel):
    schema_version: Literal["aero-bench.traffic-preview-request/v1"]
    profile_id: Identifier
    profile_sha256: Sha256
    duration_seconds: Annotated[int, Field(ge=30, le=180)]
    draft: CityWorkspaceDraft


class TrafficPreviewArtifact(StrictModel):
    url: Annotated[
        str,
        Field(pattern=r"^/authoring/v1/traffic-previews/[0-9a-f]{64}/assets/[0-9a-f]{64}$"),
    ]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    media_type: Literal["application/json"]

    @model_validator(mode="after")
    def url_uses_declared_digest(self) -> TrafficPreviewArtifact:
        if not self.url.endswith(f"/assets/{self.sha256}"):
            raise ValueError("traffic preview URL must use its declared content digest")
        return self


class TrafficPreviewAuditIdentity(StrictModel):
    status: Literal["PASS"]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]


class TrafficPreviewJobError(StrictModel):
    code: Identifier
    message: Annotated[str, Field(min_length=1)]


class TrafficPreviewJob(StrictModel):
    schema_version: Literal["aero-bench.traffic-preview-job/v1"]
    job_id: Sha256
    profile_id: Identifier
    profile_sha256: Sha256
    workspace_sha256: Sha256
    workspace_size_bytes: Annotated[int, Field(gt=0)]
    duration_seconds: Annotated[int, Field(ge=30, le=180)]
    state: Literal["queued", "recording", "auditing", "ready", "failed"]
    trace: TrafficPreviewArtifact | None
    canonical_audit: TrafficPreviewAuditIdentity | None
    error: TrafficPreviewJobError | None
    preview_scope: Literal["offline-engineering-preview"]
    formal_provider_bound: Literal[False]
    executed: Literal[False]
    verified: Literal[False]

    @model_validator(mode="after")
    def state_fields_are_consistent(self) -> TrafficPreviewJob:
        if self.state == "ready":
            if self.trace is None or self.canonical_audit is None or self.error is not None:
                raise ValueError("ready traffic preview requires trace and PASS audit only")
            expected_prefix = f"/authoring/v1/traffic-previews/{self.job_id}/assets/"
            if not self.trace.url.startswith(expected_prefix):
                raise ValueError("traffic preview URL must use its job identity")
        elif self.state == "failed":
            if self.error is None or self.trace is not None or self.canonical_audit is not None:
                raise ValueError("failed traffic preview requires an error and no published artifact")
        elif self.trace is not None or self.canonical_audit is not None or self.error is not None:
            raise ValueError("unfinished traffic preview cannot publish artifacts or errors")
        return self


__all__ = [
    "TrafficPreviewArtifact",
    "TrafficPreviewAuditIdentity",
    "TrafficPreviewJob",
    "TrafficPreviewJobError",
    "TrafficPreviewProfile",
    "TrafficPreviewProfileCatalog",
    "TrafficPreviewRequest",
]
