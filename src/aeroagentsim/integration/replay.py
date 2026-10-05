"""Indexed, read-only replay. Only caller-authorized opaque artifact IDs are read."""

import hashlib
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping, Optional, Protocol, Tuple

from .contracts import REPLAY_SCHEMA, ContractError, Gap, ObservationFrame, freeze_json, ns
from .normalization import decode_json, normalize_frame, require_keys, run_identity, token


class ArtifactReader(Protocol):
    def read(self, artifact_id: str) -> bytes:
        """Resolve an authorized opaque ID, never a caller-supplied file path or URL."""


class MappingArtifactReader:
    def __init__(self, artifacts: Mapping[str, bytes]):
        if any(not isinstance(value, bytes) for value in artifacts.values()):
            raise ContractError("artifacts must be immutable bytes")
        self._artifacts = MappingProxyType(dict(artifacts))

    def read(self, artifact_id: str) -> bytes:
        return self._artifacts[artifact_id]


@dataclass(frozen=True)
class SeekResult:
    requested_time_ns: str
    mode: str
    status: str  # exact, previous, not_indexed, before_start, after_end, gap
    frame: Optional[ObservationFrame] = None
    gap: Optional[Gap] = None


class ReadOnlyReplay:
    """Validate a small replay eagerly, then seek immutable indexed observations.

    A production BENCH reader/projector remains private and is not implemented here.
    """

    def __init__(self, manifest_bytes: bytes, reader: ArtifactReader):
        data = decode_json(manifest_bytes)
        require_keys(data, ("schema", "run", "engine_origin_ns", "bounds", "gaps", "index", "provenance"), "replay index")
        if data["schema"] != REPLAY_SCHEMA:
            raise ContractError("unsupported replay index schema")
        if not isinstance(data["provenance"], dict):
            raise ContractError("replay provenance must be a mapping")
        self.provenance = freeze_json(data["provenance"])
        self.run = run_identity(data["run"])
        self.engine_origin_ns = data["engine_origin_ns"]
        ns(self.engine_origin_ns)
        require_keys(data["bounds"], ("start_ns", "end_ns"), "replay bounds")
        self.start_ns, self.end_ns = data["bounds"]["start_ns"], data["bounds"]["end_ns"]
        if not ns(self.engine_origin_ns) <= ns(self.start_ns) <= ns(self.end_ns):
            raise ContractError("invalid replay bounds")
        if not isinstance(data["gaps"], list) or not isinstance(data["index"], list) or not data["index"]:
            raise ContractError("replay requires an index and explicit gap list")
        gaps = []
        for item in data["gaps"]:
            require_keys(item, ("start_ns", "end_ns", "reason"), "replay gap")
            if not ns(self.start_ns) <= ns(item["start_ns"]) < ns(item["end_ns"]) <= ns(self.end_ns):
                raise ContractError("gap must be nonempty and inside bounds")
            gaps.append(Gap(item["start_ns"], item["end_ns"], token(item["reason"], "gap reason")))
        if any(ns(a.end_ns) > ns(b.start_ns) for a, b in zip(gaps, gaps[1:])):
            raise ContractError("gaps must be ordered and nonoverlapping")
        self.gaps = tuple(gaps)
        frames = []
        artifact_ids = set()
        lifetimes = {}
        retired_ids = set()
        previous_ids = set()
        for item in data["index"]:
            require_keys(
                item, ("artifact_id", "sha256", "frame_seq", "sim_time_ns", "stage_evidence_key"), "frame index entry"
            )
            artifact_id = item["artifact_id"]
            if (
                not isinstance(artifact_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", artifact_id)
                or artifact_id in artifact_ids
            ):
                raise ContractError("artifact IDs must be unique opaque tokens")
            artifact_ids.add(artifact_id)
            raw = reader.read(artifact_id)
            if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ContractError("artifact hash mismatch")
            frame = normalize_frame(raw, self.run, self.engine_origin_ns)
            if (frame.key.frame_seq, frame.sim_time_ns, frame.key.stage_evidence_key) != (
                item["frame_seq"],
                item["sim_time_ns"],
                item["stage_evidence_key"],
            ) or type(item["frame_seq"]) is not int:
                raise ContractError("frame index identity does not match artifact")
            time = ns(frame.sim_time_ns)
            if not ns(self.start_ns) <= time <= ns(self.end_ns) or any(gap.contains(frame.sim_time_ns) for gap in gaps):
                raise ContractError("indexed frame is outside bounds or inside a declared gap")
            if frames and (time <= ns(frames[-1].sim_time_ns) or frame.key.frame_seq <= frames[-1].key.frame_seq):
                raise ContractError("index times and frame sequences must increase strictly")
            # Complete frames may omit an entity only after its declared lifetime ends.
            ids = {entity.entity_id for entity in frame.entities}
            if retired_ids.intersection(ids):
                raise ContractError("entity_id reused after retirement within one run epoch")
            for entity in frame.entities:
                lifetime = (entity.kind, entity.born_ns, entity.ended_ns, entity.body)
                if entity.entity_id in lifetimes and lifetimes[entity.entity_id] != lifetime:
                    raise ContractError("entity kind/lifetime/physical body changed within one run epoch")
                lifetimes[entity.entity_id] = lifetime
            for entity_id in previous_ids - ids:
                end = lifetimes[entity_id][2]
                if end is None or ns(end) > time:
                    raise ContractError("active entity omitted from complete observation frame")
                retired_ids.add(entity_id)
            previous_ids = ids
            previous_time = ns(frames[-1].sim_time_ns) if frames else ns(self.start_ns) - 1
            gaps_before = tuple(gap for gap in gaps if previous_time < ns(gap.end_ns) <= time)
            frames.append(replace(frame, gaps_before=gaps_before))
        if frames[0].sim_time_ns != self.start_ns or frames[-1].sim_time_ns != self.end_ns:
            raise ContractError("bounds must equal the first and last indexed frame times")
        self.frames: Tuple[ObservationFrame, ...] = tuple(frames)
        self._times = tuple(ns(frame.sim_time_ns) for frame in frames)

    def seek(self, time_ns: str, mode: str = "exact") -> SeekResult:
        time = ns(time_ns)
        if mode not in ("exact", "previous"):
            raise ContractError("seek mode must be exact or previous")
        if time < ns(self.start_ns):
            return SeekResult(time_ns, mode, "before_start")
        if time > ns(self.end_ns):
            return SeekResult(time_ns, mode, "after_end")
        for gap in self.gaps:
            if gap.contains(time_ns):
                return SeekResult(time_ns, mode, "gap", gap=gap)
        index = bisect_left(self._times, time)
        if index < len(self._times) and self._times[index] == time:
            return SeekResult(time_ns, mode, "exact", self.frames[index])
        if mode == "exact":
            return SeekResult(time_ns, mode, "not_indexed")
        frame = self.frames[bisect_right(self._times, time) - 1]
        # Do not carry a pre-gap frame across an unavailable interval.
        if any(ns(frame.sim_time_ns) < ns(gap.end_ns) <= time for gap in self.gaps):
            return SeekResult(time_ns, mode, "gap")
        return SeekResult(time_ns, mode, "previous", frame)
