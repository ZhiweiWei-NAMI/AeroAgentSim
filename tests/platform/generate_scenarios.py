"""Authored P1 scenarios, with explicit fleet facts and per-order machines."""

import hashlib
from copy import deepcopy
from pathlib import Path

import yaml

POS = "he.aircraft.position_enu_m"
VEL = "aas.p1.velocity_enu_m_s"
ENERGY = "aas.p1.energy_j"
STATE = "aas.p1.order_state"
SAMPLE = "aas.p1.position_sample"
REC = "aas:KinematicObservation"
ACQ = "oo:shared.observationRecord.acquisitionTime"
AVAIL = "oo:shared.observationRecord.availableTime"
SUB = "oo:shared.observationRecord.observedSubjectRef"
VEC = {"type": "vector", "items": {"type": "number"}, "length": 3}


def record(members):
    return {
        "type": "record",
        "members": members,
        "required": list(members),
        "extra": False,
    }


STRING = {"type": "string"}
NUMBER = {"type": "number"}
INT = {"type": "integer"}
CONTROL = record({"entity": STRING, "machine": STRING})
MOVE = record({"entity": STRING, "machine": STRING, "target": VEC})
RESULT = {
    "type": "record",
    "members": {"entity": STRING, "position": VEC, "reason": STRING},
    "required": [],
    "extra": False,
}
ARRIVAL = record({"entity": STRING, "machine": STRING, "position": VEC})
base = {
    "format": "aeroagentsim.scenario/v1",
    "id": "p1-slice",
    "registry": {
        "compile": {
            "root": "/mnt/data2/weizhiwei/AeroGraph",
            "types": ["oo:UAV", "oo:Order", "oo:MetricObservation"],
            "fields": [POS, ACQ, AVAIL, SUB],
            "relations": ["oo:relation:observation-subject"],
        },
        "types": [{"id": REC, "parents": ["oo:MetricObservation"], "abstract": False}],
        "fields": [
            {
                "id": VEL,
                "type": "oo:UAV",
                "schema": VEC,
                "metadata": {
                    "unit": "m/s",
                    "frame": "enu",
                    "role": "derived",
                    "clock": "canonical",
                },
            },
            {
                "id": ENERGY,
                "type": "oo:UAV",
                "schema": NUMBER,
                "metadata": {"unit": "J", "role": "derived", "clock": "canonical"},
            },
            {
                "id": STATE,
                "type": "oo:Order",
                "schema": {
                    "type": "string",
                    "enum": [
                        "submitted",
                        "executing",
                        "awaiting_acceptance",
                        "accepted",
                        "failed",
                        "canceled",
                    ],
                },
                "metadata": {"role": "state", "clock": "canonical"},
            },
            {
                "id": SAMPLE,
                "type": REC,
                "schema": VEC,
                "metadata": {
                    "unit": "m",
                    "frame": "enu",
                    "role": "observation",
                    "clock": "canonical",
                },
            },
        ],
        "messages": [
            *[
                {
                    "id": "aas.motion." + name,
                    "kind": "command",
                    "schema": MOVE if name == "move_to" else CONTROL,
                    "result_schema": RESULT,
                    "feedback_schema": RESULT,
                }
                for name in ("move_to", "hold", "stop")
            ],
            {"id": "aas.motion.arrived", "kind": "event", "schema": ARRIVAL},
            {
                "id": "aas.business.accepted",
                "kind": "event",
                "schema": record({"machine": STRING}),
            },
            {
                "id": "aas.p1.reached_x",
                "kind": "event",
                "schema": record({"entity": STRING, "from_ns": INT, "to_ns": INT}),
            },
        ],
    },
    "entities": [
        {
            "id": f"uav-{i}",
            "type": "oo:UAV",
            "facts": {
                POS: [0.0, float((i - 1) * 12), 10.0],
                VEL: [0.0, 0.0, 0.0],
                ENERGY: 100000.0,
            },
        }
        for i in range(1, 6)
    ]
    + [
        {"id": f"order-{i}", "type": "oo:Order", "facts": {STATE: "submitted"}}
        for i in range(1, 11)
    ],
    "bindings": {
        "rules": [
            {"writer": "motion", "type": "oo:UAV", "fields": [POS, VEL, ENERGY]},
            {"writer": "operations", "type": "oo:Order", "fields": [STATE]},
            {
                "writer": "observations",
                "type": REC,
                "fields": [ACQ, AVAIL, SUB, SAMPLE],
            },
        ],
        "lifecycle": [
            {"controller": "motion", "type": "oo:UAV"},
            {"controller": "operations", "type": "oo:Order"},
            {"controller": "observations", "type": REC, "ids": "obs/*"},
        ],
    },
    "engines": {
        "motion": {
            "plugin": "kinematic",
            "config": {
                "type_id": "oo:UAV",
                "position_field": POS,
                "velocity_field": VEL,
                "energy_field": ENERGY,
                "step_ns": 100000000,
                "frame": {
                    "convention": "enu",
                    "unit": "m",
                    "transform_revision": "p1-local-1",
                },
                "max_speed_m_s": 10.0,
                "max_accel_m_s2": 5.0,
                "energy": {"capacity_j": 100000.0, "idle_w": 20.0, "per_m_j": 5.0},
                "commands": {
                    name: "aas.motion." + name for name in ("move_to", "hold", "stop")
                },
                "arrival_schema": "aas.motion.arrived",
                "arrival_topic": "arrival",
                "lifecycle": True,
            },
        },
        "operations": {
            "plugin": "workflow",
            "config": {
                "produces": [STATE],
                "consumes": [],
                "emits": ["aas.motion.move_to", "aas.business.accepted"],
                "subscribes": ["arrival"],
                "targets": ["motion", "business-acceptance"],
                "lifecycle": True,
                "message_lag_ns": 10000000,
                "machines": [],
            },
        },
        "observations": {
            "plugin": "records",
            "config": {
                "trigger_schema": "aas.motion.arrived",
                "topic": "arrival",
                "subject_key": "entity",
                "position_field": POS,
                "type_id": REC,
                "acquired_field": ACQ,
                "available_field": AVAIL,
                "subject_field": SUB,
                "sample_field": SAMPLE,
                "release_delay_ns": 20000000,
            },
        },
        "threshold": {
            "plugin": "threshold",
            "config": {
                "field": POS,
                "index": 0,
                "value": 10.0,
                "event": "aas.p1.reached_x",
                "topic": "threshold-events",
            },
        },
    },
    "presentation": [
        {
            "typeId": "oo:UAV",
            "positionField": POS,
            "frame": "enu",
            "visual": {"kind": "marker", "color": "#50c9ff", "scale": 1.5},
        }
    ],
    "run": {
        "seed": 42,
        "until_ns": 22000000000,
        "pacing": "fast",
        "advance_ns": 1000000000,
    },
    "outputs": {"durability": "flush"},
}
for i in range(1, 11):
    u = (i - 1) % 5 + 1
    # The second order per subject is assigned at 11 seconds, after first arrival.
    start = 10000000 if i <= 5 else 11000000000
    dest = [float(30 if i <= 5 else 60), float((u - 1) * 12), 10.0]
    base["engines"]["operations"]["config"]["machines"].append(
        {
            "entity": f"order-{i}",
            "field": STATE,
            "initial": "submitted",
            "states": {
                "submitted": {"transitions": [{"to": "executing", "timer_ns": start}]},
                "executing": {
                    "on_enter": [
                        {
                            "kind": "command",
                            "schema": "aas.motion.move_to",
                            "target": "motion",
                            "payload": {
                                "entity": f"uav-{u}",
                                "machine": f"order-{i}",
                                "target": dest,
                            },
                        }
                    ],
                    "transitions": [
                        {"to": "awaiting_acceptance", "event": "aas.motion.arrived"},
                        {"to": "failed", "receipt": "failed"},
                        {"to": "failed", "receipt": "rejected"},
                    ],
                },
                "awaiting_acceptance": {
                    "transitions": [{"to": "accepted", "timer_ns": 100000000}]
                },
                "accepted": {
                    "on_enter": [
                        {
                            "kind": "emit",
                            "schema": "aas.business.accepted",
                            "topic": "business-acceptance",
                            "payload": {"machine": f"order-{i}"},
                        }
                    ],
                    "transitions": [],
                },
                "failed": {"transitions": []},
            },
        }
    )
