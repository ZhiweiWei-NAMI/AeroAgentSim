"""Independent projection of documented BENCH fields; no private implementation.

This module validates the known boundary fields. It is not a replacement for the
complete native schema validator, digest verifier, or BENCH replay reader.
"""

import hashlib
import math
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Optional, Tuple

from .contracts import ContractError, ObservationFrame, RunIdentity, freeze_json, ns
from .normalization import canonical_bytes, normalize_frame, token


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]*", value):
        raise ContractError("invalid BENCH identifier")
    return value


def digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError("invalid BENCH SHA256")
    return value


def integer(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError("invalid BENCH integer")
    return value


def number(value: Any) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ContractError("invalid finite BENCH number")
    return float(value)


def fields(value: Any, required: Tuple[str, ...], name: str) -> None:
    if not isinstance(value, Mapping) or any(key not in value for key in required):
        raise ContractError("missing documented " + name + " fields")


@dataclass(frozen=True)
class BenchContext:
    run: RunIdentity
    scenario_digest: str
    engine_origin_ns: str

    def __post_init__(self):
        digest(self.run.run_id)
        digest(self.scenario_digest)
        ns(self.engine_origin_ns)


def bench_time(value: Any) -> Tuple[int, str]:
    fields(value, ("tick", "sim_time_ns"), "SimulationTime")
    return integer(value["tick"]), str(integer(value["sim_time_ns"]))


def pose_enu(pose: Any) -> Tuple[float, float, float]:
    fields(pose, ("position", "orientation_enu", "orientation_ned"), "pose")
    position = pose["position"]
    fields(position, ("enu", "ned", "ecef", "wgs84", "geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m"), "coordinate")
    fields(position["enu"], ("east_m", "north_m", "up_m"), "ENU position")
    enu = tuple(number(position["enu"][key]) for key in ("east_m", "north_m", "up_m"))
    fields(position["ned"], ("north_m", "east_m", "down_m"), "NED position")
    ned = tuple(number(position["ned"][key]) for key in ("east_m", "north_m", "down_m"))
    if any(not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) for a, b in zip(enu, (ned[0], ned[1], -ned[2]))):
        raise ContractError("ENU/NED position disagreement")
    for frame in ("orientation_enu", "orientation_ned"):
        fields(pose[frame], ("qw", "qx", "qy", "qz"), "named quaternion")
        values = [number(pose[frame][key]) for key in ("qw", "qx", "qy", "qz")]
        if not math.isclose(sum(value * value for value in values), 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ContractError("quaternion is not normalized")
    for key in ("geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m"):
        number(position[key])
    fields(position["ecef"], ("x_m", "y_m", "z_m"), "ECEF position")
    for key in ("x_m", "y_m", "z_m"):
        number(position["ecef"][key])
    fields(position["wgs84"], ("longitude_deg", "latitude_deg", "ellipsoid_height_m"), "WGS84 position")
    longitude = number(position["wgs84"]["longitude_deg"])
    latitude = number(position["wgs84"]["latitude_deg"])
    number(position["wgs84"]["ellipsoid_height_m"])
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ContractError("WGS84 coordinates outside degree bounds")
    return enu


def normalize_bench_scene(
    scene: Mapping[str, Any],
    resolved_entities: Tuple[Mapping[str, Any], ...],
    context: BenchContext,
    frame_seq: int,
    neutral_kinds: Mapping[str, str],
    lifetimes: Mapping[str, Tuple[str, Optional[str]]],
    valid_until_ns: Mapping[str, str],
    barrier_validator: Callable[[Mapping[str, Any]], bool],
    physical_bodies: Optional[Mapping[str, Mapping[str, Any]]] = None,
    digest_verifier: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    source_cursors: Optional[Mapping[str, int]] = None,
) -> ObservationFrame:
    """Project a caller-materialized scene; caller policies remain explicit.

    StageBarrier internals and source digest serialization are unexpanded here, so
    callers supply their validators. Body geometry, lifetimes, freshness, and kind
    classification are declared inputs, never inferred from model names or IDs.
    """
    scene = freeze_json(scene)
    fields(
        scene,
        (
            "schema_version",
            "run_id",
            "scenario_digest",
            "at",
            "declared_entity_ids",
            "samples",
            "stage_barrier",
            "contribution_digests",
            "previous_scene_state_digest",
            "scene_state_digest",
        ),
        "SceneState",
    )
    if scene["schema_version"] != "aero-bench.scene-state/v1":
        raise ContractError("unsupported SceneState schema")
    if digest(scene["run_id"]) != context.run.run_id or digest(scene["scenario_digest"]) != context.scenario_digest:
        raise ContractError("BENCH run/scenario identity mismatch")
    tick, time_ns = bench_time(scene["at"])
    if tick < 1:
        raise ContractError("SceneState starts at tick 1; initial pose is not observed tick 0")
    integer(frame_seq)
    if not isinstance(scene["samples"], (list, tuple)) or not scene["samples"]:
        raise ContractError("SceneState requires samples")
    if not isinstance(scene["declared_entity_ids"], (list, tuple)) or not isinstance(
        scene["contribution_digests"], (list, tuple)
    ):
        raise ContractError("invalid SceneState declared IDs/contribution digests")
    scene_digest = digest(scene["scene_state_digest"])
    digest(scene["previous_scene_state_digest"])
    for value in scene["contribution_digests"]:
        digest(value)
    if not isinstance(scene["stage_barrier"], Mapping) or not barrier_validator(scene["stage_barrier"]):
        raise ContractError("motion stage barrier validation failed")
    if digest_verifier is not None and not digest_verifier(scene):
        raise ContractError("source scene/sample digest verification failed")
    declared = tuple(identifier(value) for value in scene["declared_entity_ids"])
    if len(set(declared)) != len(declared):
        raise ContractError("duplicate declared entity ID")
    registry = {}
    for raw_entity in resolved_entities:
        entity = freeze_json(raw_entity)
        fields(
            entity,
            (
                "entity_id",
                "kind",
                "owner_kind",
                "owner_id",
                "source_provider_id",
                "authority_kind",
                "state",
                "model_asset_id",
                "initial_pose",
                "selected_launch_override",
            ),
            "ResolvedEntity",
        )
        entity_id = identifier(entity["entity_id"])
        if entity_id in registry:
            raise ContractError("duplicate canonical entity ID")
        token(entity["kind"], "source kind")  # Native EntityKind members remain an external schema boundary.
        token(entity["authority_kind"], "authority kind")
        identifier(entity["owner_id"])
        if entity["owner_kind"] not in ("scenario", "provider") or type(entity["selected_launch_override"]) is not bool:
            raise ContractError("invalid canonical entity owner/launch declaration")
        pose_enu(entity["initial_pose"])  # Validate configuration without generating a tick-0 observation.
        identifier(entity["model_asset_id"])
        if entity["source_provider_id"] is not None:
            identifier(entity["source_provider_id"])
        if entity["state"] not in ("static", "dynamic"):
            raise ContractError("unsupported declared static/dynamic state")
        registry[entity_id] = entity
    if (
        set(registry) != set(declared)
        or set(neutral_kinds) != set(declared)
        or set(lifetimes) != set(declared)
        or set(valid_until_ns) != set(declared)
    ):
        raise ContractError("canonical entities, classification, lifetime and validity bindings must match exactly")
    if physical_bodies is not None and not set(physical_bodies).issubset(registry):
        raise ContractError("physical geometry belongs to an undeclared entity")
    normalized = []
    seen = set()
    for sample in scene["samples"]:
        fields(
            sample,
            (
                "schema_version",
                "run_id",
                "scenario_digest",
                "at",
                "stage",
                "entity_id",
                "provider_id",
                "sample_kind",
                "pose",
                "linear_velocity_enu",
                "linear_velocity_ned",
                "sample_digest",
            ),
            "StateSample",
        )
        entity_id = identifier(sample["entity_id"])
        if entity_id not in registry or entity_id in seen:
            raise ContractError("sample set disagrees with declared canonical entities")
        seen.add(entity_id)
        entity = registry[entity_id]
        if sample["schema_version"] != "aero-bench.state-sample/v1" or sample["stage"] != "motion":
            raise ContractError("unsupported state sample schema/stage")
        if (
            digest(sample["run_id"]) != context.run.run_id
            or digest(sample["scenario_digest"]) != context.scenario_digest
            or bench_time(sample["at"]) != (tick, time_ns)
        ):
            raise ContractError("sample run/scenario/tick/time disagreement")
        provider_id = identifier(sample["provider_id"])
        if sample["sample_kind"] != entity["state"]:
            raise ContractError("sample static/dynamic kind disagrees with canonical entity")
        if entity["state"] == "static":
            if provider_id != "scenario.compiler" or entity["source_provider_id"] is not None:
                raise ContractError("static entity authority must be scenario.compiler")
        elif entity["source_provider_id"] != provider_id:
            raise ContractError("dynamic sample provider disagrees with declared provider")
        position = pose_enu(sample["pose"])
        enu, ned = sample["linear_velocity_enu"], sample["linear_velocity_ned"]
        fields(enu, ("frame_id", "east_mps", "north_mps", "up_mps"), "ENU velocity")
        fields(ned, ("frame_id", "north_mps", "east_mps", "down_mps"), "NED velocity")
        if enu["frame_id"] != "ENU" or ned["frame_id"] != "NED":
            raise ContractError("velocity coordinate frame mismatch")
        velocity = tuple(number(enu[key]) for key in ("east_mps", "north_mps", "up_mps"))
        redundant = tuple(number(ned[key]) for key in ("east_mps", "north_mps", "down_mps"))
        if any(
            not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
            for a, b in zip(velocity, (redundant[0], redundant[1], -redundant[2]))
        ):
            raise ContractError("ENU/NED velocity disagreement")
        digest(sample["sample_digest"])
        if sample.get("mode") is not None:
            token(sample["mode"], "mode")
        if sample.get("armed") is not None and type(sample["armed"]) is not bool:
            raise ContractError("armed must be Boolean or absent/null")
        angular = sample.get("angular_velocity_body")
        if angular is not None:
            fields(angular, ("frame_id", "x_radps", "y_radps", "z_radps"), "body angular velocity")
            if angular["frame_id"] != "body":
                raise ContractError("angular velocity must preserve the body frame")
            for key in ("x_radps", "y_radps", "z_radps"):
                number(angular[key])
        born, ended = lifetimes[entity_id]
        normalized.append(
            {
                "entity_id": entity_id,
                "kind": neutral_kinds[entity_id],
                "born_ns": born,
                "ended_ns": ended,
                "sample_time_ns": time_ns,
                "valid_until_ns": valid_until_ns[entity_id],
                "position_enu_m": position,
                "velocity_enu_mps": velocity,
                "body": None if physical_bodies is None else physical_bodies.get(entity_id),
                "unknown_reasons": [],
                "provenance": {
                    "provider_id": provider_id,
                    "sample_digest": sample["sample_digest"],
                    "source_kind": entity["kind"],
                    "model_asset_id": entity["model_asset_id"],
                    "source_provider_id": entity["source_provider_id"],
                    "pose": sample["pose"],  # Preserve named quaternion and distinct altitude quantities.
                    "optional_state": {
                        key: sample.get(key)
                        for key in ("angular_velocity_body", "mode", "armed", "battery", "health", "contacts", "attributes")
                    },
                    "mobile_body_mapping": (
                        "unresolved" if physical_bodies is None or entity_id not in physical_bodies else "caller_declared"
                    ),
                },
            }
        )
    if seen != set(declared):
        raise ContractError("sample set disagrees with declared canonical entities")
    if source_cursors is not None:
        fields(source_cursors, ("after_transition", "after_scene_tick", "after_event_sequence"), "source cursor tuple")
        integer(source_cursors["after_transition"], -1)
        integer(source_cursors["after_scene_tick"])
        integer(source_cursors["after_event_sequence"], -1)
    data = {
        "schema": "aeroagentsim.observation/v1",
        "run": {
            "attachment_id": context.run.attachment_id,
            "run_id": context.run.run_id,
            "run_epoch": context.run.run_epoch,
            "manifest_revision": context.run.manifest_revision,
        },
        "frame_seq": frame_seq,
        "stage_evidence_key": scene_digest,
        "sim_time_ns": time_ns,
        "engine_origin_ns": context.engine_origin_ns,
        "coordinate_frame": "ENU",
        "units": {"position": "m", "velocity": "m/s", "time": "ns"},
        "entities": normalized,
        "provenance": {
            "source_schema": scene["schema_version"],
            "source_tick": tick,
            "scenario_digest": context.scenario_digest,
            "scene_state_digest": scene_digest,
            "previous_scene_state_digest": scene["previous_scene_state_digest"],
            "stage_barrier": scene["stage_barrier"],
            "contribution_digests": scene["contribution_digests"],
            "digest_verification": "caller_verified" if digest_verifier is not None else "format_and_identity_only",
            "motion_barrier_verification": "caller_validator",
            "source_cursors": source_cursors,
            "validity_policy": "caller_declared_exclusive_expiry",
            "lifetime_policy": "caller_declared",
        },
    }
    return normalize_frame(canonical_bytes(data), context.run, context.engine_origin_ns)


@dataclass(frozen=True)
class BenchStreamCursors:
    """Three source cursors, bound to one attachment/run/epoch/manifest."""

    run: RunIdentity
    after_transition: int = -1
    after_scene_tick: int = 0
    after_event_sequence: int = -1
    canonical_sequence: int = 0
    last_fingerprints: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self):
        digest(self.run.run_id)
        integer(self.after_transition, -1)
        integer(self.after_scene_tick)
        integer(self.after_event_sequence, -1)
        integer(self.canonical_sequence)

    def query(self) -> Mapping[str, int]:
        return freeze_json(
            {
                "after_transition": self.after_transition,
                "after_scene_tick": self.after_scene_tick,
                "after_event_sequence": self.after_event_sequence,
            }
        )

    def process(
        self,
        family: str,
        envelope: Mapping[str, Any],
        processor: Callable[[Mapping[str, Any]], Any],
        expected_run: RunIdentity,
        sse_id: Optional[str] = None,
    ) -> Tuple["BenchStreamCursors", Any]:
        """Advance only after successful validation/processing; no network activity.

        The injected processor must validate the complete family payload. Gaps require
        explicit resynchronization rather than silently moving past unseen evidence.
        """
        if expected_run != self.run:
            raise ContractError("stream attachment/run/epoch/manifest mismatch")
        families = {
            "run.transition": ("aero-bench.run-transition-event/v1", "transition", "sequence", "after_transition"),
            "scene.state": ("aero-bench.scene-state-stream-event/v1", "scene_state", None, "after_scene_tick"),
            "run.event": ("aero-bench.public-run-event-stream-event/v1", "event", "sequence", "after_event_sequence"),
        }
        if family not in families:
            raise ContractError("unsupported SSE event family")
        schema, payload_key, sequence_field, cursor_field = families[family]
        fields(envelope, ("schema_version", "run_id", payload_key), "SSE envelope")
        if envelope["schema_version"] != schema or digest(envelope["run_id"]) != self.run.run_id:
            raise ContractError("SSE schema/run mismatch")
        payload = envelope[payload_key]
        if sequence_field is None:
            fields(payload, ("at", "run_id"), "SceneState stream identity")
            sequence, _ = bench_time(payload["at"])
            if sequence < 1 or digest(payload["run_id"]) != self.run.run_id:
                raise ContractError("invalid SceneState stream identity")
            expected_id = "scene." + str(sequence)
        else:
            fields(payload, (sequence_field,), "source event sequence")
            sequence = integer(payload[sequence_field])
            expected_id = "transition." + str(sequence) if family == "run.transition" else "event.{:016d}".format(sequence)
            if family == "run.event":
                fields(payload, ("run_id", "event_id", "event_digest", "at"), "public event identity")
                if digest(payload["run_id"]) != self.run.run_id or payload["event_id"] != expected_id:
                    raise ContractError("public event run/ID mismatch")
                digest(payload["event_digest"])
                bench_time(payload["at"])
        if sse_id is not None and sse_id != expected_id:
            raise ContractError("SSE wire ID disagrees with validated family sequence")
        current = getattr(self, cursor_field)
        fingerprint = hashlib.sha256(canonical_bytes(envelope)).hexdigest()
        fingerprints = dict(self.last_fingerprints)
        if sequence <= current:
            if sequence == current and fingerprints.get(family) not in (None, fingerprint):
                raise ContractError("conflicting duplicate SSE evidence")
            return self, None
        if sequence != current + 1:
            raise ContractError("source cursor gap; explicit resynchronization required")
        result = processor(freeze_json(envelope))
        fingerprints[family] = fingerprint
        return (
            replace(
                self,
                **{
                    cursor_field: sequence,
                    "canonical_sequence": self.canonical_sequence + 1,
                    "last_fingerprints": tuple(sorted(fingerprints.items())),
                },
            ),
            result,
        )
