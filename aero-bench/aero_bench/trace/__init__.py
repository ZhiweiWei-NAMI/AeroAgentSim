"""Strict Public Trace v3 contracts and deterministic projector.

The public trace is the only Viewer-facing projection of a run. It is produced
from immutable ResolvedRun authority, sealed runtime evidence, and an optional
validated formal verification report. There is no legacy compatibility path.
"""

from aero_bench.trace.contracts import (
    PUBLIC_PROJECTOR_VERSION,
    PUBLIC_TRACE_SCHEMA_VERSION,
    PUBLIC_VERIFICATION_SCHEMA_VERSION,
    ArtifactReference,
    PublicReplayFile,
    PublicReplayIndex,
    PublicReplayManifest,
    PublicReplayShard,
    PublicTrace,
    PublicTrafficLightFrame,
    PublicTrafficLightState,
)
from aero_bench.trace.projector import (
    PublicProjectorError,
    SealedPublicArtifacts,
    project_public_run_event,
    project_public_scenario,
    project_public_trace,
)
from aero_bench.trace.vocabulary import (
    PUBLIC_EVENT_EVENT_TYPE,
    PUBLIC_NETWORK_LINK_EVENT_TYPE,
    PUBLIC_PROVIDER_EVENT_TYPES,
    PUBLIC_SENSOR_FRAME_EVENT_TYPE,
    PUBLIC_STATUS_EVENT_TYPE,
    PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE,
)

__all__ = [
    "PUBLIC_EVENT_EVENT_TYPE",
    "PUBLIC_NETWORK_LINK_EVENT_TYPE",
    "PUBLIC_PROJECTOR_VERSION",
    "PUBLIC_PROVIDER_EVENT_TYPES",
    "PUBLIC_SENSOR_FRAME_EVENT_TYPE",
    "PUBLIC_STATUS_EVENT_TYPE",
    "PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE",
    "PUBLIC_TRACE_SCHEMA_VERSION",
    "PUBLIC_VERIFICATION_SCHEMA_VERSION",
    "ArtifactReference",
    "PublicProjectorError",
    "PublicReplayFile",
    "PublicReplayIndex",
    "PublicReplayManifest",
    "PublicReplayShard",
    "PublicTrace",
    "PublicTrafficLightFrame",
    "PublicTrafficLightState",
    "SealedPublicArtifacts",
    "project_public_run_event",
    "project_public_scenario",
    "project_public_trace",
]
