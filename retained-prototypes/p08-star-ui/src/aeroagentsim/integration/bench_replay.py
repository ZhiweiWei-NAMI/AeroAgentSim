"""Thin sealed-index guards for a caller-owned BENCH replay decoder.

No SSE cursors, filesystem access, replay grants, or production scene decoder.
The complete PublicReplayFile and shard body schemas remain private boundaries.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Tuple

from .bench import BenchContext, digest, fields, integer
from .contracts import ContractError, freeze_json
from .replay import ArtifactReader


def safe_relative_path(value: Any) -> str:
    """Our conservative allowlist policy, not the unexpanded native path regex."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ContractError("unsafe replay relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ContractError("unsafe replay relative path")
    return value


@dataclass(frozen=True)
class BenchShard:
    relative_path: str
    sha256: str
    size_bytes: int
    first_tick: int
    last_tick: int
    scene_state_count: int


@dataclass(frozen=True)
class BenchReplayIndex:
    context: BenchContext
    event_chain_root: str
    first_tick: int
    last_tick: int
    scene_state_count: int
    shards: Tuple[BenchShard, ...]

    def history_shards(self, through_tick: int) -> Tuple[BenchShard, ...]:
        """Retain all preceding shards for latches, including cross-shard history."""
        integer(through_tick)
        if not self.first_tick <= through_tick <= self.last_tick:
            raise ContractError("sealed replay tick outside source range")
        if not self.shards:
            return ()
        if not any(shard.first_tick <= through_tick <= shard.last_tick for shard in self.shards):
            raise ContractError("sealed replay tick lies in an index gap")
        return tuple(shard for shard in self.shards if shard.first_tick <= through_tick)


def normalize_bench_replay_index(index: Mapping[str, Any], context: BenchContext, event_chain_root: str) -> BenchReplayIndex:
    fields(
        index,
        (
            "schema_version",
            "run_id",
            "scenario_digest",
            "event_chain_root",
            "first_tick",
            "last_tick",
            "scene_state_count",
            "shards",
        ),
        "sealed replay index",
    )
    if index["schema_version"] != "aero-bench.public-replay-index/v1" or (
        digest(index["run_id"]),
        digest(index["scenario_digest"]),
        digest(index["event_chain_root"]),
    ) != (context.run.run_id, context.scenario_digest, digest(event_chain_root)):
        raise ContractError("sealed replay schema/run/scenario/event-chain disagreement")
    first, last, count = (integer(index[key]) for key in ("first_tick", "last_tick", "scene_state_count"))
    if first > last or not isinstance(index["shards"], (list, tuple)):
        raise ContractError("invalid sealed replay bounds/shards")
    shards = []
    paths = set()
    for value in index["shards"]:
        fields(
            value, ("relative_path", "sha256", "size_bytes", "first_tick", "last_tick", "scene_state_count"), "replay shard"
        )
        shard = BenchShard(
            safe_relative_path(value["relative_path"]),
            digest(value["sha256"]),
            integer(value["size_bytes"], 1),
            integer(value["first_tick"], 1),
            integer(value["last_tick"], 1),
            integer(value["scene_state_count"], 1),
        )
        if not first <= shard.first_tick <= shard.last_tick <= last or shard.scene_state_count > min(
            256, shard.last_tick - shard.first_tick + 1
        ):
            raise ContractError("invalid shard range or scene count")
        if shard.relative_path in paths or (shards and shard.first_tick <= shards[-1].last_tick):
            raise ContractError("duplicate shard path or overlapping/unordered tick ranges")
        paths.add(shard.relative_path)
        shards.append(shard)
    if sum(shard.scene_state_count for shard in shards) != count or bool(shards) != bool(count):
        raise ContractError("sealed index/shard counts disagree")
    if shards and (shards[0].first_tick != first or shards[-1].last_tick != last):
        raise ContractError("sealed index/shard bounds disagree")
    return BenchReplayIndex(context, event_chain_root, first, last, count, tuple(shards))


def validate_bench_manifest_header(
    manifest: Mapping[str, Any], index: BenchReplayIndex, file_reference_validator: Callable[[Mapping[str, Any]], bool]
) -> None:
    """Delegate the unexpanded PublicReplayFile type to the existing private reader."""
    manifest = freeze_json(manifest)
    fields(
        manifest,
        ("schema_version", "run_id", "scenario_digest", "event_chain_root", "trace_sha256", "files"),
        "sealed replay manifest",
    )
    if manifest["schema_version"] != "aero-bench.public-replay-manifest/v1" or (
        digest(manifest["run_id"]),
        digest(manifest["scenario_digest"]),
        digest(manifest["event_chain_root"]),
    ) != (index.context.run.run_id, index.context.scenario_digest, index.event_chain_root):
        raise ContractError("sealed manifest/index identity disagreement")
    digest(manifest["trace_sha256"])
    if not isinstance(manifest["files"], (list, tuple)) or not manifest["files"]:
        raise ContractError("sealed manifest requires files")
    mode = manifest.get("replay_mode", "embedded")
    if mode not in ("embedded", "indexed"):
        raise ContractError("unsupported sealed replay mode")
    references = list(manifest["files"])
    if mode == "indexed":
        if manifest.get("replay_index") is None or manifest.get("scene_state_history") is None:
            raise ContractError("indexed mode requires replay_index and scene_state_history")
        references.extend((manifest["replay_index"], manifest["scene_state_history"]))
    if any(not isinstance(value, Mapping) or not file_reference_validator(value) for value in references):
        raise ContractError("native file reference validation failed")


def read_verified_shard(
    index: BenchReplayIndex,
    shard: BenchShard,
    reader: ArtifactReader,
    authorized_paths: Mapping[str, str],
    native_shard_validator: Callable[[bytes, BenchShard], bool],
) -> bytes:
    """Allowlisted opaque read; delegate source identity/count/scene decoding checks."""
    if shard not in index.shards:
        raise ContractError("shard belongs to another sealed index")
    artifact_id = authorized_paths.get(shard.relative_path)
    if not isinstance(artifact_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", artifact_id):
        raise ContractError("shard path is not materialized as an authorized opaque artifact")
    raw = reader.read(artifact_id)
    if not isinstance(raw, bytes) or len(raw) != shard.size_bytes or hashlib.sha256(raw).hexdigest() != shard.sha256:
        raise ContractError("sealed shard size/SHA256 mismatch")
    if not native_shard_validator(raw, shard):
        raise ContractError("native shard identity/count/scene validation failed")
    return raw
