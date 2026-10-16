"""Explicit, editable point-mass and passive-workflow authoring templates.

This module also hosts the scenario-template registry (design §1): typed,
user-tunable knobs applied as pure JSON-pointer or named structural hooks.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.resources
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.scenario.loader import UniqueLoader

POS = "aas.studio.position_enu_m"
VEL = "aas.studio.velocity_enu_m_s"
ENERGY = "aas.studio.energy_j"
STATE = "aas.studio.state"
VECTOR = {"type": "vector", "items": {"type": "number"}, "length": 3}


def starter(identifier: str, ontology: Path) -> dict[str, Any]:
    """A blank draft; parameters below are declared simulation assumptions."""
    return {
        "format": "aeroagentsim.scenario/v1",
        "id": identifier,
        "registry": {
            "compile": {
                "root": str(ontology),
                "types": ["oo:ModelObject"],
                "fields": [],
                "relations": [],
            },
            "types": [
                {
                    "id": "aas:StudioFacility",
                    "parents": ["oo:ModelObject"],
                    "abstract": False,
                },
                {
                    "id": "aas:StudioAirspace",
                    "parents": ["oo:ModelObject"],
                    "abstract": False,
                },
            ],
            "fields": [
                {
                    "id": POS,
                    "type": "oo:ModelObject",
                    "schema": VECTOR,
                    "metadata": {
                        "unit": "m",
                        "frame": "enu",
                        "transform_revision": "studio-local-1",
                        "role": "state",
                        "clock": "canonical",
                    },
                },
                {
                    "id": VEL,
                    "type": "oo:ModelObject",
                    "schema": VECTOR,
                    "metadata": {
                        "unit": "m/s",
                        "frame": "enu",
                        "role": "derived",
                        "clock": "canonical",
                    },
                },
                {
                    "id": ENERGY,
                    "type": "oo:ModelObject",
                    "schema": {"type": "number"},
                    "metadata": {"unit": "J", "role": "state", "clock": "canonical"},
                },
                {
                    "id": STATE,
                    "type": "oo:ModelObject",
                    "schema": {"type": "string"},
                    "metadata": {"role": "state", "clock": "canonical"},
                },
                {
                    "id": "aas.studio.airspace",
                    "type": "aas:StudioAirspace",
                    "schema": {
                        "type": "record",
                        "members": {
                            "polygon": {
                                "type": "array",
                                "items": {
                                    "type": "vector",
                                    "length": 2,
                                    "items": {"type": "number"},
                                },
                            },
                            "floor_m": {"type": "number"},
                            "ceiling_m": {"type": "number"},
                        },
                        "required": ["polygon", "floor_m", "ceiling_m"],
                        "extra": False,
                    },
                    "metadata": {"unit": "m", "frame": "enu", "role": "config"},
                },
            ],
            "messages": [],
        },
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "presentation": [],
        "run": {
            "pacing": "fast",
            "seed": 1,
            "until_ns": 600_000_000_000,
            "advance_ns": 100_000_000,
        },
        "outputs": {"durability": "flush"},
    }


def workflow() -> dict[str, Any]:
    return {
        "plugin": "workflow",
        "config": {
            "produces": [],
            "consumes": [],
            "emits": [],
            "subscribes": [],
            "targets": [],
            "lifecycle": True,
            "machines": [],
        },
    }


def motion(type_id: str) -> dict[str, Any]:
    return {
        "plugin": "kinematic",
        "config": {
            "type_id": type_id,
            "position_field": POS,
            "velocity_field": VEL,
            "energy_field": ENERGY,
            "step_ns": 100_000_000,
            "frame": {
                "convention": "enu",
                "unit": "m",
                "transform_revision": "studio-local-1",
            },
            "max_speed_m_s": 10.0,
            "max_accel_m_s2": 5.0,
            "energy": {"capacity_j": 100_000.0, "idle_w": 20.0, "per_m_j": 5.0},
            "commands": {
                "move_to": "aas.studio.move_to",
                "hold": "aas.studio.hold",
                "stop": "aas.studio.stop",
            },
            "arrival_schema": "aas.studio.arrived",
            "arrival_topic": "studio-arrival",
            "lifecycle": True,
            "model": "enu_point_mass",
            "arrival_payload": {"entity": "$entity", "position": "$position"},
            "result_fields": {
                "entity": "entity",
                "position": "position",
                "reason": "reason",
            },
        },
    }


def motion_messages() -> list[dict[str, Any]]:
    result = {
        "type": "record",
        "members": {
            "entity": {"type": "string"},
            "position": VECTOR,
            "reason": {"type": "string"},
        },
        "required": [],
        "extra": False,
    }
    messages = []
    for action in ("move_to", "hold", "stop"):
        members: dict[str, Any] = {"entity": {"type": "string"}}
        if action == "move_to":
            members["target"] = VECTOR
        messages.append(
            {
                "id": "aas.studio." + action,
                "kind": "command",
                "schema": {
                    "type": "record",
                    "members": members,
                    "required": list(members),
                    "extra": False,
                },
                "result_schema": result,
                "feedback_schema": result,
                "cancel_support": True,
            }
        )
    messages.append(
        {
            "id": "aas.studio.arrived",
            "kind": "event",
            "schema": {
                "type": "record",
                "members": {"entity": {"type": "string"}, "position": VECTOR},
                "required": ["entity", "position"],
                "extra": False,
            },
        }
    )
    return messages


def demo_source(name: str) -> Path:
    """Locate shipped demo inputs: installed package data first, then the repo."""
    if name != "traffic-accident":
        raise ValueError("template: unknown demo")
    resources = importlib.resources.files("aeroagentsim") / "demo_data" / name
    if (resources / "scenario.yaml").is_file():
        return Path(str(resources))
    # Editable/source checkout: the repository tree is the real resource root.
    source = Path(__file__).resolve().parents[3] / "scenarios" / "demos" / name
    if (source / "scenario.yaml").is_file():
        return source
    raise FileNotFoundError("traffic-accident template inputs are not installed")


@dataclasses.dataclass(frozen=True)
class PreparedTemplate:
    document: dict[str, Any]
    base: Path
    knobs: dict[str, Any]
    metadata: dict[str, Any]

    def run_metadata(self) -> dict[str, Any]:
        """Pin authored display titles beside the actual applied knob values."""
        return {
            "id": self.metadata["id"],
            "title": self.metadata["title"],
            **({"title_zh": self.metadata["title_zh"]} if "title_zh" in self.metadata else {}),
            "knobs": copy.deepcopy(self.knobs),
        }


def _knob_value(knob: dict[str, Any], value: Any) -> None:
    import math

    kind, name = knob["type"], knob["id"]
    valid = {
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "choice": any(
            type(value) is type(option) and value == option
            for option in knob.get("options", [])
        ),
    }[kind]
    if not valid:
        raise ValueError(
            f"knob {name}: expected {kind}"
            + (f" from {knob['options']}" if kind == "choice" else "")
        )
    if kind in {"integer", "number"}:
        if not math.isfinite(value):
            raise ValueError(f"knob {name}: finite number required")
        if "min" in knob and value < knob["min"]:
            raise ValueError(f"knob {name}: must be at least {knob['min']}")
        if "max" in knob and value > knob["max"]:
            raise ValueError(f"knob {name}: must be at most {knob['max']}")


def _pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    import re

    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError(f"knob pointer {pointer!r}: invalid JSON pointer")
    parts = [
        part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")
    ]
    target: Any = document
    for index, part in enumerate(parts):
        if isinstance(target, list):
            if not part.isdigit() or int(part) >= len(target):
                raise ValueError(f"knob pointer {pointer}: array index does not exist")
            key: Any = int(part)
        elif isinstance(target, dict) and part in target:
            key = part
        else:
            raise ValueError(f"knob pointer {pointer}: member {part!r} does not exist")
        if index == len(parts) - 1:
            target[key] = copy.deepcopy(value)
        else:
            target = target[key]


def _accident_time(document: dict[str, Any], base: Path, value: Any) -> None:
    packages = document["behaviours"]
    package = packages[0]
    if "path" in package:
        package = yaml.load((base / package["path"]).read_bytes(), Loader=UniqueLoader)
    elif "document" in package:
        package = package["document"]
    package = copy.deepcopy(package)
    transitions = package["chains"]["traffic.incident_report"]["transitions"]
    action = next(
        action
        for transition in transitions
        if transition["id"] == "schedule"
        for action in transition["actions"]
        if action["id"] == "accident-time"
    )
    duration_ns = round(value * 1e9)
    if duration_ns >= document["run"]["until_ns"]:
        raise ValueError(
            "knob accident_time_s: accident must occur before the run horizon"
        )
    action["duration_ns"] = duration_ns
    document["behaviours"] = [package, *packages[1:]]


def _decision_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    if value == "live":
        from .demo import live_decisions

        live_decisions(document, {"profile": "live-llm"})
    else:
        document["engines"]["decisions"]["config"]["fixture_path"] = str(
            base / "fixtures/decisions.json"
        )


def _physics_hz(document: dict[str, Any], base: Path, value: Any) -> None:
    step_ns = round(1e9 / value)
    document["engines"]["road_motion"]["config"]["step_ns"] = step_ns
    document["engines"]["air_motion"]["config"]["step_ns"] = step_ns
    document["run"]["advance_ns"] = step_ns


def _wind_east(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 0, value)


def _wind_north(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 1, value)


def _operator_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    operator = next(
        stream for stream in document["ingress_streams"] if stream["id"] == "operator"
    )
    operator["initial_watermark_ns"] = {
        "scheduled": document["run"]["until_ns"], "interactive": 0,
    }[value]
    if value == "interactive":
        # Omission is the ingress schema's explicit unlimited-wait policy;
        # null is not a valid numeric timeout. Stop remains available.
        operator.pop("timeout_s", None)


def _rescue_config(document: dict[str, Any]) -> dict[str, Any]:
    return next(row["config"]["rescue"] for row in document["engines"].values()
                if row["plugin"] == "lowalt_tasks" and "rescue" in row["config"])


def _rescue_participation(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    cfg["participation"] = {**cfg.get("participation", {}), "mode": value}
    if value == "filter":
        # This is an actual exported artifact supplied by the experiment owner.
        # No demonstration weights or synthetic substitute are shipped.
        path = Path(cfg["participation"]["filter"])
        cfg["participation"]["filter"] = str((base / path).resolve())


def _rescue_observation(document: dict[str, Any], base: Path, value: Any) -> None:
    import json

    cfg = _rescue_config(document)
    cfg["observation"] = value
    provider = cfg["provider"]
    if provider.get("mode") == "stub":
        responses = provider["responses"]["propose_plan"]
        for response in responses[1:]:
            call = response["message"]["tool_calls"][0]["function"]
            call["arguments"] = json.dumps({"actions": ([{"id": "a3", "role": "relay", "after": []},
                {"id": "a4", "role": "deliver", "after": ["a3"]}] if value == "positive" else []),
                "reason": "Authored stub response to received " + value + " report"})


def _rescue_radio_port(document: dict[str, Any], base: Path, value: Any) -> None:
    _rescue_config(document)["radio"]["port"] = value


def _rescue_radio(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    if value == "off":
        del cfg["radio"]


def _rescue_deadline(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    if value <= cfg["at_s"]:
        raise ValueError("rescue deadline_s must follow the notice")
    cfg["deadline_s"] = value
    # Include the expiry tick, even when no report ever arrives.
    document["run"]["until_ns"] = round(value * 1e9) + document["run"]["advance_ns"]


def _rescue_fleet_variation(document: dict[str, Any], base: Path, value: Any) -> None:
    # Materialization happens after the experiment runner supplies its seed.
    _rescue_config(document)["fleet_variation"] = value


class TemplateRegistry:
    """Operator-owned template manifests and explicitly registered pure knob hooks."""

    def __init__(self, root: Path | None = None) -> None:
        from collections.abc import Callable

        if root is None:
            installed = importlib.resources.files("aeroagentsim") / "template_data"
            source = Path(__file__).resolve().parents[3] / "scenarios/templates"
            root = Path(str(installed)) if installed.is_dir() else source
        self.root = root.resolve()
        self.hooks: dict[str, Callable[[dict[str, Any], Path, Any], None]] = {
            "traffic.accident_time": _accident_time,
            "traffic.decision_mode": _decision_mode,
            "traffic.physics_hz": _physics_hz,
            "traffic.wind_east": _wind_east,
            "traffic.wind_north": _wind_north,
            "lowalt.operator_mode": _operator_mode,
            "rescue.participation": _rescue_participation,
            "rescue.observation": _rescue_observation,
            "rescue.radio_port": _rescue_radio_port,
            "rescue.radio": _rescue_radio,
            "rescue.deadline_s": _rescue_deadline,
            "rescue.fleet_variation": _rescue_fleet_variation,
        }
        self._templates: dict[str, tuple[dict[str, Any], Path]] = {}
        if not self.root.is_dir():
            raise FileNotFoundError(f"Template registry directory missing: {self.root}")
        for manifest in sorted(self.root.glob("*/template.yaml")):
            data = yaml.load(manifest.read_bytes(), Loader=UniqueLoader)
            self._validate(data, manifest.parent.name)
            if data["id"] in self._templates:
                raise ValueError(f"Duplicate template id: {data['id']}")
            if data["id"] == "traffic-accident":
                from aeroagentsim.packs.traffic_accident.knobs import weather_knobs

                source = self._source(data, manifest.parent)
                document = yaml.load(source.read_bytes(), Loader=UniqueLoader)
                if not isinstance(document, dict):
                    raise ValueError("template scenario must be a mapping / 模板场景须为映射")
                declared = {knob["id"] for knob in data["knobs"]}
                data["knobs"].extend(knob for knob in weather_knobs(document) if knob["id"] not in declared)
            self._templates[data["id"]] = (data, manifest.parent)

    def _source(self, metadata: dict[str, Any], directory: Path) -> Path:
        relative = Path(metadata["scenario"])
        if relative.is_absolute():
            raise ValueError("template scenario path must be relative")
        if metadata["id"] == "traffic-accident" and metadata["scenario"] == "../../demos/traffic-accident/scenario.yaml":
            return demo_source(metadata["id"]) / "scenario.yaml"
        source = (directory / relative).resolve()
        if not source.is_relative_to(self.root.parent):
            raise ValueError("template scenario escapes configured registry scope")
        return source

    @staticmethod
    def _validate(data: Any, identifier: str) -> None:
        import math
        import re

        required = {
            "format",
            "id",
            "title",
            "summary",
            "category",
            "difficulty",
            "duration_s",
            "scenario",
            "observe",
            "try",
            "knobs",
            "kpis",
        }
        optional = {
            "thumbnail", "city", "title_zh", "summary_zh", "observe_zh", "try_zh"
        }
        if (
            not isinstance(data, dict)
            or required - data.keys()
            or data.keys() - required - optional
        ):
            raise ValueError(
                f"template {identifier}: required manifest fields or unsupported keys"
            )
        if (
            data["format"] != "aeroagentsim.template/v1"
            or data["id"] != identifier
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier)
        ):
            raise ValueError(f"template {identifier}: invalid format/id")
        if "city" in data and data["city"] != "huangpu":
            raise ValueError(f"template {identifier}.city: expected huangpu")
        for key in (
            "title", "summary", "scenario", "thumbnail", "title_zh", "summary_zh"
        ):
            if key in data and (
                not isinstance(data[key], str) or not data[key].strip()
            ):
                raise ValueError(
                    f"template {identifier}.{key}: nonempty string required"
                )
        if data["category"] not in {
            "logistics",
            "emergency",
            "inspection",
            "agriculture",
            "mobility",
            "security",
            "mixed",
        } or data["difficulty"] not in {"beginner", "intermediate", "advanced"}:
            raise ValueError(f"template {identifier}: invalid category/difficulty")
        duration = data["duration_s"]
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError(
                f"template {identifier}.duration_s: positive finite seconds required"
            )
        for key in ("observe", "try", "kpis"):
            if not isinstance(data[key], list) or any(
                not isinstance(item, str) or not item for item in data[key]
            ):
                raise ValueError(
                    f"template {identifier}.{key}: list of strings required"
                )
            if key != "kpis" and not 2 <= len(data[key]) <= 4:
                raise ValueError(f"template {identifier}.{key}: provide 2–4 bullets")
        for key in ("observe", "try"):
            translated = key + "_zh"
            if translated in data and (
                not isinstance(data[translated], list)
                or len(data[translated]) != len(data[key])
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in data[translated]
                )
            ):
                raise ValueError(
                    f"template {identifier}.{translated}: nonempty strings aligned "
                    f"with {key} required"
                )
        if not isinstance(data["knobs"], list):
            raise ValueError(f"template {identifier}.knobs: list required")
        known: set[str] = set()
        for knob in data["knobs"]:
            if (
                not isinstance(knob, dict)
                or {"id", "label", "type", "default"} - knob.keys()
                or knob.keys()
                - {
                    "id",
                    "label",
                    "type",
                    "default",
                    "min",
                    "max",
                    "options",
                    "pointer",
                    "apply",
                    "description",
                    "label_zh",
                    "description_zh",
                    "options_zh",
                }
            ):
                raise ValueError(f"template {identifier}: malformed knob declaration")
            if (
                not isinstance(knob["id"], str)
                or not knob["id"]
                or knob["id"] in known
                or not isinstance(knob["label"], str)
                or not knob["label"]
            ):
                raise ValueError(
                    f"template {identifier}: invalid/duplicate knob id or label"
                )
            known.add(knob["id"])
            if knob["type"] not in {"integer", "number", "choice", "boolean"} or (
                "pointer" in knob
            ) == ("apply" in knob):
                raise ValueError(
                    f"knob {knob['id']}: select a valid type and exactly one pointer/apply"
                )
            binding = knob.get("pointer", knob.get("apply"))
            if not isinstance(binding, str) or not binding:
                raise ValueError(f"knob {knob['id']}: nonempty pointer/apply required")
            if knob["type"] == "choice" and (
                not isinstance(knob.get("options"), list) or not knob["options"]
            ):
                raise ValueError(f"knob {knob['id']}: choices require options")
            for key in ("label_zh", "description", "description_zh"):
                if key in knob and (
                    not isinstance(knob[key], str) or not knob[key].strip()
                ):
                    raise ValueError(f"knob {knob['id']}.{key}: nonempty string required")
            if "description_zh" in knob and "description" not in knob:
                raise ValueError(f"knob {knob['id']}.description_zh: description required")
            if "options_zh" in knob and (
                knob["type"] != "choice"
                or not isinstance(knob["options_zh"], list)
                or len(knob["options_zh"]) != len(knob["options"])
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in knob["options_zh"]
                )
            ):
                raise ValueError(
                    f"knob {knob['id']}.options_zh: nonempty labels aligned "
                    "with choice options required"
                )
            for bound in ("min", "max"):
                if bound in knob and (
                    type(knob[bound]) not in (int, float)
                    or not math.isfinite(knob[bound])
                ):
                    raise ValueError(f"knob {knob['id']}: finite {bound} required")
            _knob_value(knob, knob["default"])

    def register_apply_hook(self, name: str, hook: Any) -> None:
        if not isinstance(name, str) or not name or not callable(hook):
            raise ValueError("apply hook requires a name and callable")
        if name in self.hooks:
            raise ValueError(f"apply hook {name}: already registered")
        self.hooks[name] = hook

    def list(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(self._templates[name][0]) for name in sorted(self._templates)
        ]

    def get(self, identifier: str) -> dict[str, Any]:
        return copy.deepcopy(self._templates[identifier][0])

    def apply(
        self, identifier: str, knobs: dict[str, Any] | None = None
    ) -> PreparedTemplate:
        from aeroagentsim.scenario import load_scenario

        metadata, directory = self._templates[identifier]
        if knobs is not None and not isinstance(knobs, dict):
            raise ValueError("knobs: mapping required")
        overrides = {} if knobs is None else knobs
        declared = {knob["id"]: knob for knob in metadata["knobs"]}
        if extra := overrides.keys() - declared.keys():
            raise ValueError(f"Unknown knobs: {', '.join(sorted(extra))}")
        source = self._source(metadata, directory)
        document = yaml.load(source.read_bytes(), Loader=UniqueLoader)
        if not isinstance(document, dict):
            raise ValueError("template scenario must be a mapping")
        if "city" in metadata:
            document["city"] = metadata["city"]
        elif identifier in {
            "traffic-accident", "parcel-delivery", "medical-delivery",
            "powerline-inspection", "crop-spraying", "air-taxi", "security-patrol", "city-day",
        }:
            document["city"] = "huangpu"
        values = {
            name: copy.deepcopy(
                overrides[name] if name in overrides else knob["default"]
            )
            for name, knob in declared.items()
        }
        for name, knob in declared.items():
            _knob_value(knob, values[name])
            if knob["type"] == "number":
                # JSON 2 and 2.0 represent the same number; kernel Number facts
                # require a float. Keep integer knobs exact and independently typed.
                values[name] = float(values[name])
            if "pointer" in knob:
                _pointer(document, knob["pointer"], values[name])
            else:
                hook = self.hooks.get(knob["apply"])
                if hook is None:
                    raise ValueError(
                        f"knob {name}: unregistered apply hook {knob['apply']}"
                    )
                hook(document, source.parent, values[name])
        load_scenario(document, base=source.parent)
        return PreparedTemplate(
            document, source.parent, values, copy.deepcopy(metadata)
        )
