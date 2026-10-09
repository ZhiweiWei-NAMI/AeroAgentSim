"""Reproduce explicitly authored P5 model scenarios from read-only AeroGraph."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.authoring.inputs import configured_ontology
from aeroagentsim.integrations.aerograph import Selection, compile_registry

ROOT = Path(__file__).resolve().parent
SECOND = 1_000_000_000
POS = "he.aircraft.position_enu_m"
NUMBER = {"type": "number"}
STRING = {"type": "string"}
INTEGER = {"type": "integer", "minimum": 0}
VECTOR = {"type": "vector", "length": 3, "items": NUMBER}


def record(
    members: dict[str, Any], required: list[str] | None = None, extra: bool = False
) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": list(members) if required is None else required,
        "extra": extra,
    }


def field(
    identity: str, type_id: str, schema: dict[str, Any], unit: str, frame: bool = False
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "role": "observation",
        "unit": unit,
        "time_semantics": "explicit canonical model computation and half-open validity",
    }
    if frame:
        metadata.update(frame="enu", transform_revision="packs.local/v1")
    return {"id": identity, "type": type_id, "schema": schema, "metadata": metadata}


def base(identity: str) -> dict[str, Any]:
    return {
        "format": "aeroagentsim.scenario/v1",
        "id": identity,
        "registry": {
            "snapshot": "aerograph.snapshot.json",
            "types": [
                {
                    "id": "aas:Custodian",
                    "parents": ["oo:ModelObject"],
                    "abstract": True,
                },
                {
                    "id": "aas:Carrier",
                    "parents": ["oo:UAV", "aas:Custodian"],
                    "abstract": False,
                },
                {
                    "id": "aas:Facility",
                    "parents": ["oo:Facility", "aas:Custodian"],
                    "abstract": False,
                },
                {"id": "aas:Parcel", "parents": ["oo:ModelObject"], "abstract": False},
                {"id": "aas:Camera", "parents": ["oo:ModelObject"], "abstract": False},
                {
                    "id": "aas:InspectionObservation",
                    "parents": ["oo:ObservationRecord"],
                    "abstract": False,
                },
            ],
            "fields": [],
            "messages": [],
            "relations": [],
            "field_metadata": {
                POS: {"frame": "enu", "transform_revision": "packs.local/v1"}
            },
        },
        "entities": [],
        "bindings": {"rules": [], "lifecycle": [], "relations": [], "commands": []},
        "engines": {},
        "presentation": [
            {
                "typeId": "aas:Carrier",
                "positionField": POS,
                "frame": "enu",
                "visual": {"kind": "marker", "color": "#50c9ff", "scale": 1.5},
            }
        ],
        "run": {
            "seed": 42,
            "until_ns": 180 * SECOND,
            "advance_ns": 10 * SECOND,
            "pacing": "fast",
        },
        "outputs": {"durability": "flush"},
    }


def motion(d: dict[str, Any], count: int) -> None:
    d["registry"]["fields"] += [
        field("packs.velocity", "oo:UAV", VECTOR, "m/s", True),
        field("packs.energy", "oo:UAV", NUMBER, "J"),
    ]
    result = record({"entity": STRING, "position": VECTOR, "reason": STRING}, [])
    for kind in ("move_to", "hold", "stop"):
        members = {
            "entity": STRING,
            **({"target": VECTOR} if kind == "move_to" else {}),
        }
        d["registry"]["messages"].append(
            {
                "id": f"packs.motion.{kind}",
                "kind": "command",
                "schema": record(members),
                "result_schema": result,
                "feedback_schema": record(
                    {
                        "entity": STRING,
                        "position": VECTOR,
                        "velocity": VECTOR,
                        "energy_j": NUMBER,
                    },
                    [],
                    True,
                ),
                "cancel_support": True,
            }
        )
    d["registry"]["messages"].append(
        {
            "id": "packs.motion.arrived",
            "kind": "event",
            "schema": record({"entity": STRING, "position": VECTOR}),
        }
    )
    d["entities"] += [
        {
            "id": f"carrier-{i + 1}",
            "type": "aas:Carrier",
            "facts": {
                POS: [0.0, 0.0, 10.0],
                "packs.velocity": [0.0, 0.0, 0.0],
                "packs.energy": 100000.0,
            },
        }
        for i in range(count)
    ]
    d["bindings"]["rules"].append(
        {
            "writer": "motion",
            "type": "aas:Carrier",
            "fields": [POS, "packs.velocity", "packs.energy"],
        }
    )
    d["bindings"]["lifecycle"].append({"controller": "motion", "type": "aas:Carrier"})
    d["engines"]["motion"] = {
        "plugin": "kinematic",
        "config": {
            "model": "enu_point_mass",
            "type_id": "aas:Carrier",
            "position_field": POS,
            "velocity_field": "packs.velocity",
            "energy_field": "packs.energy",
            "max_speed_m_s": 10.0,
            "max_accel_m_s2": 5.0,
            "energy": {"capacity_j": 100000.0, "idle_w": 10.0, "per_m_j": 2.0},
            "commands": {
                kind: f"packs.motion.{kind}" for kind in ("move_to", "hold", "stop")
            },
            "arrival_schema": "packs.motion.arrived",
            "arrival_topic": "motion-arrivals",
            "arrival_payload": {"entity": "$entity", "position": "$position"},
            "result_fields": {
                "entity": "entity",
                "position": "position",
                "reason": "reason",
            },
            "step_ns": 200000000,
            "lifecycle": True,
            "frame": {
                "convention": "enu",
                "unit": "m",
                "transform_revision": "packs.local/v1",
            },
        },
    }


def logistics(count: int = 30, fleet: int = 5) -> dict[str, Any]:
    d = base("logistics-small")
    motion(d, fleet)
    fields = {
        "position": POS,
        "velocity": "packs.velocity",
        "energy": "packs.energy",
        "state": "packs.order.state",
        "facility_position": "packs.facility.position",
        "pad_capacity": "packs.facility.pad_capacity",
        "locker_capacity": "packs.facility.locker_capacity",
        "dwell_ns": "packs.facility.dwell_ns",
        "restriction_active": "packs.restriction.active",
        "restriction_start_ns": "packs.restriction.start_ns",
        "restriction_end_ns": "packs.restriction.end_ns",
        "restriction_lower": "packs.restriction.lower",
        "restriction_upper": "packs.restriction.upper",
        "restriction_activities": "packs.restriction.activities",
    }
    facility_fields = [
        field(fields["facility_position"], "aas:Facility", VECTOR, "m", True),
        field(fields["pad_capacity"], "aas:Facility", INTEGER, "1"),
        field(fields["locker_capacity"], "aas:Facility", INTEGER, "1"),
        field(fields["dwell_ns"], "aas:Facility", INTEGER, "ns"),
    ]
    restriction_fields = [
        field(fields[key], "agp:type:ActivityRestriction", schema, unit, frame)
        for key, schema, unit, frame in (
            ("restriction_active", {"type": "boolean"}, "1", False),
            ("restriction_start_ns", INTEGER, "ns", False),
            ("restriction_end_ns", INTEGER, "ns", False),
            ("restriction_lower", VECTOR, "m", True),
            ("restriction_upper", VECTOR, "m", True),
            ("restriction_activities", {"type": "array", "items": STRING}, "1", False),
        )
    ]
    d["registry"]["fields"] += (
        facility_fields
        + restriction_fields
        + [
            field(fields["state"], "oo:Order", STRING, "1"),
            field("packs.order.review", "oo:Order", STRING, "1"),
        ]
    )
    d["entities"] += [
        {
            "id": name,
            "type": "aas:Facility",
            "facts": {
                fields["facility_position"]: position,
                fields["pad_capacity"]: fleet,
                fields["locker_capacity"]: count,
                fields["dwell_ns"]: SECOND,
            },
        }
        for name, position in (
            ("depot", [0.0, 0.0, 10.0]),
            ("locker", [40.0, 0.0, 10.0]),
        )
    ]
    d["entities"].append(
        {
            "id": "restriction-1",
            "type": "agp:type:ActivityRestriction",
            "facts": {
                fields["restriction_active"]: True,
                fields["restriction_start_ns"]: 0,
                fields["restriction_end_ns"]: 180 * SECOND,
                fields["restriction_lower"]: [100.0, -10.0, 0.0],
                fields["restriction_upper"]: [120.0, 10.0, 30.0],
                fields["restriction_activities"]: ["parcel_transport"],
            },
        }
    )
    orders = [
        {
            "order": f"order-{i + 1}",
            "parcel": f"parcel-{i + 1}",
            "source": "depot",
            "destination": "locker",
            "deadline_ns": 150 * SECOND,
        }
        for i in range(count)
    ]
    for item in orders:
        d["entities"] += [
            {
                "id": item["order"],
                "type": "oo:Order",
                "facts": {
                    fields["state"]: "unreleased",
                    "packs.order.review": "pending",
                },
            },
            {"id": item["parcel"], "type": "aas:Parcel", "facts": {}},
        ]
    relation = {
        "id": "packs.parcel.custodian",
        "source_type": "aas:Parcel",
        "target_type": "aas:Custodian",
        "targets_per_source": {"minimum": 0, "maximum": 1},
        "sources_per_target": {"minimum": 0, "maximum": None},
        "identity_policy": "edge_id",
        "metadata": {"validity": "half-open; close before transfer"},
    }
    d["registry"]["relations"].append(relation)
    event_members = {
        "order": STRING,
        "state": STRING,
        "carrier": {"type": "string", "nullable": True},
        "release_ns": {**INTEGER, "nullable": True},
        "deadline_ns": INTEGER,
        "energy_start_j": NUMBER,
        "energy_used_j": NUMBER,
        "reason": STRING,
    }
    d["registry"]["messages"] += [
        {
            "id": "packs.logistics.transition",
            "kind": "event",
            "schema": record(
                event_members,
                ["order", "state", "carrier", "release_ns", "deadline_ns"],
            ),
        },
        {
            "id": "packs.business.decision",
            "kind": "event",
            "schema": record({"order": STRING, "accepted": {"type": "boolean"}}),
        },
    ]
    commands = {
        "assign": "packs.logistics.assign",
        "decide": "packs.logistics.decide",
        "cancel": "packs.logistics.cancel",
    }
    for kind, schema in commands.items():
        members = {
            "order": STRING,
            **(
                {"carrier": STRING}
                if kind == "assign"
                else {"accepted": {"type": "boolean"}}
                if kind == "decide"
                else {}
            ),
        }
        d["registry"]["messages"].append(
            {
                "id": schema,
                "kind": "command",
                "schema": record(members),
                "result_schema": record({"reason": STRING}, []),
            }
        )
    produces = [fields["state"]] + [
        f["id"] for f in facility_fields + restriction_fields
    ]
    consumes = [POS, fields["velocity"], fields["energy"]] + [
        f["id"] for f in facility_fields + restriction_fields
    ]
    for type_id, selected in (
        ("oo:Order", [fields["state"]]),
        ("aas:Facility", [f["id"] for f in facility_fields]),
        ("agp:type:ActivityRestriction", [f["id"] for f in restriction_fields]),
    ):
        d["bindings"]["rules"].append(
            {"writer": "logistics", "type": type_id, "fields": selected}
        )
    for type_id in (
        "oo:Order",
        "aas:Parcel",
        "aas:Facility",
        "agp:type:ActivityRestriction",
    ):
        d["bindings"]["lifecycle"].append({"controller": "logistics", "type": type_id})
    d["bindings"]["relations"].append(
        {"writer": "logistics", "relation": relation["id"], "type": "aas:Parcel"}
    )
    d["bindings"]["rules"].append(
        {"writer": "acceptance", "type": "oo:Order", "fields": ["packs.order.review"]}
    )
    d["engines"]["logistics"] = {
        "plugin": "logistics",
        "config": {
            "fields": fields,
            "produces": produces,
            "consumes": consumes,
            "fleet": [f"carrier-{i + 1}" for i in range(fleet)],
            "facilities": ["depot", "locker"],
            "restrictions": ["restriction-1"],
            "restriction_type": "agp:type:ActivityRestriction",
            "activity": "parcel_transport",
            "orders": orders,
            "arrivals": {"mode": "poisson", "rate_per_s": 0.4, "start_ns": 0},
            "policy": "deterministic",
            "poll_ns": 500000000,
            "arrival_radius_m": 0.1,
            "stopped_speed_m_s": 0.01,
            "energy": {
                "source": "joules",
                "speed_m_s": 10.0,
                "accel_m_s2": 5.0,
                "idle_w": 10.0,
                "per_m_j": 2.0,
                "reserve_j": 500.0,
                "motion_step_ns": 200000000,
            },
            "custody_relation": relation["id"],
            "event_schema": "packs.logistics.transition",
            "event_topic": "logistics-events",
            "decision_schema": "packs.business.decision",
            "decision_topic": "business-decisions",
            "commands": commands,
            "motion": {
                "schema": "packs.motion.move_to",
                "target": "motion",
                "entity_key": "entity",
                "target_key": "target",
                "extra_payload": {},
            },
        },
    }
    machines = [
        {
            "entity": o["order"],
            "field": "packs.order.review",
            "initial": "pending",
            "states": {
                "pending": {
                    "transitions": [
                        {
                            "to": "reviewing",
                            "event": "packs.logistics.transition",
                            "guard": {
                                "entity": o["order"],
                                "field": fields["state"],
                                "op": "eq",
                                "value": "delivered",
                            },
                        }
                    ]
                },
                "reviewing": {"transitions": [{"to": "decided", "timer_ns": SECOND}]},
                "decided": {
                    "transitions": [],
                    "on_enter": [
                        {
                            "kind": "emit",
                            "schema": "packs.business.decision",
                            "topic": "business-decisions",
                            "payload": {"order": o["order"], "accepted": True},
                        }
                    ],
                },
            },
        }
        for o in orders
    ]
    d["engines"]["acceptance"] = {
        "plugin": "workflow",
        "config": {
            "produces": ["packs.order.review"],
            "consumes": [fields["state"]],
            "emits": ["packs.business.decision"],
            "subscribes": ["logistics-events"],
            "targets": ["business-decisions"],
            "lifecycle": False,
            "correlation_key": "order",
            "machines": machines,
        },
    }
    return d


def inspection() -> dict[str, Any]:
    d = base("inspection-small")
    motion(d, 1)
    fields = {
        "position": POS,
        "attitude": "packs.camera.attitude_deg",
        "horizontal_fov": "packs.camera.horizontal_fov_deg",
        "vertical_fov": "packs.camera.vertical_fov_deg",
        "sample": "packs.inspection.sample",
        "acquired": "packs.inspection.acquired_ns",
        "available": "packs.inspection.available_ns",
        "subject": "packs.inspection.subject",
    }
    sample = record(
        {
            "model": STRING,
            "subject": STRING,
            "position_enu_m": VECTOR,
            "attitude_deg": VECTOR,
            "horizontal_fov_deg": NUMBER,
            "vertical_fov_deg": NUMBER,
            "ground_z_m": NUMBER,
            "pose_acquisition": record(
                {
                    "clock": STRING,
                    "mapping": STRING,
                    "numerator": INTEGER,
                    "denominator": {"type": "integer", "minimum": 1},
                }
            ),
            "pose_available_ns": INTEGER,
            "valid": {"type": "boolean"},
            "footprint": {
                "type": "array",
                "items": {"type": "vector", "length": 2, "items": NUMBER},
                "nullable": True,
            },
            "reason": STRING,
        },
        [
            "model",
            "subject",
            "position_enu_m",
            "attitude_deg",
            "horizontal_fov_deg",
            "vertical_fov_deg",
            "ground_z_m",
            "pose_acquisition",
            "pose_available_ns",
            "valid",
            "footprint",
        ],
    )
    d["registry"]["fields"] += [
        field(fields["attitude"], "aas:Camera", VECTOR, "deg", True),
        field(fields["horizontal_fov"], "aas:Camera", NUMBER, "deg"),
        field(fields["vertical_fov"], "aas:Camera", NUMBER, "deg"),
        field(fields["sample"], "aas:InspectionObservation", sample, "1"),
        field(fields["acquired"], "aas:InspectionObservation", INTEGER, "ns"),
        field(fields["available"], "aas:InspectionObservation", INTEGER, "ns"),
        field(
            fields["subject"],
            "aas:InspectionObservation",
            {"type": "ref", "target_type": "aas:Carrier"},
            "1",
        ),
    ]
    d["entities"].append(
        {
            "id": "camera-1",
            "type": "aas:Camera",
            "facts": {
                fields["attitude"]: [0.0, 0.0, 0.0],
                fields["horizontal_fov"]: 90.0,
                fields["vertical_fov"]: 90.0,
            },
        }
    )
    relation = {
        "id": "packs.inspection.subject",
        "source_type": "aas:InspectionObservation",
        "target_type": "aas:Carrier",
        "targets_per_source": {"minimum": 0, "maximum": 1},
        "sources_per_target": {"minimum": 0, "maximum": None},
        "identity_policy": "edge_id",
        "metadata": {"validity": "record availability onward"},
    }
    d["registry"]["relations"].append(relation)
    d["registry"]["messages"].append(
        {
            "id": "packs.inspection.observation",
            "kind": "event",
            "schema": record(
                {
                    "record": STRING,
                    "acquired_ns": INTEGER,
                    "available_ns": INTEGER,
                    "sample": sample,
                }
            ),
        }
    )
    produces = [
        fields[k]
        for k in (
            "attitude",
            "horizontal_fov",
            "vertical_fov",
            "sample",
            "acquired",
            "available",
            "subject",
        )
    ]
    for type_id, selected in (
        ("aas:Camera", produces[:3]),
        ("aas:InspectionObservation", produces[3:]),
    ):
        d["bindings"]["rules"].append(
            {"writer": "inspection", "type": type_id, "fields": selected}
        )
        d["bindings"]["lifecycle"].append({"controller": "inspection", "type": type_id})
    d["bindings"]["relations"].append(
        {
            "writer": "inspection",
            "relation": relation["id"],
            "type": "aas:InspectionObservation",
        }
    )
    d["engines"]["inspection"] = {
        "plugin": "inspection",
        "config": {
            "fields": fields,
            "produces": produces,
            "consumes": [POS] + produces[:3],
            "subjects": ["carrier-1"],
            "sensors": {"carrier-1": "camera-1"},
            "record_type": "aas:InspectionObservation",
            "subject_relation": relation["id"],
            "sample_period_ns": SECOND,
            "availability_delay_ns": 100000000,
            "start_ns": SECOND,
            "end_ns": 19 * SECOND,
            "ground_z_m": 0.0,
            "event_schema": "packs.inspection.observation",
            "event_topic": "inspection-observations",
            "targets": [
                {"id": "origin", "kind": "waypoint", "point": [0.0, 0.0]},
                {"id": "destination", "kind": "waypoint", "point": [40.0, 0.0]},
                {
                    "id": "corridor",
                    "kind": "area",
                    "bounds": [-10.0, -10.0, 50.0, 10.0],
                },
            ],
        },
    }
    d["bindings"]["commands"] = [
        {
            "schema": "packs.motion.move_to",
            "target": "motion",
            "at_ns": 2 * SECOND,
            "payload": {"entity": "carrier-1", "target": [40.0, 0.0, 10.0]},
        },
        {
            "schema": "packs.motion.move_to",
            "target": "motion",
            "at_ns": 11 * SECOND,
            "payload": {"entity": "carrier-1", "target": [0.0, 0.0, 10.0]},
        },
    ]
    d["run"].update(until_ns=20 * SECOND)
    return d


def px4() -> dict[str, Any]:
    d = logistics(1, 1)
    d["id"] = "logistics-px4"
    native = yaml.safe_load((ROOT.parent / "adapters/px4-flight.yaml").read_text())
    d["engines"].pop("motion")
    d["registry"]["messages"] += native["registry"]["messages"]
    # Preserve the adapter's real telemetry schemas, remapping only position to
    # the actual selected AeroGraph position field.
    for f in native["registry"]["fields"]:
        if f["id"] == "e1.px4.position":
            continue
        cloned = copy.deepcopy(f)
        cloned["type"] = "aas:Carrier"
        d["registry"]["fields"].append(cloned)
    config = copy.deepcopy(native["engines"]["flight"]["config"])
    config["vehicles"] = {"carrier-1": "u1"}
    config["fields"]["position"] = POS
    config["control_step_ns"] = 20_000_000
    d["engines"]["flight"] = {"plugin": "px4_gazebo", "config": config}
    d["clock_mappings"] = native["clock_mappings"]
    d["bindings"]["rules"] = [
        r for r in d["bindings"]["rules"] if r["writer"] != "motion"
    ] + [
        {
            "writer": "flight",
            "type": "aas:Carrier",
            "fields": list(config["fields"].values()),
        }
    ]
    d["bindings"]["lifecycle"] = [
        r for r in d["bindings"]["lifecycle"] if r["controller"] != "motion"
    ] + [{"controller": "flight", "type": "aas:Carrier"}]
    next(e for e in d["entities"] if e["id"] == "carrier-1")["facts"] = {}
    for e in d["entities"]:
        if e["id"] == "locker":
            e["facts"]["packs.facility.position"] = [40.0, 0.0, 5.0]
        elif e["id"] == "depot":
            e["facts"]["packs.facility.position"] = [0.0, 0.0, 5.0]
    c = d["engines"]["logistics"]["config"]
    c["fields"].update(velocity="e1.px4.velocity", energy="e1.px4.battery")
    c["consumes"] = [POS, "e1.px4.velocity", "e1.px4.battery"] + c["consumes"][3:]
    c["arrivals"] = {"mode": "scheduled", "times_ns": [56 * SECOND]}
    c["arrival_radius_m"], c["stopped_speed_m_s"] = 1.0, 0.5
    c["energy"].update(
        source="battery_fraction",
        capacity_j=1000000.0,
        speed_m_s=2.0,
        accel_m_s2=1.0,
        idle_w=100.0,
        per_m_j=10.0,
        motion_step_ns=config["step_ns"],
    )
    c["motion"] = {
        "schema": "adapters.px4_gazebo.goto",
        "target": "flight",
        "entity_key": "entity",
        "target_key": "position_enu",
        "extra_payload": {"yaw_deg": 90.0},
    }
    c["orders"][0]["deadline_ns"] = 250 * SECOND
    # The native parcel schedule is a model input, never a claimed observed
    # transfer time. Pad dwell can only start after actual stopped telemetry.
    for e in d["entities"]:
        if e["id"] == "locker":
            e["facts"]["packs.facility.dwell_ns"] = 55 * SECOND
    # A long fixed arm-to-takeoff delay expires PX4's preflight auto-disarm.
    # Use actual successful child receipts; telemetry stays owned by flight.
    d["bindings"]["commands"] = []
    phase = "packs.flight.phase"
    d["registry"]["fields"].append(
        {
            "id": phase,
            "type": "aas:Carrier",
            "schema": STRING,
            "metadata": {
                "role": "state",
                "time_semantics": "canonical workflow phase from native command receipts",
            },
        }
    )
    d["bindings"]["rules"].append(
        {"writer": "launch", "type": "aas:Carrier", "fields": [phase]}
    )
    states: dict[str, Any] = {
        "waiting": {"transitions": [{"to": "arming", "timer_ns": SECOND}]},
        "ready": {
            "transitions": [
                {
                    "to": "landing",
                    "predicate": {
                        "entity": "order-1",
                        "field": "packs.order.state",
                        "op": "eq",
                        "value": "accepted",
                    },
                }
            ]
        },
        "landed": {"transitions": []},
        "failed": {"transitions": []},
    }
    for state, action, next_state, params in (
        ("arming", "arm", "taking_off", {}),
        ("taking_off", "takeoff", "ready", {"altitude_m": 5.0}),
        ("landing", "land", "landed", {}),
    ):
        states[state] = {
            "on_enter": [
                {
                    "kind": "command",
                    "id": action,
                    "schema": f"adapters.px4_gazebo.{action}",
                    "target": "flight",
                    "payload": {"entity": "carrier-1", **params},
                }
            ],
            "transitions": [
                {
                    "to": next_state,
                    "receipt": {
                        "status": "succeeded",
                        "children": [action],
                        "policy": "all",
                    },
                },
                {"to": "failed", "receipt": "failed"},
                {"to": "failed", "receipt": "rejected"},
            ],
        }
    d["engines"]["launch"] = {
        "plugin": "workflow",
        "config": {
            "produces": [phase],
            "consumes": ["packs.order.state"],
            "emits": [f"adapters.px4_gazebo.{a}" for a in ("arm", "takeoff", "land")],
            "subscribes": [],
            "targets": ["flight"],
            "lifecycle": False,
            "machines": [
                {
                    "entity": "carrier-1",
                    "field": phase,
                    "initial": "waiting",
                    "states": states,
                }
            ],
        },
    }
    d["run"].update(until_ns=300 * SECOND)
    return d


def main() -> None:
    snapshot = compile_registry(
        configured_ontology(),
        Selection(
            (
                "oo:UAV",
                "oo:Order",
                "oo:Facility",
                "oo:ObservationRecord",
                "agp:type:ActivityRestriction",
            ),
            (POS,),
            (),
        ),
    )
    snapshot.write_snapshot(ROOT / "aerograph.snapshot.json")
    for name, document in (
        ("logistics-small", logistics()),
        ("inspection-small", inspection()),
        ("logistics-px4", px4()),
    ):
        (ROOT / f"{name}.yaml").write_text(yaml.safe_dump(document, sort_keys=False))


if __name__ == "__main__":
    main()