# Runtime relation direction absent in the source is explicitly authored here.
REL = "oo:relation:observation-subject"
base["registry"]["relations"] = [
    {
        "id": REL,
        "source_type": "oo:ObservationRecord",
        "target_type": "oo:ModelObject",
        "targets_per_source": {"minimum": 1, "maximum": 1},
        "sources_per_target": {"minimum": 0, "maximum": None},
        "identity_policy": "edge_id",
        "metadata": {
            "inverse_policy": "authored unconstrained many records per subject; source declares no inverse direction",
            "reviewStatus": "proposed",
        },
    }
]
base["bindings"]["relations"] = [
    {"writer": "observations", "relation": REL, "type": REC, "ids": "obs/*"}
]
base["bindings"]["obligations"] = [
    {
        "controller": "observations",
        "relation": REL,
        "direction": "targets_per_source",
        "type": REC,
        "ids": "obs/*",
    }
]
base["engines"]["observations"]["config"]["relation_id"] = REL
base["engines"]["threshold"]["config"].update(
    context="aas.p1.reached_x",
    ast={
        "op": "gte",
        "args": [
            {"field": POS, "role": "subject", "path": [0]},
            {"parameter": "thresholdXM"},
        ],
    },
    parameters={"thresholdXM": 10.0},
    native_reference={
        "path": "/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/expanded_runtime.js",
        "sha256": hashlib.sha256(
            Path(
                "/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/expanded_runtime.js"
            ).read_bytes()
        ).hexdigest(),
    },
)
base["bindings"]["samples"] = [
    {
        "context": "aas.p1.reached_x",
        "partition": "threshold",
        "upstream": ["motion", "operations", "observations", "acceptance"],
        "bindings": {f"uav-{i}": f"uav-{i}" for i in range(1, 6)},
        "sources": {f"uav-{i}": "motion" for i in range(1, 6)},
        "clocks": {f"uav-{i}": ["canonical", "canonical"] for i in range(1, 6)},
        "parameters": {"thresholdXM": 10.0},
    }
]
# Independent authored DES reviewer. Arrival makes a review eligible; it does
# not constitute business acceptance. Removing this producer leaves orders waiting.
ACK = "aas.p1.acceptance_state"
base["registry"]["fields"].append(
    {
        "id": ACK,
        "type": "oo:Order",
        "schema": {"type": "string", "enum": ["pending", "reviewing", "acknowledged"]},
        "metadata": {"role": "state", "clock": "canonical"},
    }
)
base["bindings"]["rules"].append(
    {"writer": "acceptance", "type": "oo:Order", "fields": [ACK]}
)
base["engines"]["operations"]["config"]["subscribes"].append("business-acceptance")
base["engines"]["operations"]["config"]["emits"] = ["aas.motion.move_to"]
base["engines"]["operations"]["config"]["targets"] = ["motion"]
base["engines"]["acceptance"] = {
    "plugin": "workflow",
    "config": {
        "produces": [ACK],
        "consumes": [],
        "emits": ["aas.business.accepted"],
        "subscribes": ["arrival"],
        "targets": ["business-acceptance"],
        "lifecycle": False,
        "machines": [],
    },
}
for entity in base["entities"]:
    if entity["type"] == "oo:Order":
        entity["facts"][ACK] = "pending"
        base["engines"]["acceptance"]["config"]["machines"].append(
            {
                "entity": entity["id"],
                "field": ACK,
                "initial": "pending",
                "states": {
                    "pending": {
                        "transitions": [
                            {"to": "reviewing", "event": "aas.motion.arrived"}
                        ]
                    },
                    "reviewing": {
                        "transitions": [{"to": "acknowledged", "timer_ns": 100000000}]
                    },
                    "acknowledged": {
                        "on_enter": [
                            {
                                "kind": "emit",
                                "schema": "aas.business.accepted",
                                "topic": "business-acceptance",
                                "payload": {"machine": entity["id"]},
                            }
                        ],
                        "transitions": [],
                    },
                },
            }
        )
