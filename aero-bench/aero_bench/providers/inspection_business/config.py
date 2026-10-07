from __future__ import annotations

from typing import Literal

from aero_bench.config.models import Identifier, StrictModel
from aero_bench.tasks.inspection.contracts import InspectionTaskPackage


class InspectionBusinessConfig(StrictModel):
    """Strict domain declaration for the deterministic business Provider.

    The declaration carries only domain identity and the canonical
    InspectionTaskPackage. The runtime image is not a domain fact: it is
    pinned by the ProviderManifest that the materializer resolves from
    ProviderRef.workload.runtime.image. Endpoints, protocol versions, and
    runtime deployment parameters are deliberately absent: the materializer
    owns the runtime endpoint (RuntimeEndpoint/ProviderRef.port) and the RPC
    protocol version is fixed by the adapter implementation, so no
    configuration field can silently repoint or re-version a declared run.
    """

    schema_version: Literal["aero-bench.inspection-business/v1"]
    provider_id: Identifier
    task_package: InspectionTaskPackage


__all__ = ["InspectionBusinessConfig"]
