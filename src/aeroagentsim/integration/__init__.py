"""Independent external-observation foundation, with no native lifecycle wiring."""

from .contracts import (
    ContractError,
    FrameKey,
    ObservationFrame,
    RunIdentity,
    ViewKey,
    contract_payload,
    enu_to_render,
    relative_seconds,
    render_to_enu,
)
from .normalization import canonical_bytes, normalize_frame
from .replay import ArtifactReader, MappingArtifactReader, ReadOnlyReplay, SeekResult
from .selection import Selection, SharedViewStore, ViewSnapshot
from .semantics import (
    EvaluationRequest,
    NativeEvaluator,
    SemanticBinding,
    SemanticEvidence,
    SemanticSession,
)
from .bench import BenchContext, BenchStreamCursors, normalize_bench_scene
from .bench_replay import (
    BenchReplayIndex,
    BenchShard,
    normalize_bench_replay_index,
    read_verified_shard,
    validate_bench_manifest_header,
)
from .network import (
    PublicEventIdentity,
    join_link_evidence,
    normalize_link_properties,
    normalize_network_geometry,
    normalize_provider_receipt,
)

__all__ = [
    "ArtifactReader",
    "ContractError",
    "EvaluationRequest",
    "FrameKey",
    "MappingArtifactReader",
    "NativeEvaluator",
    "ObservationFrame",
    "ReadOnlyReplay",
    "RunIdentity",
    "SeekResult",
    "Selection",
    "SemanticBinding",
    "SemanticEvidence",
    "SemanticSession",
    "SharedViewStore",
    "ViewKey",
    "ViewSnapshot",
    "canonical_bytes",
    "contract_payload",
    "enu_to_render",
    "normalize_frame",
    "relative_seconds",
    "render_to_enu",
    "BenchContext",
    "BenchReplayIndex",
    "BenchShard",
    "BenchStreamCursors",
    "PublicEventIdentity",
    "join_link_evidence",
    "normalize_bench_replay_index",
    "normalize_bench_scene",
    "normalize_link_properties",
    "normalize_network_geometry",
    "normalize_provider_receipt",
    "read_verified_shard",
    "validate_bench_manifest_header",
]