for machine in base["engines"]["operations"]["config"]["machines"]:
    machine["states"]["awaiting_acceptance"]["transitions"] = [
        {"to": "accepted", "event": "aas.business.accepted"}
    ]
    machine["states"]["accepted"] = {"transitions": []}
motion_config = base["engines"]["motion"]["config"]
motion_config["model"] = "enu_point_mass"
motion_config["arrival_payload"] = {
    "entity": "$entity",
    "position": "$position",
    "machine": "$payload.machine",
}
motion_config["result_fields"] = {
    "entity": "entity",
    "position": "position",
    "reason": "reason",
}
base["registry"]["field_metadata"] = {
    POS: {
        "frame": "enu",
        "transform_revision": motion_config["frame"]["transform_revision"],
    }
}
for message in base["registry"]["messages"]:
    if message["kind"] == "command":
        message["cancel_support"] = True
        message["schema"]["members"]["subject"] = {
            "type": "ref",
            "target_type": "oo:UAV",
        }
for engine in base["engines"].values():
    if engine["plugin"] != "workflow":
        continue
    engine["config"]["correlation_key"] = "machine"
    for machine in engine["config"]["machines"]:
        for state in machine["states"].values():
            for index, action in enumerate(state.get("on_enter", [])):
                if action["kind"] == "command":
                    action["id"] = f"{machine['entity']}/{index}/move"
                    action["payload"]["subject"] = {
                        "$ref": {
                            "run_id": base["id"],
                            "epoch": "0",
                            "id": action["payload"]["entity"],
                            "generation": 0,
                            "type_id": "oo:UAV",
                        }
                    }
