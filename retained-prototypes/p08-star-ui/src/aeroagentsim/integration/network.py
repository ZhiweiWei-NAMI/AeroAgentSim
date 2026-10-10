"""Independent BENCH network display contracts; no network-to-Atlas rule inference."""

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .bench import BenchContext, bench_time, digest, fields, identifier, integer, number, pose_enu
from .contracts import ContractError, Vec3, freeze_json

LINK_UNITS = {
    "distance_m": "m",
    "base_path_loss_db": "dB",
    "obstruction_loss_db": "dB",
    "forward_rssi_dbm": "dBm",
    "reverse_rssi_dbm": "dBm",
    "forward_snr_db": "dB",
    "reverse_snr_db": "dB",
    "source_queue_packets": "packets",
    "destination_queue_packets": "packets",
    "source_queue_bytes": "bytes",
    "destination_queue_bytes": "bytes",
    "pending_messages": "count",
    "obstruction_volume_count": "count",
    "obstruction_volume_ids": "identifiers",
}
RECEIPT_DIGESTS = frozenset(
    (
        "accepted_scene_state_digest",
        "motion_predecessor_barrier_digest",
        "staged_node_positions_digest",
        "staged_link_attenuations_digest",
        "staged_link_obstructions_digest",
        "link_state_digest",
        "network_projection_digest",
    )
)
RECEIPT_UNITS = {
    **{key: "sha256" for key in RECEIPT_DIGESTS},
    "staged_positions_applied": "source_declared",
    "staged_position_binding": "source_declared",
    "link_state_count": "count",
    "network_model": "source_declared",
    "simulator_time_ns": "ns",
    "submitted_messages": "count",
    "delivered_messages": "count",
    "dropped_messages": "count",
    "delivered_payload_bytes_this_step": "bytes/step",
    "delivered_throughput_bps": "bps",
}


@dataclass(frozen=True)
class PropertyObservation:
    value: Any
    unit: str
    provenance: Any


@dataclass(frozen=True)
class PublicEventIdentity:
    context: BenchContext
    tick: int
    sim_time_ns: str
    sequence: int
    event_id: str
    event_digest: str
    verified_scene_digest: Optional[str] = None  # Caller evidence; not an invented source event field.

    def __post_init__(self):
        from .contracts import ns

        integer(self.tick)
        ns(self.sim_time_ns)
        integer(self.sequence)
        if self.event_id != "event.{:016d}".format(self.sequence):
            raise ContractError("public event ID/sequence mismatch")
        digest(self.event_digest)
        if self.verified_scene_digest is not None:
            digest(self.verified_scene_digest)


@dataclass(frozen=True)
class LinkProperties:
    identity: PublicEventIdentity
    link_id: str
    source_entity_id: str
    target_entity_id: str
    sensitivity_state: str  # Radio condition only, not delivery/connectivity evidence.
    properties: Mapping[str, PropertyObservation]
    provenance: Any


@dataclass(frozen=True)
class NodeGeometry:
    node_id: str
    entity_id: str
    endpoint_id: str
    position_enu_m: Vec3
    source_pose: Any


@dataclass(frozen=True)
class LinkGeometry:
    link_id: str
    source_node_id: str
    destination_node_id: str
    source_position_enu_m: Vec3
    destination_position_enu_m: Vec3
    source_pose: Any
    destination_pose: Any


@dataclass(frozen=True)
class NetworkGeometry:
    context: BenchContext
    tick: int
    sim_time_ns: str
    scene_state_digest: str
    nodes: Tuple[NodeGeometry, ...]
    links: Tuple[LinkGeometry, ...]


@dataclass(frozen=True)
class ProviderReceipt:
    context: BenchContext
    provider_id: str
    event_id: str
    tick: int
    sim_time_ns: str
    observations: Mapping[str, PropertyObservation]
    count_semantics: str = "accumulation_reset_window_unresolved"


def _named(entries, allowed, property_provenance=False):
    if not isinstance(entries, (list, tuple)):
        raise ContractError("named observations must be an array")
    values = {}
    for entry in entries:
        fields(entry, ("name", "value", "provenance") if property_provenance else ("name", "value"), "named observation")
        name = entry["name"]
        if not isinstance(name, str) or name not in allowed or name in values:
            raise ContractError("unsupported or duplicate named observation")
        values[name] = entry
    return values


def normalize_link_properties(document: Mapping[str, Any], identity: PublicEventIdentity) -> LinkProperties:
    """The owner extracts the documented v3 event document from its verified envelope."""
    fields(
        document,
        ("link_id", "source_entity_id", "target_entity_id", "state", "public_properties", "provenance"),
        "public link event",
    )
    if document["state"] not in ("above-sensitivity", "below-sensitivity"):
        raise ContractError("unsupported radio sensitivity state")
    values = _named(document["public_properties"], LINK_UNITS, True)
    observations = {}
    for name, unit in LINK_UNITS.items():
        entry = values.get(name)
        value = None if entry is None else entry["value"]
        if value is not None:
            if name == "obstruction_volume_ids":
                if not isinstance(value, str):
                    raise ContractError("obstruction IDs must arrive as a JSON-encoded string")
                try:
                    decoded = json.loads(value)
                except (ValueError, TypeError) as exc:
                    raise ContractError("invalid JSON obstruction IDs") from exc
                if not isinstance(decoded, list):
                    raise ContractError("obstruction IDs must decode to an identifier array")
                value = tuple(identifier(item) for item in decoded)
                if len(set(value)) != len(value):
                    raise ContractError("duplicate obstruction volume ID")
            elif unit in ("packets", "bytes", "count"):
                # Some public property encoders represent integer counts as floats.
                numeric = number(value)
                if numeric < 0 or not numeric.is_integer():
                    raise ContractError("queue/count observation must be nonnegative integral")
            else:
                numeric = number(value)
                if name == "distance_m" and numeric < 0:
                    raise ContractError("distance must be nonnegative")
        provenance = None if entry is None else freeze_json(entry["provenance"])
        observations[name] = PropertyObservation(freeze_json(value), unit, provenance)
    count, volumes = observations["obstruction_volume_count"].value, observations["obstruction_volume_ids"].value
    if count is not None and volumes is not None and count != len(volumes):
        raise ContractError("obstruction count/identifier disagreement")
    return LinkProperties(
        identity,
        identifier(document["link_id"]),
        identifier(document["source_entity_id"]),
        identifier(document["target_entity_id"]),
        document["state"],
        _freeze_observations(observations),
        freeze_json(document["provenance"]),
    )


