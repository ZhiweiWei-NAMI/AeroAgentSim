"""Named facilities and street samples from the shipped Huangpu geometry."""

from __future__ import annotations

import copy
import importlib.resources
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from aeroagentsim.authoring.templates import demo_source

ROOF_ROLES = ["vertiport", "charging_pad", "kitchen", "customer", "handoff_point", "inspection_start", "inspection_end"]
STREET_ROLES = ["incident_site", "handoff_point", "inspection_start", "inspection_end"]
LANDMARKS = {
    "plaza": ("West Plaza", "西广场"), "hospital": ("Hospital rooftop", "医院屋顶"),
    "clinic": ("Clinic", "诊所"), "east": ("East Roof", "东侧屋顶"),
    "north": ("North Roof", "北侧屋顶"), "bund": ("Bund Roof", "外滩屋顶"),
}


def _distance(a: list[float], b: list[float]) -> float:
    return math.dist(a[:2], b[:2])


@lru_cache(maxsize=1)
def _catalogue() -> list[dict[str, Any]]:
    installed = importlib.resources.files("aeroagentsim") / "template_data" / "huangpu-placements.json"
    source = Path(__file__).resolve().parents[3] / "scenarios/templates/huangpu-placements.json"
    path = Path(str(installed)) if installed.is_file() else source
    data = json.loads(path.read_text(encoding="utf-8"))
    result = [{"key": key, "label": labels[0], "label_zh": labels[1], "position": data[key],
               "level": "ground" if data[key][2] == 0 else "rooftop", "suits": list(ROOF_ROLES), "near": None}
              for key, labels in LANDMARKS.items()]
    for index, point in enumerate(data["customers"], 1):
        result.append({"key": f"customer-{index}", "label": f"Customer rooftop {index}",
                       "label_zh": f"客户屋顶 {index}", "position": point, "level": "rooftop",
                       "suits": list(ROOF_ROLES), "near": None})
    city = json.loads((demo_source("traffic-accident") / "inputs/lite-city/scene.json").read_text(encoding="utf-8"))
    samples: list[list[float]] = []
    seen: set[tuple[float, float]] = set()
    for road in city["roads"]:
        points = road["points_enu_m"]
        for a, b in zip(points, points[1:]):
            parts = max(1, math.ceil(_distance(a, b) / 50))
            for i in range(parts + 1):
                p = [round(a[j] + (b[j] - a[j]) * i / parts, 3) for j in range(2)] + [0.0]
                key = (p[0], p[1])
                if key not in seen:
                    seen.add(key)
                    samples.append(p)
    if not samples:
        raise ValueError("Huangpu street geometry has no samples / 黄浦街道几何没有采样点")
    landmarks = result[:len(LANDMARKS)]
    for landmark in landmarks:
        p = min(samples, key=lambda point: _distance(point, landmark["position"]))
        result.append({"key": "street-" + landmark["key"], "label": "Street by the " + landmark["label"],
                       "label_zh": landmark["label_zh"] + "旁街道", "position": p, "level": "street",
                       "suits": list(STREET_ROLES), "near": landmark["key"]})
    for index, p in enumerate(samples, 1):
        landmark = min(landmarks, key=lambda row: _distance(p, row["position"]))
        result.append({"key": f"street-{index}", "label": f"Street by the {landmark['label']} · {index}",
                       "label_zh": f"{landmark['label_zh']}旁街道 · {index}", "position": p,
                       "level": "street", "suits": list(STREET_ROLES), "near": landmark["key"]})
    return result


def place_catalogue() -> list[dict[str, Any]]:
    return copy.deepcopy(_catalogue())


def find_places(query: str | None = None, near: str | list[float] | dict[str, Any] | None = None,
                role: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer / limit 必须为正整数")
    rows = place_catalogue()
    position: list[float] | None = None
    if isinstance(near, str):
        found = next((r for r in rows if r["key"] == near), None)
        if found is None:
            raise ValueError("Resolve entity ids to a position before finding places / 查找前请将实体标识解析为坐标")
        position = found["position"]
    elif near is not None:
        position = near["position"] if isinstance(near, dict) else near
        if not isinstance(position, (list, tuple)) or len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
            raise ValueError("near requires an ENU position / near 需要 ENU 坐标")
    q = (query or "").casefold()
    rows = [r for r in rows if (role is None or role in r["suits"]) and any(q in str(r[k]).casefold() for k in ("key", "label", "label_zh"))]
    if position is not None:
        for row in rows:
            row["distance_m"] = round(_distance(row["position"], position), 3)
        rows.sort(key=lambda r: (r["distance_m"], r["key"]))
    return rows[:limit]
