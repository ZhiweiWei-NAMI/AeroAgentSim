"""Validate the complete mutation before touching the native simulator."""

import math

from .wire import fields, integer, number, string

MAX_NS = 2**63 - 1
MAX_NODES = 128
DEFAULT_CHANNEL = {
    "number": 36,
    "width_mhz": 20,
    "tx_power_dbm": 20.0,
    "rx_sensitivity_dbm": -90.0,
    "noise_figure_db": 7.0,
}
DEFAULT_PROPAGATION = {"exponent": 3.0}


def bounded(value, name, low, high):
    number(value, name)
    if not low <= value <= high:
        raise ValueError(f"{name} outside [{low}, {high}]")
    return value


def identity(value, name):
    string(value, name)
    if len(value.encode("utf-8")) > 256:
        raise ValueError(f"{name} exceeds 256 UTF-8 bytes")
    return value


def vector(value, name):
    if type(value) is not list or len(value) != 3:
        raise ValueError(f"{name} must contain three ENU coordinates")
    return [bounded(v, name, -1e9, 1e9) for v in value]


def reset(payload):
    fields(payload, ("seed", "nodes"), ("channel", "propagation", "scene_volumes"))
    # ns-3 requires a nonzero uint32 seed. No silent remapping of host seeds.
    seed = integer(payload["seed"], "seed", 1, 2**32 - 1)
    nodes = payload["nodes"]
    if type(nodes) is not list or not 2 <= len(nodes) <= MAX_NODES:
        raise ValueError(f"nodes must contain 2..{MAX_NODES} nodes")
    normalized, seen = [], set()
    for node in nodes:
        fields(node, ("id", "position_enu"))
        name = identity(node["id"], "node id")
        if name in seen:
            raise ValueError("duplicate node id")
        seen.add(name)
        normalized.append(
            {"id": name, "position_enu": vector(node["position_enu"], "position")}
        )
    channel = DEFAULT_CHANNEL.copy()
    if "channel" in payload:
        fields(payload["channel"], (), tuple(channel))
        channel.update(payload["channel"])
    number_ = integer(channel["number"], "channel number", 36, 144)
    # Explicit 5 GHz / 20 MHz profile, not an arbitrary silently mapped channel.
    if number_ not in (*range(36, 65, 4), *range(100, 145, 4)):
        raise ValueError("unsupported 5 GHz channel")
    integer(channel["width_mhz"], "width_mhz", 20, 20)
    bounded(channel["tx_power_dbm"], "tx_power_dbm", -30, 60)
    bounded(channel["rx_sensitivity_dbm"], "rx_sensitivity_dbm", -120, -20)
    bounded(channel["noise_figure_db"], "noise_figure_db", 0, 30)
    propagation = DEFAULT_PROPAGATION.copy()
    if "propagation" in payload:
        fields(payload["propagation"], (), ("exponent", "reference_loss_db"))
        propagation.update(payload["propagation"])
    bounded(propagation["exponent"], "exponent", 1, 8)
    if "reference_loss_db" not in propagation:
        frequency = (5000 + 5 * number_) * 1e6
        propagation["reference_loss_db"] = 20 * math.log10(
            4 * math.pi * frequency / 299792458
        )
    bounded(propagation["reference_loss_db"], "reference_loss_db", 0, 200)
    volumes = payload.get("scene_volumes", [])
    if type(volumes) is not list or len(volumes) > 1024:
        raise ValueError("scene_volumes must be an array of at most 1024 volumes")
    boxes = []
    for box in volumes:
        fields(box, ("min_enu", "max_enu", "loss_db"))
        low, high = vector(box["min_enu"], "min_enu"), vector(box["max_enu"], "max_enu")
        if any(a >= b for a, b in zip(low, high)):
            raise ValueError("volume must have positive extent on all axes")
        boxes.append(
            {
                "min_enu": low,
                "max_enu": high,
                "loss_db": bounded(box["loss_db"], "loss_db", 0, 1000),
            }
        )
    return {
        "seed": seed,
        "nodes": normalized,
        "channel": channel,
        "propagation": propagation,
        "scene_volumes": boxes,
    }


def advance(payload, now, nodes):
    fields(payload, ("to_sim_ns",), ("mobility",))
    target = integer(payload["to_sim_ns"], "to_sim_ns", now + 1, MAX_NS)
    updates = payload.get("mobility", [])
    if type(updates) is not list or len(updates) > 8192:
        raise ValueError("mobility must be an array of at most 8192 updates")
    seen, validated = set(), []
    for update in updates:
        fields(update, ("node", "sim_ns", "position"))
        node = identity(update["node"], "node")
        if node not in nodes:
            raise ValueError("unknown mobility node")
        at = integer(update["sim_ns"], "mobility sim_ns", now, target)
        if (node, at) in seen:
            raise ValueError("duplicate node/time mobility update")
        seen.add((node, at))
        validated.append((at, nodes[node], vector(update["position"], "position")))
    return target, sorted(validated)


def command(payload, now, nodes):
    fields(payload, ("action", "params"))
    if payload["action"] != "send":
        raise ValueError("unsupported action")
    params = payload["params"]
    fields(params, ("packet_id", "src", "dst", "size", "payload_ref"), ("lifetime_ns",))
    identity(params["packet_id"], "packet_id")
    for field in ("src", "dst"):
        identity(params[field], field)
        if params[field] not in nodes:
            raise ValueError(f"unknown {field} node")
    if params["src"] == params["dst"]:
        raise ValueError("send requires distinct nodes")
    integer(params["size"], "size", 1, 61440)
    identity(params["payload_ref"], "payload_ref")
    lifetime = integer(
        params.get("lifetime_ns", 1_000_000_000), "lifetime_ns", 1, 60_000_000_000
    )
    if now + lifetime > MAX_NS:
        raise ValueError("packet lifetime exceeds native time range")
    return {**params, "lifetime_ns": lifetime}
