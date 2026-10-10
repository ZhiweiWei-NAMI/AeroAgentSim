"""Portable low-altitude registry descriptors and event feed declarations."""

from __future__ import annotations

from typing import Any

TASK_KINDS = (
    "parcel",
    "medical",
    "inspection",
    "survey",
    "spraying",
    "passenger",
    "patrol",
    "incident_response",
)
TASK_CATEGORIES = dict(
    zip(
        TASK_KINDS,
        (
            "logistics",
            "emergency",
            "inspection",
            "inspection",
            "agriculture",
            "mobility",
            "security",
            "emergency",
        ),
    )
)
NUMBER = {"type": "number"}
STRING = {"type": "string"}
BOOL = {"type": "boolean"}
VECTOR = {"type": "vector", "length": 3, "items": NUMBER}
INT = {"type": "integer", "minimum": 0}
STRINGS = {"type": "array", "items": STRING}


def record(members: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": [k for k in members if k not in optional],
        "extra": False,
    }


EVENT_NARRATIVES: dict[str, dict[str, Any]] = {
    "lowalt.task.created": {
        "text": "Order {task} appeared",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.assigned": {
        "text": "{vehicle} was assigned to {task}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.started": {
        "text": "{vehicle} started {task}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.picked_up": {
        "text": "{vehicle} picked up {task}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.completed": {
        "text": "{vehicle} completed {task}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.failed": {
        "text": "{task} failed: {reason}",
        "category": "logistics",
        "severity": "warning",
    },
    "lowalt.airspace.conflict": {
        "text": "{a} and {b} are too close",
        "category": "airspace",
        "severity": "warning",
    },
    "lowalt.airspace.conflict_resolved": {
        "text": "Separation between {a} and {b} restored ({action})",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.airspace.geofence_entered": {
        "text": "{vehicle} entered {zone}",
        "category": "airspace",
        "severity": "warning",
    },
    "lowalt.airspace.zone_activated": {
        "text": "{zone} activated: {reason}",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.airspace.zone_deactivated": {
        "text": "{zone} deactivated: {reason}",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.energy.low": {
        "text": "{vehicle} has low battery",
        "category": "energy",
        "severity": "warning",
    },
    "lowalt.energy.charging_started": {
        "text": "{vehicle} began charging at {pad}",
        "category": "energy",
        "severity": "info",
    },
    "lowalt.energy.charging_finished": {
        "text": "{vehicle} finished charging at {pad}",
        "category": "energy",
        "severity": "info",
    },
    "lowalt.weather.hold": {
        "text": "Flight held: {reason}",
        "category": "weather",
        "severity": "warning",
    },
    "lowalt.weather.release": {
        "text": "Flight released: {reason}",
        "category": "weather",
        "severity": "notice",
    },
}
EVENT_NARRATIVES.update(
    {
        "lowalt.task.preempted": {
            "text": "{vehicle} paused {task} for urgent {urgent_task}",
            "category": "emergency",
            "severity": "notice",
        },
        "lowalt.task.resumed": {
            "text": "{vehicle} resumed {task}",
            "category": "logistics",
            "severity": "info",
        },
        "lowalt.task.replanned": {
            "text": "{vehicle} changed route: {reason}",
            "category": "airspace",
            "severity": "notice",
        },
        "lowalt.inspection.observed": {
            "text": "{vehicle} surveyed a section of {task}",
            "category": "inspection",
            "severity": "info",
        },
        "lowalt.spraying.applied": {
            "text": "{vehicle} treated a field section for {task}",
            "category": "agriculture",
            "severity": "info",
        },
        "lowalt.patrol.visited": {
            "text": "{vehicle} reached a patrol point for {task}",
            "category": "security",
            "severity": "info",
        },
        "lowalt.site.closed": {
            "text": "Operator closed {site}",
            "category": "mobility",
            "severity": "warning",
        },
        "lowalt.mobility.pad_wait": {
            "text": "{vehicle} is waiting for a pad at {site}",
            "category": "mobility",
            "severity": "notice",
        },
        "lowalt.energy.swap_started": {
            "text": "{vehicle} began a battery swap at {pad}",
            "category": "energy",
            "severity": "info",
        },
        "lowalt.energy.swap_finished": {
            "text": "{vehicle} received a charged battery at {pad}",
            "category": "energy",
            "severity": "info",
        },
    }
)
for _schema, _meta in EVENT_NARRATIVES.items():
    if _schema.startswith("lowalt.task."):
        _meta["category_by_kind"] = TASK_CATEGORIES


def overlay() -> dict[str, Any]:
    types = [
        {"id": "lowalt:" + name, "parents": parents, "abstract": False}
        for name, parents in (
            ("UAV", ["oo:UAV"]),
            ("Site", ["oo:ModelObject"]),
            ("Customer", ["lowalt:Site"]),
            ("Vertiport", ["lowalt:Site", "oo:Facility"]),
            ("Depot", ["lowalt:Vertiport"]),
            ("ChargingPad", ["lowalt:Site"]),
            ("AirspaceZone", ["oo:ModelObject"]),
            ("Task", ["oo:Task"]),
            ("Environment", ["oo:ModelObject"]),
        )
    ]
    fields: list[dict[str, Any]] = []

    def add(name: str, owner: str, schema: dict[str, Any], unit: str = "1") -> None:
        meta: dict[str, Any] = {
            "role": "observation",
            "unit": unit,
            "time_semantics": "canonical computation; half-open validity",
        }
        if schema is VECTOR:
            meta.update(frame="enu", transform_revision="lowalt.district/v1")
        fields.append(
            {
                "id": "lowalt." + name,
                "type": "lowalt:" + owner,
                "schema": schema,
                "metadata": meta,
            }
        )

    for name in ("position", "velocity"):
        add(name, "UAV", VECTOR, "m" if name == "position" else "m/s")
    for name, unit in (
        ("soc", "1"),
        ("capacity_j", "J"),
        ("motion_energy_j", "J"),
        ("payload_kg", "kg"),
        ("payload_capacity_kg", "kg"),
        ("energy_used_j", "J"),
        ("distance_m", "m"),
    ):
        add(name, "UAV", NUMBER, unit)
    add("vehicle_class", "UAV", STRING)
    for name in ("membership", "conflicts"):
        add(name, "UAV", STRINGS)
    for name in ("weather_held", "charging"):
        add(name, "UAV", BOOL)
    add("site_position", "Site", VECTOR, "m")
    add("site_open", "Site", BOOL)
    add("site_capacity", "Site", INT)
    add("pad_power_w", "ChargingPad", NUMBER, "W")
    add("wind", "Environment", VECTOR, "m/s")
    add(
        "zone_kind",
        "AirspaceZone",
        {
            "type": "string",
            "enum": ["no_fly", "restricted", "altitude_band", "corridor"],
        },
    )
    for name in ("zone_lower", "zone_upper"):
        add(name, "AirspaceZone", VECTOR, "m")
    for name in ("zone_start_ns", "zone_end_ns"):
        add(name, "AirspaceZone", INT, "ns")
    for name in ("zone_active", "zone_dynamic"):
        add(name, "AirspaceZone", BOOL)
    add("task_kind", "Task", {"type": "string", "enum": list(TASK_KINDS)})
    for name in ("task_state", "task_origin", "task_destination", "task_vehicle"):
        add(name, "Task", STRING)
    add("task_payload_kg", "Task", NUMBER, "kg")
    add("task_priority", "Task", INT)
    add("task_created_ns", "Task", INT, "ns")
    members: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
        "task.created": (
            {
                "task": STRING,
                "kind": STRING,
                "priority": INT,
                "origin": STRING,
                "destination": STRING,
            },
            ("origin", "destination"),
        ),
        "task.assigned": ({"task": STRING, "vehicle": STRING, "algorithm": STRING}, ()),
        "task.started": ({"task": STRING, "vehicle": STRING}, ()),
        "task.picked_up": ({"task": STRING, "vehicle": STRING}, ()),
        "task.completed": (
            {"task": STRING, "vehicle": STRING, "latency_s": NUMBER},
            (),
        ),
        "task.preempted": (
            {"task": STRING, "vehicle": STRING, "urgent_task": STRING},
            (),
        ),
        "task.resumed": ({"task": STRING, "vehicle": STRING}, ()),
        "task.replanned": (
            {"task": STRING, "vehicle": STRING, "reason": STRING, "zone": STRING},
            ("zone",),
        ),
        "inspection.observed": (
            {
                "task": STRING,
                "vehicle": STRING,
                "position": VECTOR,
                "distance_m": NUMBER,
            },
            (),
        ),
        "spraying.applied": (
            {
                "task": STRING,
                "vehicle": STRING,
                "position": VECTOR,
                "distance_m": NUMBER,
            },
            (),
        ),
        "patrol.visited": ({"task": STRING, "vehicle": STRING, "position": VECTOR}, ()),
        "site.closed": ({"site": STRING}, ()),
        "operator.close_vertiport": ({"site": STRING}, ()),
        "operator.add_urgent_order": (
            {"task": STRING, "origin": STRING, "destination": STRING},
            (),
        ),
        "operator.investigate": (
            {"task": STRING, "origin": STRING, "destination": STRING},
            (),
        ),
        "energy.swap_started": ({"vehicle": STRING, "pad": STRING}, ()),
        "energy.swap_finished": ({"vehicle": STRING, "pad": STRING, "soc": NUMBER}, ()),
        "mobility.pad_wait": ({"task": STRING, "vehicle": STRING, "site": STRING}, ()),
        "task.failed": (
            {"task": STRING, "vehicle": STRING, "reason": STRING},
            ("vehicle",),
        ),
        "airspace.conflict": (
            {"a": STRING, "b": STRING, "horizontal_m": NUMBER, "vertical_m": NUMBER},
            (),
        ),
        "airspace.conflict_resolved": (
            {"a": STRING, "b": STRING, "action": STRING},
            (),
        ),
        "airspace.geofence_entered": ({"vehicle": STRING, "zone": STRING}, ()),
        "airspace.zone_activated": ({"zone": STRING, "reason": STRING}, ()),
        "airspace.zone_deactivated": ({"zone": STRING, "reason": STRING}, ()),
        "energy.low": ({"vehicle": STRING, "soc": NUMBER}, ()),
        "energy.charging_started": (
            {"vehicle": STRING, "pad": STRING, "soc": NUMBER},
            (),
        ),
        "energy.charging_finished": (
            {"vehicle": STRING, "pad": STRING, "soc": NUMBER},
            (),
        ),
        "weather.hold": (
            {"vehicle": STRING, "zone": STRING, "reason": STRING, "wind_mps": NUMBER},
            ("vehicle", "zone", "wind_mps"),
        ),
        "weather.release": (
            {"vehicle": STRING, "zone": STRING, "reason": STRING, "wind_mps": NUMBER},
            ("vehicle", "zone", "wind_mps"),
        ),
        "control": (
            {
                "vehicle": STRING,
                "action": STRING,
                "target": VECTOR,
                "until_ns": INT,
                "reason": STRING,
            },
            ("target", "until_ns"),
        ),
        "allocation.request": ({"tasks_json": STRING, "vehicles_json": STRING}, ()),
        "allocation.result": ({"assignments_json": STRING}, ()),
        "motion.arrived": ({"entity": STRING, "position": VECTOR}, ()),
    }
    messages: list[dict[str, Any]] = [
        {"id": "lowalt." + name, "kind": "event", "schema": record(m, o)}
        for name, (m, o) in members.items()
    ]
    result = record(
        {"entity": STRING, "position": VECTOR, "reason": STRING},
        ("entity", "position", "reason"),
    )
    for name in ("move_to", "hold", "stop"):
        m = {"entity": STRING, **({"target": VECTOR} if name == "move_to" else {})}
        messages.append(
            {
                "id": "lowalt.motion." + name,
                "kind": "command",
                "schema": record(m),
                "result_schema": result,
                "feedback_schema": result,
                "cancel_support": False,
            }
        )
    messages.append(
        {
            "id": "lowalt.airspace.activate",
            "kind": "command",
            "schema": record({"zone": STRING, "active": BOOL, "reason": STRING}),
            "result_schema": record({"zone": STRING, "active": BOOL}),
            "cancel_support": False,
        }
    )
    return {"types": types, "fields": fields, "messages": messages}
