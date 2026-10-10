from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from aero_bench.config.models import Identifier, StrictModel


class SoftwareIdentity(StrictModel):
    version: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class WorldSceneConfig(StrictModel):
    """Implementation identity for the read-only scenario projection service.

    Static world declarations are compiled into ``ResolvedScenario`` and never
    repeated in provider configuration. Deployment, protocol, and runtime image
    identities remain materializer/manifest owned.
    """

    schema_version: Literal["aero-bench.world-scene/v2"]
    provider_id: Identifier
    scene: SoftwareIdentity
