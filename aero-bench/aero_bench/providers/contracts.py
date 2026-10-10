from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import Field

from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    Identifier,
    ImplementationIdentity,
    Sha256,
    StrictModel,
)
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    ProviderEvent,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    ProviderStageRequest,
    ProviderStageResult,
    StepReceipt,
)


class ProviderManifest(StrictModel):
    provider_id: Identifier
    adapter: Identifier
    implementation: ImplementationIdentity
    runtime_image: Annotated[
        str,
        Field(
            pattern=(
                r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$"
            )
        ),
    ]
    config_digest: Sha256
    capabilities: tuple[Identifier, ...]
    protocol_schema: FileRef
    artifact_requirements: tuple[ArtifactRequirement, ...]


class ProviderCommandResult(StrictModel):
    """Internal command outcome; Provider events are never Agent-visible."""

    receipts: tuple[CommandReceipt, ...] = Field(min_length=1)
    events: tuple[ProviderEvent, ...] = ()


class ProviderSession(Protocol):
    @property
    def manifest(self) -> ProviderManifest: ...

    async def prepare(self) -> None: ...

    async def reset(self, *, seed: int) -> StepReceipt: ...

    async def step_stage(
        self, request: ProviderStageRequest
    ) -> ProviderStageResult: ...

    async def handle_command(
        self, request: CommandRequest
    ) -> ProviderCommandResult: ...

    async def finalize(
        self, request: ProviderFinalizationRequest
    ) -> ProviderFinalizationReceipt: ...

    async def snapshot_digest(self) -> str: ...

    async def shutdown(self) -> None: ...
