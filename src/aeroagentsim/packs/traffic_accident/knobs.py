"""Expose only weather inputs consumed by the traffic template's motion model."""

from __future__ import annotations

import copy
import math
from typing import Any


def wind_source(document: dict[str, Any]) -> tuple[str, str, dict[str, Any], int | None] | None:
    engines = document.get("engines", {})
    motion = engines.get("air_motion", {})
    model = motion.get("config", {}).get("consumption_model", {})
    if motion.get("plugin") != "kinematic" or model.get("plugin") != "wind":
        return None
    config = model["config"]
    entity, field = config["entity"], config["field"]
    for engine in engines.values():
        if engine.get("plugin") == "environment":
            profile = engine.get("config", {}).get("profiles", {}).get(entity, {}).get(field)
            if isinstance(profile, dict):
                return entity, field, profile, config.get("vector_index")
    return None


def weather_knobs(document: dict[str, Any]) -> list[dict[str, Any]]:
    source = wind_source(document)
    if source is None:
        return []
    _, _, profile, row = source
    if profile.get("mode") not in {"calm", "constant"}:
        return []  # Gust profiles need their interval editor, not a constant knob.
    value = profile["value"] if row is None else profile["value"][row]
    if not isinstance(value, list) or len(value) not in {2, 3} or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError("Traffic wind: finite ENU vector required / 交通场景风速须为有限 ENU 向量")
    return [
        {
            "id": f"wind_{axis}_mps", "label": f"{label} wind (m/s)", "label_zh": f"{label_zh}风速（米/秒）",
            "type": "number", "default": float(value[index]), "apply": f"traffic.wind_{axis}",
            "description": "Constant ENU wind component, read by the UAV energy model and response feasibility assessment; ground-track motion is unchanged.",
            "description_zh": "恒定 ENU 风速分量，由无人机能耗模型和响应可达性评估读取；不改变地面航迹运动。",
        }
        for index, axis, label, label_zh in ((0, "east", "Eastward", "向东"), (1, "north", "Northward", "向北"))
    ]


def set_wind(document: dict[str, Any], index: int, value: float) -> None:
    source = wind_source(document)
    if source is None:
        raise ValueError("Traffic wind has no configured runtime consumer / 交通场景未配置风速执行组件")
    entity_id, field, profile, row = source
    if profile.get("mode") not in {"calm", "constant"}:
        raise ValueError("Edit gust intervals before selecting constant wind / 请先编辑阵风区间，再选择恒定风速")
    authored = copy.deepcopy(profile["value"])
    vector = authored if row is None else authored[row]
    # Applying the declared default must preserve the authored weather profile.
    if vector[index] == value:
        return
    vector[index] = float(value)
    profile.update(mode="constant", value=authored)
    entity = next(entity for entity in document["entities"] if entity["id"] == entity_id)
    # Environment requires agreement at t=0 when an initial fact is authored.
    if field in entity["facts"]:
        entity["facts"][field] = copy.deepcopy(authored)