def _freeze_observations(observations):
    from types import MappingProxyType

    return MappingProxyType(dict(observations))


def normalize_network_geometry(
    frame: Mapping[str, Any], context: BenchContext, node_registry: Mapping[str, Tuple[str, str]]
) -> NetworkGeometry:
    fields(frame, ("at", "scene_state_digest", "nodes", "links"), "PublicNetworkFrame")
    tick, time_ns = bench_time(frame["at"])
    if not isinstance(frame["nodes"], (list, tuple)) or not isinstance(frame["links"], (list, tuple)):
        raise ContractError("network geometry arrays must be explicit")
    nodes, links = {}, {}
    for node in frame["nodes"]:
        fields(node, ("node_id", "entity_id", "endpoint_id", "pose"), "network node")
        node_id, entity_id, endpoint_id = (identifier(node[key]) for key in ("node_id", "entity_id", "endpoint_id"))
        if node_id in nodes or node_registry.get(node_id) != (entity_id, endpoint_id):
            raise ContractError("duplicate node or canonical node/entity/endpoint alias mismatch")
        nodes[node_id] = NodeGeometry(node_id, entity_id, endpoint_id, pose_enu(node["pose"]), freeze_json(node["pose"]))
    for link in frame["links"]:
        fields(
            link,
            ("link_id", "source_node_id", "destination_node_id", "source_pose", "destination_pose"),
            "network link geometry",
        )
        link_id, source, destination = (identifier(link[key]) for key in ("link_id", "source_node_id", "destination_node_id"))
        if link_id in links or source not in nodes or destination not in nodes:
            raise ContractError("duplicate link or undeclared link node")
        links[link_id] = LinkGeometry(
            link_id,
            source,
            destination,
            pose_enu(link["source_pose"]),
            pose_enu(link["destination_pose"]),
            freeze_json(link["source_pose"]),
            freeze_json(link["destination_pose"]),
        )
    return NetworkGeometry(
        context, tick, time_ns, digest(frame["scene_state_digest"]), tuple(nodes.values()), tuple(links.values())
    )


def normalize_provider_receipt(receipt: Mapping[str, Any], context: BenchContext) -> ProviderReceipt:
    fields(receipt, ("provider_id", "event_id", "time", "payload_schema_id", "payload"), "provider receipt")
    if receipt["payload_schema_id"] != "ns3.state.v4":
        raise ContractError("unsupported provider receipt schema")
    tick, time_ns = bench_time(receipt["time"])
    if receipt["event_id"] != "state." + str(tick):
        raise ContractError("receipt event ID/tick mismatch")
    values = _named(receipt["payload"], RECEIPT_UNITS)
    observations = {}
    for name, unit in RECEIPT_UNITS.items():
        value = None if name not in values else values[name]["value"]
        if value is not None:
            if name in RECEIPT_DIGESTS:
                digest(value)
            elif name == "simulator_time_ns":
                from .contracts import ns

                if type(value) is int:
                    value = str(integer(value))
                ns(value)
            elif unit in ("count", "bytes/step"):
                integer(value)
            elif unit == "bps" and number(value) < 0:
                raise ContractError("throughput must be nonnegative")
        observations[name] = PropertyObservation(freeze_json(value), unit, freeze_json({"provider_receipt": True}))
    return ProviderReceipt(
        context,
        identifier(receipt["provider_id"]),
        identifier(receipt["event_id"]),
        tick,
        time_ns,
        _freeze_observations(observations),
    )


@dataclass(frozen=True)
class LinkJoinResult:
    status: str
    reason: Optional[str]
    geometry: Optional[LinkGeometry] = None
    properties: Optional[LinkProperties] = None


def join_link_evidence(geometry: NetworkGeometry, properties: LinkProperties) -> LinkJoinResult:
    identity = properties.identity
    if identity.verified_scene_digest is None:
        return LinkJoinResult("pending", "source_event_scene_link_unverified")
    if (geometry.context, geometry.tick, geometry.sim_time_ns, geometry.scene_state_digest) != (
        identity.context,
        identity.tick,
        identity.sim_time_ns,
        identity.verified_scene_digest,
    ):
        return LinkJoinResult("pending", "run_scenario_epoch_tick_stage_disagreement")
    nodes = {node.node_id: node for node in geometry.nodes}
    link = next((item for item in geometry.links if item.link_id == properties.link_id), None)
    if link is None or (nodes[link.source_node_id].entity_id, nodes[link.destination_node_id].entity_id) != (
        properties.source_entity_id,
        properties.target_entity_id,
    ):
        return LinkJoinResult("pending", "link_entity_alias_disagreement")
    return LinkJoinResult("joined", None, link, properties)
