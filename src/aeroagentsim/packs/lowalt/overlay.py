"""Portable low-altitude registry descriptors and event feed declarations."""

from __future__ import annotations

from typing import Any

TASK_KINDS = (
    "parcel",
    "food",
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
PAIR = {"type": "vector", "length": 2, "items": NUMBER}
STRINGS = {"type": "array", "items": STRING}
PROVENANCE = {
    "type": "record",
    "members": {"message_ids": STRINGS, "event_key": STRING, "parent_event_keys": STRINGS},
    "required": ["message_ids"],
    "extra": False,
}


def target(type_id: str) -> dict[str, str]:
    return {"type": "ref", "target_type": "lowalt:" + type_id}


def record(members: dict[str, Any], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": [k for k in members if k not in optional],
        "extra": False,
    }


EVENT_NARRATIVES: dict[str, dict[str, Any]] = {
    "lowalt.operator.rejected": {
        "text": "Operator request {operation} rejected: {reason}",
        "text_zh": "操作请求 {operation} 被拒绝：{reason}",
        "category": "logistics", "severity": "warning", "entities": [],
    },
    "lowalt.parcel.dropoff_changed": {
        "text": "{task} drop-off changed from {previous} to {destination}",
        "text_zh": "{task}收货点从{previous}改为{destination}",
        "entities": ["task", "previous", "destination"], "position": "destination",
        "category": "logistics", "severity": "notice",
    },
    "lowalt.medical.prioritized": {
        "text": "Urgent request raised {task} priority to {priority}",
        "text_zh": "紧急请求将{task}优先级提高至 {priority}",
        "entities": ["task"], "category": "emergency", "severity": "notice",
    },
    "lowalt.inspection.defect_reported": {
        "text": "Defect reported at {site}; {task} released for inspection",
        "text_zh": "{site}收到缺陷报告，已释放{task}进行复查",
        "entities": ["site", "task"], "position": "site",
        "category": "inspection", "severity": "warning",
    },
    "lowalt.spraying.paused": {
        "text": "Operator paused spraying for {task}",
        "text_zh": "操作员已暂停{task}的喷洒作业", "entities": ["task"],
        "category": "agriculture", "severity": "notice",
    },
    "lowalt.spraying.resumed": {
        "text": "{task} resumed after the operator spraying pause",
        "text_zh": "操作员停喷时段结束，{task}恢复喷洒作业", "entities": ["task"],
        "category": "agriculture", "severity": "info",
    },
    "lowalt.food.preparation_started": {
        "text": "{restaurant} started preparing {task}",
        "text_zh": "{restaurant}开始制作{task}",
        "entities": ["restaurant", "task"],
        "position": "restaurant",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.food.ready": {
        "text": "{task} is ready at {restaurant} after {preparation_s} seconds of preparation",
        "text_zh": "{restaurant}完成{task}，备餐耗时 {preparation_s} 秒",
        "entities": ["restaurant", "task"],
        "position": "restaurant",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.food.preparation_delayed": {
        "text": "{restaurant} preparation delay: {delay_s} seconds; affected orders: {tasks}",
        "text_zh": "{restaurant}出餐延迟 {delay_s} 秒；受影响订单：{tasks}",
        "entities": ["restaurant"],
        "position": "restaurant",
        "category": "logistics",
        "severity": "warning",
    },
    "lowalt.food.delivered": {
        "text": "{vehicle} delivered {task} to {destination}, {ready_to_delivery_s} seconds after preparation",
        "text_zh": "{vehicle}已将{task}送达{destination}，出餐后耗时 {ready_to_delivery_s} 秒",
        "entities": ["task", "vehicle", "destination"],
        "position": "vehicle",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.created": {
        "text": "New {noun} for {destination}",
        "text_zh": "新增前往{destination}的{noun}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.assigned": {
        "text": "{vehicle} was assigned the {noun} for {destination}",
        "text_zh": "{vehicle}获派前往{destination}的{noun}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.started": {
        "text": "{vehicle} is heading to {origin} for the {noun}",
        "text_zh": "{vehicle}前往{origin}执行{noun}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.picked_up": {
        "text": "{vehicle} picked up the {noun} at {origin}",
        "text_zh": "{vehicle}在{origin}接取{noun}",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.completed": {
        "text": "{vehicle} {done} {destination} ({latency_s} s)",
        "text_zh": "{vehicle}在{destination}{done}（耗时 {latency_s} 秒）",
        "category": "logistics",
        "severity": "info",
    },
    "lowalt.task.failed": {
        "text": "The {noun} for {destination} failed: {reason}",
        "text_zh": "前往{destination}的{noun}失败：{reason}",
        "category": "logistics",
        "severity": "warning",
    },
    "lowalt.airspace.conflict": {
        "text": "{a} and {b} are too close",
        "text_zh": "{a}与{b}间隔过近",
        "category": "airspace",
        "severity": "warning",
    },
    "lowalt.airspace.conflict_resolved": {
        "text": "Separation between {a} and {b} restored ({action})",
        "text_zh": "{a}与{b}已恢复安全间隔（{action}）",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.airspace.geofence_entered": {
        "text": "{vehicle} entered {zone}",
        "text_zh": "{vehicle}进入{zone}",
        "category": "airspace",
        "severity": "warning",
    },
    "lowalt.airspace.zone_activated": {
        "text": "{zone} activated: {reason}",
        "text_zh": "{zone}已启用：{reason}",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.airspace.zone_deactivated": {
        "text": "{zone} deactivated: {reason}",
        "text_zh": "{zone}已停用：{reason}",
        "category": "airspace",
        "severity": "notice",
    },
    "lowalt.energy.low": {
        "text": "{vehicle} has low battery",
        "text_zh": "{vehicle}电量不足",
        "category": "energy",
        "severity": "warning",
    },
    "lowalt.energy.charging_started": {
        "text": "{vehicle} began charging at {pad}",
        "text_zh": "{vehicle}在{pad}开始充电",
        "category": "energy",
        "severity": "info",
    },
    "lowalt.energy.charging_finished": {
        "text": "{vehicle} finished charging at {pad}",
        "text_zh": "{vehicle}在{pad}完成充电",
        "category": "energy",
        "severity": "info",
    },
    "lowalt.weather.hold": {
        "text": "Flight held: {reason}",
        "text_zh": "飞行暂停：{reason}",
        "category": "weather",
        "severity": "warning",
    },
    "lowalt.weather.release": {
        "text": "Flight released: {reason}",
        "text_zh": "飞行恢复：{reason}",
        "category": "weather",
        "severity": "notice",
    },
}
EVENT_NARRATIVES.update(
    {
        "lowalt.task.preempted": {
            "text": "{vehicle} paused {task} for urgent {urgent_task}",
            "text_zh": "{vehicle}暂停{task}，优先执行紧急任务{urgent_task}",
            "entities": ["task", "vehicle", "urgent_task"],
            "position": "vehicle",
            "category": "emergency",
            "severity": "notice",
        },
        "lowalt.task.resumed": {
            "text": "{vehicle} resumed {task}",
            "text_zh": "{vehicle}恢复执行{task}",
            "entities": ["task", "vehicle"],
            "position": "vehicle",
            "category": "logistics",
            "severity": "info",
        },
        "lowalt.task.replanned": {
            "text": "{vehicle} changed route: {reason}",
            "text_zh": "{vehicle}调整航线：{reason}",
            "entities": ["task", "vehicle", "zone"],
            "position": "vehicle",
            "category": "airspace",
            "severity": "notice",
        },
        "lowalt.inspection.observed": {
            "text": "{vehicle} surveyed a section of {task}",
            "text_zh": "{vehicle}完成{task}的一段巡检观测",
            "entities": ["task", "vehicle"],
            "position": "position",
            "category": "inspection",
            "severity": "info",
        },
        "lowalt.spraying.applied": {
            "text": "{vehicle} treated a field section for {task}",
            "text_zh": "{vehicle}完成{task}的一段农田喷洒",
            "entities": ["task", "vehicle"],
            "position": "position",
            "category": "agriculture",
            "severity": "info",
        },
        "lowalt.patrol.visited": {
            "text": "{vehicle} reached a patrol point for {task}",
            "text_zh": "{vehicle}到达{task}的巡逻点",
            "entities": ["task", "vehicle"],
            "position": "position",
            "category": "security",
            "severity": "info",
        },
        "lowalt.site.closed": {
            "text": "Operator closed {site}",
            "text_zh": "操作员关闭{site}",
            "entities": ["site"],
            "position": "site",
            "category": "mobility",
            "severity": "warning",
        },
        "lowalt.mobility.pad_wait": {
            "text": "{vehicle} is waiting for a pad at {site}",
            "text_zh": "{vehicle}在{site}等待空闲起降位",
            "entities": ["task", "vehicle", "site"],
            "position": "vehicle",
            "category": "mobility",
            "severity": "notice",
        },
        "lowalt.energy.swap_started": {
            "text": "{vehicle} began a battery swap at {pad}",
            "text_zh": "{vehicle}在{pad}开始换电",
            "entities": ["vehicle", "pad"],
            "position": "vehicle",
            "category": "energy",
            "severity": "info",
        },
        "lowalt.energy.swap_finished": {
            "text": "{vehicle} received a charged battery at {pad}",
            "text_zh": "{vehicle}在{pad}换上充满电的电池",
            "entities": ["vehicle", "pad"],
            "position": "vehicle",
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
            ("Restaurant", ["lowalt:Site"]),
            ("Field", ["lowalt:Site"]),
            ("Vertiport", ["lowalt:Site", "oo:Facility"]),
            ("Depot", ["lowalt:Vertiport"]),
            ("ChargingPad", ["lowalt:Site"]),
            ("AirspaceZone", ["oo:ModelObject"]),
            ("Task", ["oo:Task"]),
            ("FoodOrder", ["lowalt:Task"]),
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
    # Detections the airspace engine publishes for the shared rules package.
    add("wind_mps", "UAV", NUMBER, "m/s")
    add("wind_limit_mps", "UAV", NUMBER, "m/s")
    add("conflict_partner", "UAV", STRING)
    add("conflict_gap_m", "UAV", PAIR, "m")
    for name in ("membership", "conflicts", "breached_zones"):
        add(name, "UAV", STRINGS)
    for name in ("weather_held", "charging", "returning_to_charge"):
        add(name, "UAV", BOOL)
    add("site_position", "Site", VECTOR, "m")
    add("site_open", "Site", BOOL)
    add("site_capacity", "Site", INT)
    add("food_preparation_s", "Restaurant", NUMBER, "s")
    for name in (
        "food_preparation_started_ns",
        "food_expected_ready_ns",
        "food_ready_ns",
        "food_picked_up_ns",
        "food_delivered_ns",
        "food_deadline_ns",
    ):
        add(name, "FoodOrder", INT, "ns")
    add("pad_power_w", "ChargingPad", NUMBER, "W")
    add("wind", "Environment", VECTOR, "m/s")
    add("zone_id", "AirspaceZone", STRING)
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
        "food.preparation_started": (
            {"task": STRING, "restaurant": STRING, "ready_ns": INT},
            (),
        ),
        "food.ready": (
            {"task": STRING, "restaurant": STRING, "preparation_s": NUMBER},
            (),
        ),
        "food.preparation_delayed": (
            {"restaurant": STRING, "delay_s": NUMBER, "tasks": STRINGS},
            (),
        ),
        "food.delivered": (
            {
                "task": STRING,
                "vehicle": STRING,
                "destination": STRING,
                "deadline_ns": INT,
                "ready_to_delivery_s": NUMBER,
                "on_time": BOOL,
            },
            (),
        ),
        "operator.restaurant_delay": (
            {"restaurant": STRING, "delay_s": {"type": "number", "minimum": 0.001}},
            (),
        ),
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
        "operator.change_dropoff": ({"task": target("Task"), "destination": target("Customer")}, ()),
        "operator.prioritize_medical": ({"task": target("Task"), "priority": {"type": "integer", "minimum": 101, "maximum": 1000}}, ()),
        "operator.report_defect": ({"task": target("Task"), "site": target("Site")}, ()),
        "operator.pause_spraying": ({"task": target("Task"), "duration_s": {"type": "number", "minimum": 1, "maximum": 120}}, ()),
        "operator.close_air_taxi_port": ({"site": target("Vertiport")}, ()),
        "operator.rejected": ({"operation": STRING, "reason": STRING, "provenance": PROVENANCE}, ()),
        "parcel.dropoff_changed": ({"task": STRING, "previous": STRING, "destination": STRING}, ()),
        "medical.prioritized": ({"task": STRING, "priority": INT}, ()),
        "inspection.defect_reported": ({"task": STRING, "site": STRING}, ()),
        "spraying.paused": ({"task": STRING, "until_ns": INT}, ()),
        "spraying.resumed": ({"task": STRING}, ()),
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
    messages: list[dict[str, Any]] = []
    for name, (m, o) in members.items():
        if name.startswith(("task.", "food.", "inspection.", "spraying.", "patrol.", "parcel.", "medical.")) or name == "site.closed":
            m, o = {**m, "provenance": PROVENANCE}, (*o, "provenance")
        subjects = []
        for key, type_id in {"task": "Task", "vehicle": "UAV", "restaurant": "Restaurant", "site": "Site", "origin": "Site", "destination": "Site", "previous": "Site"}.items():
            if key in m and m[key].get("type") == "string":
                subjects.append({"path": [key], "type_id": "lowalt:" + type_id})
        if "tasks" in m:
            subjects.append({"path": ["tasks", "*"], "type_id": "lowalt:Task"})
        messages.append({"id": "lowalt." + name, "kind": "event", "schema": record(m, o), **({"subjects": subjects} if subjects else {})})
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
    # Commands the rules package (aeroagentsim.behaviour-package/v1 chains) sends.
    for name, arguments, result_members in (
        (
            "energy.return_to_pad",
            {"vehicle": STRING, "reason": STRING},
            {"vehicle": STRING, "pad": STRING, "soc": NUMBER, "reason": STRING},
        ),
        (
            "airspace.resolve_conflict",
            {"a": STRING, "b": STRING},
            {"a": STRING, "b": STRING, "action": STRING},
        ),
        (
            "airspace.flight_control",
            {"vehicle": STRING, "action": STRING, "reason": STRING},
            {"vehicle": STRING, "action": STRING},
        ),
    ):
        messages.append(
            {
                "id": "lowalt." + name,
                "kind": "command",
                "schema": record(arguments),
                "result_schema": record(result_members, tuple(result_members)),
                "cancel_support": False,
            }
        )
    return {"types": types, "fields": fields, "messages": messages}
