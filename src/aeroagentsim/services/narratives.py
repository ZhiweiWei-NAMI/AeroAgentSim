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


TASK_KIND_CATEGORY = {
    "parcel": "logistics",
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
    "medical": "delivered urgent supplies to",
    "inspection": "finished inspecting",
    "survey": "finished surveying",
    "spraying": "finished spraying",
    "passenger": "dropped off the passenger at",
    "patrol": "finished patrolling",
    "incident_response": "responded at",
}

LOWALT_DEFAULTS: dict[str, dict[str, Any]] = {
    "lowalt.task.created": {
        "template": "{task} task created",
        "category": "mixed",
        "severity": "info",
        "entities": ["task"],
        "position": "task",
    },
    "lowalt.task.assigned": {
        "template": "{vehicle} assigned to {task} using {algorithm}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.started": {
        "template": "{vehicle} started {task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.picked_up": {
        "template": "{vehicle} picked up {task}",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.completed": {
        "template": "{vehicle} completed {task} in {latency_s} seconds",
        "category": "mixed",
        "severity": "info",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.task.failed": {
        "template": "{task} failed: {reason}",
        "category": "mixed",
        "severity": "warning",
        "entities": ["task", "vehicle"],
        "position": "vehicle",
    },
    "lowalt.airspace.conflict": {
        "template": "{a} and {b} separation conflict: {horizontal_m} m horizontal, {vertical_m} m vertical",
        "category": "airspace",
        "severity": "warning",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.conflict_resolved": {
        "template": "{a} and {b} conflict resolved: {action}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["a", "b"],
    },
    "lowalt.airspace.geofence_entered": {
        "template": "{vehicle} entered geofence zone {zone}",
        "category": "airspace",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
        "position": "vehicle",
    },
    "lowalt.airspace.zone_activated": {
        "template": "Zone {zone} activated: {reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.airspace.zone_deactivated": {
        "template": "Zone {zone} deactivated: {reason}",
        "category": "airspace",
        "severity": "notice",
        "entities": ["zone"],
    },
    "lowalt.energy.low": {
        "template": "{vehicle} battery low: {soc} state of charge",
        "category": "energy",
        "severity": "warning",
        "entities": ["vehicle"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_started": {
        "template": "{vehicle} charging started at {pad}: {soc} state of charge",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.energy.charging_finished": {
        "template": "{vehicle} charging finished at {pad}: {soc} state of charge",
        "category": "energy",
        "severity": "info",
        "entities": ["vehicle", "pad"],
        "position": "vehicle",
    },
    "lowalt.weather.hold": {
        "template": "Weather hold: {reason}",
        "category": "weather",
        "severity": "warning",
        "entities": ["vehicle", "zone"],
    },
    "lowalt.weather.release": {
        "template": "Weather released: {reason}",
        "category": "weather",
        "severity": "notice",
        "entities": ["vehicle", "zone"],
    },
    # Registry table spells the release alias without the lowalt prefix.
    "weather.release": {
        "template": "Weather released: {reason}",
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
        template = declaration["template"]
        if type(template) is not str or not template:
            raise ValueError(f"event_narratives.{schema_id}.template: string required")
        # Plain JSON placeholders only: every brace group must be a bare {field}.
        for match in _BRACES.finditer(template):
            if _PLACEHOLDER.fullmatch(match.group(0)) is None:
                raise ValueError(
                    f"event_narratives.{schema_id}.template: "
                    f"invalid placeholder {match.group(0)!r}"
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
    ) -> None:
        self._declarations = declarations
        self._labels = labels or {}
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
        return cls(declarations, position_fields, labels)

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
            self._seen.add(message.id)
            event = self._narrate(message)
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

        values: dict[str, str] = {}
        if message.schema_id.startswith("lowalt.task."):
            values.update(self._task_values(message.schema_id, payload))
        for name, value in payload.items():
            values[name] = self._render(value)
        for name in _fields(declaration["template"]):
            if name not in values:
                raise ValueError(
                    f"narratives.{message.schema_id}: missing placeholder {name}"
                )
        text = declaration["template"].format(**values)

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
            "text": text,
            "entities": entities,
        }
        position = self._position(payload, declaration, entities)
        if position is not None:
            event["position"] = list(position)
        return event

    def label(self, identity: str) -> str:
        """Friendly name of an entity: its authored label, else the id."""
        return self._labels.get(identity, identity)

    def _render(self, value: Any) -> str:
        """Plain text for a payload member; never a raw structure or ref."""
        if type(value) is str:
            return self.label(value) if value in self._labels else value
        identity = _entity_id(value)
        if identity is not None:
            return self.label(identity)
        if isinstance(value, (list, tuple)):
            parts = [self._render(item) for item in value]
            if len(parts) > 3:
                return ", ".join(parts[:3]) + f" and {len(parts) - 3} more"
            return ", ".join(parts) if parts else "none"
        if isinstance(value, dict):
            return "details"
        if value is None:
            return "unknown"
        return _plain(value)

    def _task_values(self, schema: str, payload: dict[str, Any]) -> dict[str, str]:
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
        return {
            "noun": TASK_NOUN.get(kind, "task"),
            "done": TASK_DONE.get(kind, "completed the task at"),
            "origin": self.label(info["origin"])
            if info.get("origin")
            else "its pickup",
            "destination": self.label(info["destination"])
            if info.get("destination")
            else "its destination",
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