Path("scenarios/p1-slice.yaml").write_text(yaml.safe_dump(base, sort_keys=False))
scale = deepcopy(base)
scale["id"] = "p1-scale"
scale["registry"]["compile"]["types"] = ["oo:UAV"]
scale["registry"]["compile"]["fields"] = [POS]
scale["registry"]["compile"]["relations"] = []
scale["registry"]["types"] = []
scale["registry"]["relations"] = []
for inactive in ("samples", "relations", "obligations"):
    scale["bindings"].pop(inactive, None)
scale["registry"]["fields"] = scale["registry"]["fields"][:2]
scale["entities"] = [
    {
        "id": f"mover-{i:04d}",
        "type": "oo:UAV",
        "facts": {
            POS: [0.0, float(i % 40) * 5, float(i // 40) * 5],
            VEL: [10.0, 0.0, 0.0],
            ENERGY: 100000.0,
        },
    }
    for i in range(1000)
]
scale["bindings"]["rules"] = scale["bindings"]["rules"][:1]
scale["bindings"]["lifecycle"] = scale["bindings"]["lifecycle"][:1]
scale["engines"] = {"motion": scale["engines"]["motion"]}
scale["engines"]["motion"]["config"]["step_ns"] = 1000000000
scale["run"]["until_ns"] = 10000000000
Path("scenarios/p1-scale.yaml").write_text(yaml.safe_dump(scale, sort_keys=False))
