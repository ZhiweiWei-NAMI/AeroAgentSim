"""Business event narratives projected from committed kernel records.

Declarations map a committed event schema id to a plain-language template with a
category and severity. Rendering never fabricates data: a placeholder whose
payload member is absent raises ``ValueError``, optional entity links are simply
omitted when genuinely absent, and a position is only attached when the actual
payload carries an ENU vector or a linked entity has a stored ENU position.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.messages import Emit, Message
from aerokernel.operations import Create, FactWrite, Remove, RetractFact
from aerokernel.registry import MemoryRegistry
from aerokernel.values import thaw

from .storage import RunStorage

CATEGORIES = frozenset(
    {
        "logistics",
        "emergency",
        "inspection",
        "agriculture",
        "mobility",
        "security",
        "airspace",
        "weather",
        "energy",
        "mixed",
    }
)
SEVERITIES = frozenset({"info", "notice", "warning", "critical"})

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BRACES = re.compile(r"\{[^{}]*\}|[{}]")


# Finite shipped display vocabulary only; arbitrary authored names/prose stay intact.
_LABELS_ZH = {
    "Customer": "客户",
    "Shared city weather": "城市天气",
    "Vertiport West Plaza": "西广场起降场",
    "Vertiport East Roof": "东侧屋顶起降场",
    "Vertiport North Roof": "北侧屋顶起降场",
    "Vertiport Bund Roof": "外滩屋顶起降场",
    "West Plaza charging pad": "西广场充电桩",
    "East rooftop charging pad": "东侧屋顶充电桩",
    "Bund rooftop charging pad": "外滩屋顶充电桩",
    "East pad": "东侧充电桩",
    "North pad": "北侧充电桩",
    "Hospital rooftop pad": "医院屋顶起降位",
    "Clinic rooftop": "诊所屋顶",
    "Customer rooftop east": "东侧客户屋顶",
    "North road power-line start": "北侧道路输电线路起点",
    "North road survey return point": "北侧道路巡检返航点",
    "Powerline corridor": "输电线路走廊",
    "Power-line survey drone": "输电线路巡检无人机",
    "Field sprayer": "农田喷洒无人机",
    "Field-edge battery station": "田边换电站",
    "Field-edge battery swap pad": "田边换电位",
    "Field entry": "农田入口",
    "Field exit": "农田出口",
    "Field start": "农田起点",
    "Field end": "农田终点",
    "Huangpu edge demonstration field": "黄浦边缘示范农田",
    "Guard return": "巡逻返航点",
    "Incident site": "事件现场",
    "Temporary incident no-fly zone": "事故临时禁飞区",
    "Huangpu road flight corridor": "黄浦道路飞行走廊",
    "East west corridor": "东西向走廊",
    "North corridor": "北侧走廊",
    "Landmark rooftop exclusion": "地标屋顶禁飞区",
    "Edge coordinator": "边缘协调器",
    "Lane incident": "事故车道",
    "Lane adjacent": "相邻车道",
    "Adjacent lane bypass": "相邻车道绕行路线",
}
_NUMBERED_LABELS_ZH = {
    "Drone": "无人机", "UAV": "无人机", "Survey drone": "巡检无人机",
    "Patrol drone": "巡逻无人机", "Sprayer": "喷洒无人机", "Air taxi": "空中出租车",
    "Customer": "客户", "Customer rooftop": "客户屋顶", "Vertiport": "起降场",
    "Pad": "充电桩", "Charging pad": "充电桩", "Depot": "配送站",
    "Vehicle": "车辆", "Car": "车辆", "Task": "任务",
    "Parcel mission": "包裹配送任务", "Medical mission": "医疗配送任务",
    "Passenger mission": "载客任务", "Spraying mission": "喷洒任务",
    "Inspection mission": "巡检任务", "Patrol mission": "巡逻任务",
    "Incident Response mission": "事故响应任务", "Route vehicle": "车辆路线",
    "Road vehicle": "道路车辆", "Road task vehicle": "道路车辆任务",
    "Medical delivery": "医疗配送", "District patrol": "街区巡逻",
    "Background patrol": "背景巡逻", "Incident capture": "事故现场图像",
    "Incident": "事故", "Bg": "背景机",
}
_NUMBERED_LABEL = re.compile(
    "(" + "|".join(re.escape(name) for name in _NUMBERED_LABELS_ZH) + r") ([A-Z]?\d+)\Z"
)
_ENUMS_ZH = {
    "kind": {"person_fall": "行人跌倒"},
    "choice": {
        "respond_now": "立即响应", "request_handoff": "请求交接",
        "finish_then_respond": "完成任务后响应", "report_only": "仅上报",
        "accept": "接受", "decline": "拒绝",
    },
    "mode": {"prescribed": "预设规则", "agent": "智能体决策"},
    "algorithm": {
        "nearest": "就近分配", "auction": "竞价分配", "hungarian": "批量最优匹配",
        "llm": "语言模型智能体", "straight": "直线航线", "corridor_astar": "安全走廊航线",
        "coverage": "区域覆盖", "survey": "巡检航点", "patrol": "巡逻环线",
        "altitude_layers": "高度分层", "hold_and_go": "等待放行", "threshold_return": "低电量返航",
    },
    "action": {
        "altitude_layers": "高度分层", "hold_and_go": "等待放行",
        "altitude": "调整高度", "hold": "等待", "release": "放行", "reroute": "改道",
        "stop": "停止", "already resolving": "正在解除冲突", "observed separation": "已恢复安全间隔",
    },
    "reason": {
        "battery low": "电量不足", "battery reserve": "预留电量不足",
        "battery below reserve": "电量低于预留阈值", "active no-fly zone": "禁飞区生效",
        "no-fly zone": "禁飞区限制", "vertiport closed": "起降场关闭",
        "wind exceeds vehicle limit": "风速超过飞行器上限",
        "wind within vehicle limit": "风速恢复到允许范围", "active interval": "设定生效时段",
        "incident": "事故影响", "cleared": "限制已解除", "weather": "天气限制",
        "wind subsided": "风速减弱", "zone closed": "空域关闭",
        "separation conflict": "间隔冲突", "charging finished": "充电完成",
        "charging algorithm": "充电策略", "traffic incident": "交通事故影响",
    },
}


def _chinese_label(label: str) -> str:
    if label in _LABELS_ZH:
        return _LABELS_ZH[label]
    match = _NUMBERED_LABEL.fullmatch(label)
    if match:
        return f"{_NUMBERED_LABELS_ZH[match[1]]} {match[2]}"
    match = re.fullmatch(r"(Incident drone|Route uav) (Alpha|Bravo|Bg \d+)", label)
    if match:
        family = "事故响应无人机" if match[1] == "Incident drone" else "无人机路线"
        name = match[2].replace("Bg ", "背景机 ")
        return f"{family} {name}"
    match = re.fullmatch(r"Road (task vehicle|vehicle) (reporter|(?:incident )?[ab])", label)
    if match:
        family = "道路车辆任务" if match[1] == "task vehicle" else "道路车辆"
        name = "报告车" if match[2] == "reporter" else match[2].replace("incident ", "事故 ")
        return f"{family} {name}"
    return label


TASK_KIND_CATEGORY = {
    "parcel": "logistics",
    "food": "logistics",
    "medical": "emergency",
    "inspection": "inspection",
    "survey": "inspection",
    "spraying": "agriculture",
    "passenger": "mobility",
    "patrol": "security",
    "incident_response": "emergency",
}

# Plain nouns and completion phrases per task kind, used by task narratives.
TASK_NOUN = {
    "parcel": "parcel order",
    "food": "meal order",
    "medical": "urgent medical order",
    "inspection": "inspection job",
    "survey": "survey job",
    "spraying": "spraying job",
    "passenger": "passenger trip",
    "patrol": "patrol round",
    "incident_response": "incident response",
}
TASK_DONE = {
    "parcel": "delivered the parcel to",
    "food": "delivered the meal to",
    "medical": "delivered urgent supplies to",
    "inspection": "finished inspecting",
    "survey": "finished surveying",
    "spraying": "finished spraying",
    "passenger": "dropped off the passenger at",
    "patrol": "finished patrolling",
    "incident_response": "responded at",
}

TASK_NOUN_ZH = {
    "parcel": "包裹配送任务",
    "food": "外卖订单",
    "medical": "紧急医疗配送任务",
    "inspection": "巡检任务",
    "survey": "测绘任务",
    "spraying": "喷洒任务",
    "passenger": "载客任务",
    "patrol": "巡逻任务",
    "incident_response": "事故响应任务",
}
TASK_DONE_ZH = {
    "parcel": "完成包裹投递",
    "food": "送达餐食",
    "medical": "送达紧急医疗物资",
    "inspection": "完成巡检",
    "survey": "完成测绘",
    "spraying": "完成喷洒",
    "passenger": "送达乘客",
    "patrol": "完成巡逻",
    "incident_response": "完成事故响应",
}

LOWALT_DEFAULTS: dict[str, dict[str, Any]] = {
    "lowalt.task.created": {
        "template": "{task} task created",
        "template_zh": "任务{task}已创建",
        "category": "mixed",
        "severity": "info",
        "entities": ["task"],
        "position": "task",
    },
    "lowalt.task.assigned": {
        "template": "{vehicle} assigned to {task} using {algorithm}",
        "template_zh": "通过{algorithm}将{task}分配给{vehicle}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.started": {
        "template": "{vehicle} started {task}",
        "template_zh": "{vehicle}开始执行{task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.picked_up": {
        "template": "{vehicle} picked up {task}",
        "template_zh": "{vehicle}接取任务{task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.completed": {
        "template": "{vehicle} completed {task} in {latency_s} seconds",
        "template_zh": "{vehicle}完成{task}，耗时 {latency_s} 秒",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.failed": {
        "template": "{task} failed: {reason}",
        "template_zh": "{task}失败：{reason}",
        "category": "mixed",
        "severity": "warning",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.airspace.conflict": {
        "template": "{a} and {b} separation conflict: {horizontal_m} m horizontal, {vertical_m} m vertical",
        "template_zh": "{a}与{b}间隔冲突：水平 {horizontal_m} 米，垂直 {vertical_m} 米",
        "category": "airspace",
        "severity": "warning",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.conflict_resolved": {
        "template": "{a} and {b} conflict resolved: {action}",
        "template_zh": "{a}与{b}的冲突已解除：{action}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.geofence_entered": {
        "template": "{vehicle} entered geofence zone {zone}",
        "template_zh": "{vehicle}进入电子围栏区域{zone}",
        "category": "airspace",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
        "position": "vehicle",
    },
    "lowalt.airspace.zone_activated": {
        "template": "Zone {zone} activated: {reason}",
        "template_zh": "区域{zone}已启用：{reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.airspace.zone_deactivated": {
        "template": "Zone {zone} deactivated: {reason}",
        "template_zh": "区域{zone}已停用：{reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.energy.low": {
        "template": "{vehicle} battery low: {soc} state of charge",
        "template_zh": "{vehicle}电量不足，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "warning",
        "entities": ["vehicle"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_started": {
        "template": "{vehicle} charging started at {pad}: {soc} state of charge",
        "template_zh": "{vehicle}在{pad}开始充电，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_finished": {
        "template": "{vehicle} charging finished at {pad}: {soc} state of charge",
        "template_zh": "{vehicle}在{pad}完成充电，剩余电量比例为 {soc}",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.weather.hold": {
        "template": "Weather hold: {reason}",
        "template_zh": "因天气暂停飞行：{reason}",
        "category": "weather",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
    },
    "lowalt.weather.release": {
        "template": "Weather released: {reason}",
        "template_zh": "天气限制已解除：{reason}",
        "category": "weather",
        "severity": "notice",
        "entities": ["vehicle", "zone"],
    },
    # Registry table spells the release alias without the lowalt prefix.
    "weather.release": {
        "template": "Weather released: {reason}",
        "template_zh": "天气限制已解除：{reason}",
        "category": "weather",
        "severity": "notice",
        "entities": ["vehicle", "zone"],
    },
}


def _is_enu(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(type(v) in (int, float) and math.isfinite(v) for v in value)
    )


def validate_declarations(data: dict[str, Any]) -> None:
    """Validate a ``schemaId -> declaration`` mapping; raise ValueError."""
    if not isinstance(data, dict) or not data:
        raise ValueError("event_narratives: mapping required")
    for schema_id, declaration in data.items():
        if type(schema_id) is not str or not schema_id:
            raise ValueError("event_narratives: schema id must be a nonempty string")
        if not isinstance(declaration, dict):
            raise ValueError(f"event_narratives.{schema_id}: mapping required")
        allowed = {
            "template",
            "template_zh",
            "category",
            "severity",
            "entities",
            "position",
            "hidden",
        }
        if extra := set(declaration) - allowed:
            raise ValueError(
                f"event_narratives.{schema_id}: unknown keys {sorted(extra)}"
            )
        for field in ("template", "category", "severity"):
            if field not in declaration:
                raise ValueError(f"event_narratives.{schema_id}: missing {field}")
        for field in ("template", "template_zh"):
            if field not in declaration:
                continue
            template = declaration[field]
            if type(template) is not str or not template.strip():
                raise ValueError(
                    f"event_narratives.{schema_id}.{field}: nonempty string required"
                )
            # Plain JSON placeholders only: every brace group is a bare {field}.
            for match in _BRACES.finditer(template):
                if _PLACEHOLDER.fullmatch(match.group(0)) is None:
                    raise ValueError(
                        f"event_narratives.{schema_id}.{field}: "
                        f"invalid placeholder {match.group(0)!r}"
                    )
        if "template_zh" in declaration and set(_fields(declaration["template_zh"])) != set(
            _fields(declaration["template"])
        ):
            raise ValueError(
                f"event_narratives.{schema_id}.template_zh: "
                "placeholders must match template"
            )
        if declaration["category"] not in CATEGORIES:
            raise ValueError(f"event_narratives.{schema_id}.category: unknown category")
        if declaration["severity"] not in SEVERITIES:
            raise ValueError(f"event_narratives.{schema_id}.severity: unknown severity")
        entities = declaration.get("entities", [])
        if not isinstance(entities, list) or any(
            type(name) is not str or not name for name in entities
        ):
            raise ValueError(
                f"event_narratives.{schema_id}.entities: list of strings required"
            )
        if "position" in declaration and (
            type(declaration["position"]) is not str or not declaration["position"]
        ):
            raise ValueError(f"event_narratives.{schema_id}.position: string required")


def _entity_id(value: Any) -> str | None:
    """Human id from a declared entity field: string or {$ref: {...}}."""
    if type(value) is str and value:
        return value
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, dict) and type(ref.get("id")) is str and ref["id"]:
            return str(ref["id"])
        if type(value.get("id")) is str and value["id"]:
            return str(value["id"])
    return None


def _merge(target: dict[str, dict[str, Any]], source: dict[str, Any]) -> None:
    for schema_id, declaration in source.items():
        if schema_id in target and target[schema_id] != declaration:
            raise ValueError(
                f"event_narratives: conflicting declaration for {schema_id}"
            )
        target[schema_id] = declaration


class NarrativeProjector:
    """Project committed kernel records into plain-language event feed rows."""

    def __init__(
        self,
        declarations: dict[str, dict[str, Any]],
        position_fields: set[str] | None = None,
        labels: dict[str, str] | None = None,
        labels_zh: dict[str, str] | None = None,
    ) -> None:
        validate_declarations(declarations)
        self._declarations = declarations
        self._labels = labels or {}
        self._labels_zh = labels_zh or {}
        self._task_info: dict[str, dict[str, str]] = {}
        self._seen: set[str] = set()
        self._position_fields = position_fields or set()
        self._task_kinds: dict[str, str] = {}
        # Entity identity -> latest stored ENU position (ENU only).
        self._positions: dict[tuple[str, str, str, int], tuple[float, ...]] = {}
        self._by_id: dict[str, list[tuple[str, str, str, int]]] = {}

    @classmethod
    def from_directory(cls, path: str | Path) -> NarrativeProjector:
        """Load declarations from defaults, pack, behaviour IR and scenario."""
        base = Path(path)
        declarations: dict[str, dict[str, Any]] = {}

        def read(name: str) -> dict[str, Any]:
            file = base / name
            with file.open("r", encoding="utf-8") as handle:
                loaded: dict[str, Any] = json.load(handle)
                return loaded

        for key, value in LOWALT_DEFAULTS.items():
            declarations[key] = dict(value)
        from ..packs.lowalt.overlay import EVENT_NARRATIVES as LOWALT_PACK
        from ..packs.traffic_accident.narratives import EVENT_NARRATIVES

        # Pack declarations are authoritative statements of what the pack's
        # engines emit; they pin the defaults truthfully (defaults stay for
        # schemas no pack declares).
        for key, value in LOWALT_PACK.items():
            own = declarations.get(key, {})
            # Task events carry the pack's per-kind category map; render the
            # category from the actual task kind instead of a flat default.
            per_kind = value.get("category_by_kind")
            declarations[key] = {
                "template": value["text"],
                **({"template_zh": value["text_zh"]} if "text_zh" in value else {}),
                "category": "mixed" if per_kind else value["category"],
                "severity": value["severity"],
                "entities": value.get("entities", [n for n in own.get("entities", [])]),
                **(
                    {"position": value["position"]}
                    if "position" in value
                    else ({"position": own["position"]} if "position" in own else {})
                ),
            }
            del own
        for key, value in EVENT_NARRATIVES.items():
            declarations[key] = dict(value)

        behaviour = (
            read("behaviour.ir.json") if (base / "behaviour.ir.json").exists() else {}
        )
        package_declarations: dict[str, dict[str, Any]] = {}
        packages = behaviour.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError("behaviour.ir.json: packages must be a list")
        for package in packages:
            document = package.get("document", {}) if isinstance(package, dict) else {}
            declared = document.get("event_narratives")
            if declared:
                validate_declarations(declared)
                _merge(package_declarations, declared)

        declarations.update(package_declarations)
        scenario = read("scenario.json")
        registry = MemoryRegistry.from_data(read("runtime.registry.json"))
        position_fields: set[str] = set()
        presentations = {
            item["positionField"]
            for item in scenario.get("presentation", [])
            if item["frame"] == "enu"
        }
        for field in registry.fields:
            unit = field.metadata.get("unit")
            frame = field.metadata.get("frame")
            unit = unit.get("symbol") if isinstance(unit, Mapping) else unit
            frame = frame.get("convention") if isinstance(frame, Mapping) else frame
            if (
                (field.id in presentations or "position" in field.id)
                and unit == "m"
                and frame == "enu"
                and field.metadata.get("role") != "config"
            ):
                position_fields.add(field.id)
        overrides = scenario.get("event_narratives")
        if overrides:
            validate_declarations(overrides)
            for key, value in overrides.items():
                declarations[key] = dict(value)
        labels = {
            e["id"]: e["label"]
            for e in scenario.get("entities", [])
            if isinstance(e, dict)
            and type(e.get("id")) is str
            and type(e.get("label")) is str
            and e["label"]
        }
        labels_zh = {
            e["id"]: e["label_zh"]
            for e in scenario.get("entities", [])
            if isinstance(e, dict)
            and type(e.get("id")) is str
            and type(e.get("label_zh")) is str
            and e["label_zh"].strip()
        }
        return cls(declarations, position_fields, labels, labels_zh)

    def cursor(self) -> dict[str, Any]:
        """Dedup/position state needed to restore this projector later."""
        return {
            "seen": sorted(self._seen),
            "task_kinds": dict(self._task_kinds),
            "task_info": {k: dict(v) for k, v in self._task_info.items()},
            "positions": [
                [*key, list(value)] for key, value in self._positions.items()
            ],
        }

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        """Restore state produced by :meth:`cursor`; complements from_directory."""
        if not isinstance(cursor, dict):
            raise ValueError("narrative cursor: mapping required")
        self._task_kinds = {
            str(task): str(kind) for task, kind in cursor["task_kinds"].items()
        }
        self._task_info = {
            str(task): {str(k): str(v) for k, v in info.items()}
            for task, info in cursor.get("task_info", {}).items()
        }
        self._seen.clear()
        self._positions.clear()
        self._by_id.clear()
        for message_id in cursor["seen"]:
            self._seen.add(str(message_id))
        for run_id, epoch, entity_id, generation, value in cursor["positions"]:
            restored = (run_id, epoch, entity_id, int(generation))
            self._positions[restored] = tuple(float(v) for v in value)
            self._key_hint(restored, entity_id)

    @classmethod
    def projection_context(
        cls, path: str | Path, cursor: dict[str, Any]
    ) -> NarrativeProjector:
        """Restore a projector instance from a stored cursor context."""
        projector = cls.from_directory(path)
        projector.restore_cursor(cursor)
        return projector

    # -- record consumption ------------------------------------------------

    def consume(self, raw_record: dict[str, Any]) -> list[dict[str, Any]]:
        """Narrate newly committed Emit messages once; dedup by message id."""
        record = expand_record(raw_record)
        events: list[dict[str, Any]] = []
        for item in record.get("items", []):
            if not isinstance(item, dict):
                continue
            if "proposal" in item:
                op = decode_record(item["proposal"])
                if isinstance(op, Create):
                    key = self._key(op.ref)
                    self._key_hint(key, op.ref.id)
                elif isinstance(op, Remove):
                    self._positions.pop(self._key(op.ref), None)
                elif isinstance(op, FactWrite):
                    self._track_position(op)
                elif isinstance(op, RetractFact) and op.key[1] in self._position_fields:
                    self._positions.pop(self._key(op.key[0]), None)
            if "message" not in item:
                continue
            proposal = decode_record(item["proposal"]) if "proposal" in item else None
            if not isinstance(proposal, Emit):
                continue
            message = decode_record(item["message"])
            if not isinstance(message, Message) or message.kind != "event":
                continue
            if message.id in self._seen:
                continue
            event = self._narrate(message)
            # Failed interpolation must remain an error on retry, not disappear
            # behind dedup state in this projector's next request.
            self._seen.add(message.id)
            if event is not None:
                events.append(event)
        return events

    def _key(self, ref: Any) -> tuple[str, str, str, int]:
        return (ref.run_id, ref.epoch, ref.id, ref.generation)

    def _key_hint(self, key: tuple[str, str, str, int], entity_id: str) -> None:
        keys = self._by_id.setdefault(entity_id, [])
        if key not in keys:
            keys.append(key)

    def _track_position(self, op: FactWrite) -> None:
        value = op.value
        entity_id = op.key[0].id
        field = op.key[1]
        vector: tuple[float, ...] | None = None
        if field not in self._position_fields:
            return
        if isinstance(value, (list, tuple)):
            members = list(value)
            if _is_enu(value):
                vector = tuple(float(v) for v in members)
        elif isinstance(value, dict) and _is_enu(value.get("position")):
            members = list(value["position"])
            vector = tuple(float(v) for v in members)
        del field
        if vector is None:
            return
        key = self._key(op.key[0])
        self._positions[key] = vector
        self._key_hint(key, entity_id)

    def _stored_position(self, value: Any) -> tuple[float, ...] | None:
        if isinstance(value, dict) and "$ref" in value:
            ref = value["$ref"]
            return self._positions.get(
                (ref["run_id"], ref["epoch"], ref["id"], ref["generation"])
            )
        entity_id = _entity_id(value)
        if entity_id is None:
            return None
        for key in reversed(self._by_id.get(entity_id, [])):
            position = self._positions.get(key)
            if position:
                return position
        return None

    # -- rendering ---------------------------------------------------------

    def _narrate(self, message: Message) -> dict[str, Any] | None:
        declaration = self._declarations.get(message.schema_id)
        if declaration is None:
            return None  # Undeclared kernel events are ignored intentionally.
        if declaration.get("hidden"):
            return None  # Engine bookkeeping stays in the journal only.
        payload = thaw(message.payload)
        if not isinstance(payload, dict):
            raise ValueError(
                f"narratives.{message.schema_id}: payload mapping required"
            )

        category = declaration["category"]
        if message.schema_id.startswith("lowalt.task."):
            task = _entity_id(payload.get("task"))
            if message.schema_id == "lowalt.task.created":
                kind = payload.get("kind")
                if kind not in TASK_KIND_CATEGORY or task is None:
                    raise ValueError(
                        "lowalt.task.created: task and known kind required"
                    )
                self._task_kinds[task] = kind
            if category == "mixed" and task in self._task_kinds:
                category = TASK_KIND_CATEGORY[self._task_kinds[task]]

        texts: dict[str, str] = {}
        for language, field, output in (
            ("en", "template", "text"), ("zh", "template_zh", "text_zh")
        ):
            if field not in declaration:
                continue
            values: dict[str, str] = {}
            if message.schema_id.startswith("lowalt.task."):
                values.update(self._task_values(message.schema_id, payload, language))
            for name, value in payload.items():
                if (
                    language == "zh"
                    and (message.schema_id.startswith("lowalt.") or message.schema_id == "weather.release")
                    and type(value) is str
                    and name in _ENUMS_ZH
                ):
                    values[name] = _ENUMS_ZH[name].get(value, value)
                else:
                    values[name] = self._render(value, language)
            for name in _fields(declaration[field]):
                if name not in values:
                    raise ValueError(
                        f"narratives.{message.schema_id}.{field}: "
                        f"missing placeholder {name}"
                    )
            texts[output] = declaration[field].format(**values)

        entities: list[str] = []
        for name in declaration.get("entities", []):
            if name in payload:
                entity_id = _entity_id(payload[name])
                if entity_id is not None and entity_id not in entities:
                    entities.append(entity_id)

        event: dict[str, Any] = {
            "at_ns": str(message.at.ns),
            "category": category,
            "severity": declaration["severity"],
            **texts,
            "entities": entities,
        }
        position = self._position(payload, declaration, entities)
        if position is not None:
            event["position"] = list(position)
        return event

    def label(self, identity: str, language: str = "en") -> str:
        """Authored zh label, finite shipped translation, English label, then id."""
        label = self._labels.get(identity, identity)
        if language == "zh":
            return self._labels_zh.get(identity, _chinese_label(label))
        return label

    def _render(self, value: Any, language: str = "en") -> str:
        """Plain text for a payload member; identities/authored strings are intact."""
        if type(value) is str:
            return (
                self.label(value, language)
                if value in self._labels or language == "zh" and value in self._labels_zh
                else value
            )
        identity = _entity_id(value)
        if identity is not None:
            return self.label(identity, language)
        if isinstance(value, (list, tuple)):
            parts = [self._render(item, language) for item in value]
            separator = "、" if language == "zh" else ", "
            if len(parts) > 3:
                extra = (
                    f"，另有 {len(parts) - 3} 项"
                    if language == "zh"
                    else f" and {len(parts) - 3} more"
                )
                return separator.join(parts[:3]) + extra
            return separator.join(parts) if parts else ("无" if language == "zh" else "none")
        if isinstance(value, dict):
            return "详情" if language == "zh" else "details"
        if value is None:
            return "未知" if language == "zh" else "unknown"
        return _plain(value)

    def _task_values(
        self, schema: str, payload: dict[str, Any], language: str = "en"
    ) -> dict[str, str]:
        """Derived placeholders {noun}, {done}, {origin}, {destination} per task."""
        task = _entity_id(payload.get("task"))
        if schema == "lowalt.task.created" and task is not None:
            self._task_info[task] = {
                "kind": str(payload.get("kind", "")),
                "origin": _entity_id(payload.get("origin")) or "",
                "destination": _entity_id(payload.get("destination")) or "",
            }
        info = self._task_info.get(task or "", {})
        kind = info.get("kind", "")
        chinese = language == "zh"
        nouns, completions = (TASK_NOUN_ZH, TASK_DONE_ZH) if chinese else (TASK_NOUN, TASK_DONE)
        return {
            "noun": nouns.get(kind, "任务" if chinese else "task"),
            "done": completions.get(kind, "完成任务" if chinese else "completed the task at"),
            "origin": self.label(info["origin"], language)
            if info.get("origin")
            else ("取件点" if chinese else "its pickup"),
            "destination": self.label(info["destination"], language)
            if info.get("destination")
            else ("目的地" if chinese else "its destination"),
        }

    def _position(
        self,
        payload: dict[str, Any],
        declaration: dict[str, Any],
        entities: list[str],
    ) -> tuple[float, ...] | None:
        position_field = declaration.get("position")
        if position_field is not None and position_field in payload:
            value = payload[position_field]
            if _is_enu(value):
                return tuple(float(v) for v in value)
            return self._stored_position(value)
        for name in declaration.get("entities", []):
            if name in payload:
                stored = self._stored_position(payload[name])
                if stored is not None:
                    return stored
        return None


def _fields(template: str) -> list[str]:
    return [match.group(1) for match in _PLACEHOLDER.finditer(template)]


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        rounded = round(value, 1)
        return str(int(rounded)) if rounded.is_integer() else str(rounded)
    return str(value)


def projection_context(path: Path, cursor: int) -> NarrativeProjector:
    """Restore actual narrative state from the committed prefix before cursor."""
    projector = NarrativeProjector.from_directory(path)
    storage = RunStorage(path)
    start = 1
    while start < cursor:
        records = storage.records(start, min(1024, cursor - start))
        if not records:
            break
        for record in records:
            projector.consume(record)
        start = records[-1]["index"] + 1
    return projector
