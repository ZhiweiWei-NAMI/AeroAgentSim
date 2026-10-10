"""Derive public Huangpu placements and roof-safe mission paths from OSM lite geometry.

Run with the project Python and PYTHONPATH=aerokernel:src. No AeroGraph needed.
The field is an explicitly authored demonstration polygon beyond the dense core;
it does not claim that the OSM source contains agricultural land.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCENE = ROOT / "scenarios/demos/traffic-accident/inputs/lite-city/scene.json"
TEMPLATES = (
    "parcel-delivery",
    "medical-delivery",
    "powerline-inspection",
    "crop-spraying",
    "air-taxi",
    "security-patrol",
    "city-day",
)
BUSINESS = {
    "parcel": "logistics",
    "medical": "emergency",
    "inspection": "inspection",
    "survey": "inspection",
    "spraying": "agriculture",
    "passenger": "mobility",
    "patrol": "security",
    "incident_response": "emergency",
}


def inside(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point[:2]
    result = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > y) != (b[1] > y) and x < (
            (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
        ):
            result = not result
    return result


def bounds(points: list[list[float]]) -> tuple[list[float], list[float]]:
    return (
        [min(p[i] for p in points) for i in range(2)],
        [max(p[i] for p in points) for i in range(2)],
    )


def roof(building: dict[str, Any]) -> list[float]:
    """Find a reproducible interior pad with maximum sampled distance to the outline."""
    ring = building["footprint"]
    lower, upper = bounds(ring)

    def edge_distance(p: list[float], a: list[float], b: list[float]) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        t = (
            0.0
            if length == 0
            else min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
        )
        return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)

    candidates = [
        [
            lower[0] + (upper[0] - lower[0]) * x / 20,
            lower[1] + (upper[1] - lower[1]) * y / 20,
        ]
        for x in range(1, 20)
        for y in range(1, 20)
    ]
    valid = [p for p in candidates if inside(p, ring)]
    if not valid:
        raise ValueError(f"No interior pad candidate for {building['id']}")
    point = max(
        valid,
        key=lambda p: min(
            edge_distance(p, a, b) for a, b in zip(ring, ring[1:] + ring[:1])
        ),
    )
    return [round(point[0], 3), round(point[1], 3), float(building["height_m"])]


def field_asset(width: float, depth: float) -> str:
    """An inline real glTF green ground polygon, rendered by the existing model loader."""
    vertices = (
        -width / 2,
        0.02,
        -depth / 2,
        width / 2,
        0.02,
        -depth / 2,
        width / 2,
        0.02,
        depth / 2,
        -width / 2,
        0.02,
        depth / 2,
    )
    binary = struct.pack("<12f6H", *vertices, 0, 2, 1, 0, 3, 2)
    asset = {
        "asset": {"version": "2.0", "generator": "Huangpu demonstration field"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "buffers": [
            {
                "byteLength": len(binary),
                "uri": "data:application/octet-stream;base64,"
                + base64.b64encode(binary).decode(),
            }
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 48, "target": 34962},
            {"buffer": 0, "byteOffset": 48, "byteLength": 12, "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 4,
                "type": "VEC3",
                "min": [-width / 2, 0.02, -depth / 2],
                "max": [width / 2, 0.02, depth / 2],
            },
            {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"},
        ],
        "materials": [
            {
                "name": "Demo field, not OSM cropland",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.18, 0.48, 0.18, 1.0],
                    "metallicFactor": 0,
                    "roughnessFactor": 1,
                },
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}
                ]
            }
        ],
    }
    return (
        "data:model/gltf+json;base64,"
        + base64.b64encode(json.dumps(asset).encode()).decode()
    )


def simplify(points: list[list[float]], tolerance: float = 4.0) -> list[list[float]]:
    """Douglas-Peucker road shape reduction bounded to four metres in ENU XY."""
    if len(points) <= 2:
        return points
    a, b = points[0], points[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    distances = []
    for p in points[1:-1]:
        t = (
            0.0
            if length == 0
            else min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
        )
        distances.append(math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    index = max(range(len(distances)), key=distances.__getitem__)
    if distances[index] <= tolerance:
        return [a, b]
    split = index + 1
    return simplify(points[: split + 1], tolerance)[:-1] + simplify(
        points[split:], tolerance
    )


def placements(scene: dict[str, Any]) -> dict[str, Any]:
    if scene["format"] != "traffic-city-lite/v1" or scene["frame"] != "enu":
        raise ValueError("Expected the committed traffic-city-lite/v1 ENU scene")
    buildings = scene["buildings"]
    by_id = {b["id"]: b for b in buildings}

    def pick(osm: int) -> dict[str, Any]:
        return by_id[f"building.way.{osm}.component.0"]

    incident = scene["incident"]
    plaza = [round(incident["east_m"] - 105, 3), round(incident["north_m"] - 7, 3), 0.0]
    if any(inside(plaza, b["footprint"]) for b in buildings):
        raise ValueError("Derived west plaza intersects a building")
    cruise = max(float(b["height_m"]) for b in buildings) + 20.0
    road = next(r for r in scene["roads"] if r["id"] == "incident-lane")
    road_points = simplify([[p[0], p[1], cruise] for p in road["points_enu_m"]])
    north_road = next(r for r in scene["roads"] if r["id"] == "route-vehicle.012")
    # Vehicle traces repeat the same street several times. Use its first
    # northbound traversal, rather than mistaking repeated traffic for an asset.
    source = north_road["points_enu_m"]
    end = max(range(len(source)), key=lambda i: source[i][1])
    north_points = simplify([[p[0], p[1], cruise] for p in source[: end + 1]])
    # The source has no agricultural land. This explicit polygon starts beyond
    # the northernmost building, alongside the north road, outside the core.
    north = max(p[1] for b in buildings for p in b["footprint"])
    west = min(p[0] for b in buildings for p in b["footprint"])
    lower = [round(west + 5, 3), round(north + 10, 3)]
    upper = [round(lower[0] + 160, 3), round(lower[1] + 120, 3)]
    landmark = pick(369237836)
    lo, hi = bounds(landmark["footprint"])
    patrol = [
        [lo[0] - 15, lo[1] - 15, cruise],
        [lo[0] - 15, hi[1] + 15, cruise],
        [hi[0] + 15, hi[1] + 15, cruise],
        [hi[0] + 15, lo[1] - 15, cruise],
    ]
    blocked = pick(447124025)
    customers = [
        roof(b)
        for b in sorted(buildings, key=lambda b: b["id"])
        if b["id"] != blocked["id"]
    ]
    return {
        "cruise": cruise,
        "plaza": plaza,
        "hospital": roof(pick(447124028)),
        "clinic": roof(pick(369237839)),
        "east": roof(pick(389701956)),
        "north": roof(pick(369237850)),
        "customer": roof(pick(389701871)),
        "bund": roof(pick(505655138)),
        "customers": customers,
        "road": road_points,
        "north_road": north_points,
        "patrol": patrol,
        "blocked": blocked,
        "field_lower": lower,
        "field_upper": upper,
    }


def place_document(document: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(document)
    for descriptor in doc["registry"]["fields"]:
        if (
            descriptor["id"].startswith("lowalt.")
            and descriptor["metadata"].get("frame") == "enu"
        ):
            descriptor["metadata"]["transform_revision"] = "huangpu-lite-city/v1"
    identity = doc["id"]
    cruise = layout["cruise"]
    field_lo, field_hi = layout["field_lower"], layout["field_upper"]
    field_entry = [*field_lo, 5.0]
    field_exit = [*field_hi, 5.0]
    field_pad = [field_lo[0] - 8, field_lo[1], 0.0]
    road = layout["road"]
    north = layout["north_road"]
    locations = {
        "home": layout["plaza"],
        "home-pad": layout["plaza"],
        "vertiport-1": layout["plaza"],
        "pad-1": layout["plaza"],
        "vertiport-2": layout["east"],
        "pad-2": layout["east"],
        "hospital": layout["hospital"],
        "clinic": layout["clinic"],
        "customer": layout["customer"],
        "customer-east": layout["customer"],
        "depot-east": layout["east"],
        "east-port": layout["east"],
        "east-pad": layout["east"],
        "north-port": layout["north"],
        "north-pad": layout["north"],
        "line-start": north[-1],
        "line-end": north[-1],
        "field-entry": field_entry,
        "field-start": field_entry,
        "field-exit": field_exit,
        "field-end": field_exit,
        "guard-return": layout["plaza"],
        "incident-site": road[len(road) // 2],
    }
    if identity == "air-taxi":
        locations["home"] = locations["home-pad"] = layout["bund"]
    if identity == "crop-spraying":
        locations["home"] = locations["home-pad"] = field_pad
    for entity in doc["entities"]:
        facts = entity["facts"]
        eid = entity["id"]
        if "lowalt.site_position" in facts:
            if eid.startswith("customer-") and eid != "customer-east":
                facts["lowalt.site_position"] = layout["customers"][
                    int(eid.split("-")[-1]) - 1
                ]
            elif eid != "demo-field":
                facts["lowalt.site_position"] = locations[eid]
        if "lowalt.zone_kind" in facts:
            if facts["lowalt.zone_kind"] == "corridor":
                points = road + north if eid != "north-corridor" else north
                lo, hi = bounds(points)
                facts["lowalt.zone_lower"] = [lo[0] - 12, lo[1] - 12, cruise - 5]
                facts["lowalt.zone_upper"] = [hi[0] + 12, hi[1] + 12, cruise + 50]
            elif eid == "central-no-fly":
                lo, hi = bounds(layout["blocked"]["footprint"])
                facts["lowalt.zone_lower"] = [*lo, 0.0]
                facts["lowalt.zone_upper"] = [*hi, cruise + 30]
            elif eid == "incident-no-fly":
                # Runtime re-centres this box on the committed road incident.
                facts["lowalt.zone_lower"][2] = 0.0
                facts["lowalt.zone_upper"][2] = cruise + 55
    cfg = doc["engines"]["tasks"]["config"]
    classes = {doc["generators"]["fleet"]["facts"]["lowalt.vehicle_class"]}
    classes.update(
        e["facts"]["lowalt.vehicle_class"]
        for e in doc["entities"]
        if "lowalt.vehicle_class" in e["facts"]
    )
    cfg["cruise_altitudes"] = {
        c: (5.0 if identity == "crop-spraying" else cruise) for c in sorted(classes)
    }
    for kind, spec in cfg.get("work_routes", {}).items():
        config = spec["config"]
        if kind == "spraying":
            config.update(lower=field_lo, upper=field_hi)
        elif kind == "inspection":
            # A plausible authored power-line survey follows source road edges.
            config["waypoints"] = (
                north + list(reversed(north)) + north + list(reversed(north))
            )
            config["cruise_altitude_m"] = cruise
        elif kind == "passenger":
            config["waypoints"] = list(reversed(road)) + north
            config["cruise_altitude_m"] = cruise
        elif kind == "patrol":
            config["waypoints"] = layout["patrol"]
            config["cruise_altitude_m"] = cruise
    doc["engines"]["airspace"]["config"].setdefault("algorithm_config", {}).setdefault(
        "conflict_resolution", {}
    )["minimum_altitude_m"] = (
        cruise
        if doc["algorithms"]["conflict_resolution"] == "altitude_layers"
        else None
    )
    if doc["algorithms"]["conflict_resolution"] != "altitude_layers":
        doc["engines"]["airspace"]["config"]["algorithm_config"][
            "conflict_resolution"
        ].pop("minimum_altitude_m")
    if identity == "city-day":
        doc["engines"]["airspace"]["config"]["dynamic_zone_centers"]["incident-no-fly"][
            "dimensions_m"
        ][2] = cruise + 55
        for e in doc["entities"]:
            if e["id"] == "taxi-vehicle":
                e["facts"]["lowalt.position"] = layout["hospital"]
            if e["id"] == "line-vehicle":
                e["facts"]["lowalt.position"] = [
                    layout["plaza"][0] + 5,
                    layout["plaza"][1],
                    0.0,
                ]
            if e["id"] == "field-vehicle":
                e["facts"]["lowalt.position"] = [
                    layout["plaza"][0],
                    layout["plaza"][1] + 5,
                    5.0,
                ]
    fleet = doc["generators"]["fleet"]
    fleet["starts"] = [locations.get("home", layout["plaza"])]
    if identity in {"parcel-delivery", "medical-delivery"}:
        # Drones start at both vertiports so allocators see different distances.
        fleet["starts"] = [layout["plaza"], layout["east"]]
    fleet["spacing_m"] = 1.0
    if identity == "parcel-delivery":
        # Two minutes to clear the final burst: demand remains seeded and
        # rate-dependent; unused authored task slots are never created orders.
        for group in cfg["generators"]:
            group["arrivals"]["end_ns"] = 180_000_000_000
        # Delivery sites are multi-bay aprons; parcel pickup never uses the
        # passenger landing reservation queue (capacity was not its bottleneck).
        for entity in doc["entities"]:
            if entity["type"] in {"lowalt:Vertiport", "lowalt:ChargingPad"}:
                entity["facts"]["lowalt.site_capacity"] = 4
    if identity == "medical-delivery":
        # Keep preemption early; routine demand has the same measured cutoff.
        for group in cfg["generators"]:
            if group["arrivals"]["mode"] == "poisson":
                group["arrivals"].pop("times_ns", None)
                group["arrivals"]["end_ns"] = 180_000_000_000
            else:
                group["arrivals"]["times_ns"] = [
                    8_000_000_000,
                    90_000_000_000,
                    150_000_000_000,
                ]
    if identity == "city-day":
        # A second rooftop pickup keeps a parcel departure independent of the
        # congested plaza. Medical preemption chooses the closest parcel drone.
        fleet["starts"] = [layout["plaza"], layout["hospital"], layout["plaza"]]
        generators = cfg["generators"]
        generators[:] = [g for g in generators if g["tasks"] != ["parcel-2"]]
        parcels = next(g for g in generators if g["kind"] == "parcel")
        if "parcel-2" in parcels["tasks"]:
            index = parcels["tasks"].index("parcel-2")
            parcels["tasks"].pop(index)
            parcels["arrivals"]["times_ns"].pop(index)
        rooftop = copy.deepcopy(parcels)
        rooftop.update(tasks=["parcel-2"], origins=["hospital"])
        rooftop["arrivals"]["times_ns"] = [0]
        generators.append(rooftop)
    c = fleet["facts"]["lowalt.vehicle_class"]
    fleet.update(
        label={
            "parcel": "Drone P{index}",
            "inspection": "Survey drone {index}",
            "spraying": "Sprayer {index}",
            "passenger": "Air taxi {index}",
            "patrol": "Patrol drone {index}",
        }[c],
        business=BUSINESS[c],
        vehicle_class=c,
    )
    if identity in ("crop-spraying", "city-day"):
        if not any(t["id"] == "lowalt:Field" for t in doc["registry"]["types"]):
            doc["registry"]["types"].append(
                {"id": "lowalt:Field", "parents": ["lowalt:Site"], "abstract": False}
            )
        doc["entities"] = [e for e in doc["entities"] if e["id"] != "demo-field"]
        doc["entities"].append(
            {
                "id": "demo-field",
                "type": "lowalt:Field",
                "facts": {
                    "lowalt.site_position": [
                        (field_lo[0] + field_hi[0]) / 2,
                        (field_lo[1] + field_hi[1]) / 2,
                        0.0,
                    ],
                    "lowalt.site_open": True,
                    "lowalt.site_capacity": 1,
                },
            }
        )
        doc["presentation"] = [
            p for p in doc["presentation"] if p["typeId"] != "lowalt:Field"
        ]
        doc["presentation"].append(
            {
                "typeId": "lowalt:Field",
                "positionField": "lowalt.site_position",
                "frame": "enu",
                "visual": {
                    "kind": "model",
                    "asset": field_asset(160, 120),
                    "scale": 1.0,
                    "color": "#2e7b2e",
                },
            }
        )
    labels = {
        "home": "Vertiport West Plaza",
        "home-pad": "West Plaza charging pad",
        "hospital": "Hospital rooftop pad",
        "clinic": "Clinic rooftop",
        "depot-east": "Vertiport East Roof",
        "east-port": "Vertiport East Roof",
        "north-port": "Vertiport North Roof",
        "vertiport-1": "Vertiport West Plaza",
        "vertiport-2": "Vertiport East Roof",
        "pad-1": "West Plaza charging pad",
        "pad-2": "East rooftop charging pad",
        "line-start": "North road power-line start",
        "line-end": "North road survey return point",
        "demo-field": "Huangpu edge demonstration field",
        "weather": "Shared city weather",
        "taxi-vehicle": "Air taxi 1",
        "line-vehicle": "Power-line survey drone",
        "field-vehicle": "Field sprayer",
        "incident-no-fly": "Temporary incident no-fly zone",
        "central-no-fly": "Landmark rooftop exclusion",
        "main-corridor": "Huangpu road flight corridor",
    }
    for entity in doc["entities"]:
        eid = entity["id"]
        facts = entity["facts"]
        kind = facts.get("lowalt.task_kind")
        if eid in labels:
            label = labels[eid]
        elif eid.startswith("customer-"):
            label = "Customer rooftop " + eid.split("-")[-1]
        elif eid.startswith("uav."):
            label = "Incident drone " + eid[4:].replace(".", " ").title()
        elif eid.startswith("vehicle."):
            label = "Road vehicle " + eid.split(".")[-1]
        elif kind is not None:
            label = kind.replace("_", " ").title() + " mission " + eid.split("-")[-1]
        else:
            label = (
                eid.replace(".", " ").replace("-", " ").replace("_", " ").capitalize()
            )
        entity["label"] = label
        if "lowalt.vehicle_class" in facts:
            entity["vehicle_class"] = facts["lowalt.vehicle_class"]
            entity["business"] = BUSINESS[entity["vehicle_class"]]
        elif kind is not None:
            entity["business"] = BUSINESS[kind]
        elif eid == "demo-field" or eid.startswith("field-"):
            entity["business"] = "agriculture"
        elif eid.startswith("uav."):
            entity.update(vehicle_class="incident_response", business="emergency")
        elif eid.startswith("vehicle."):
            entity.update(vehicle_class="road_vehicle", business="traffic")
        elif entity["type"] == "lowalt:AirspaceZone":
            entity["business"] = "airspace"
        elif entity["type"] == "lowalt:Environment":
            entity["business"] = "weather"
        elif entity["type"].startswith("lowalt:"):
            entity["business"] = {
                "medical-delivery": "emergency",
                "air-taxi": "mobility",
                "security-patrol": "security",
                "powerline-inspection": "inspection",
                "crop-spraying": "agriculture",
            }.get(identity, "logistics")
        else:
            entity["business"] = "emergency"
    if identity == "crop-spraying":
        next(e for e in doc["entities"] if e["id"] == "home")["label"] = (
            "Field-edge battery station"
        )
        next(e for e in doc["entities"] if e["id"] == "home-pad")["label"] = (
            "Field-edge battery swap pad"
        )
    if identity == "air-taxi":
        next(e for e in doc["entities"] if e["id"] == "home")["label"] = (
            "Vertiport Bund Roof"
        )
        next(e for e in doc["entities"] if e["id"] == "home-pad")["label"] = (
            "Bund rooftop charging pad"
        )
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=SCENE)
    parser.add_argument("--templates", type=Path, default=ROOT / "scenarios/templates")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check reproducibility without changing files",
    )
    args = parser.parse_args()
    layout = placements(json.loads(args.scene.read_text()))
    for identity in TEMPLATES:
        path = args.templates / identity / "scenario.yaml"
        before = path.read_text()
        document = place_document(yaml.safe_load(before), layout)
        after = yaml.safe_dump(document, sort_keys=False)
        if args.check:
            if before != after:
                raise SystemExit(f"{identity}: placements differ; regenerate")
        else:
            path.write_text(after)
    metadata = {
        **layout,
        "source_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
        "field_role": "Explicit demonstration field outside dense OSM core; not source cropland",
    }
    output = args.templates / "huangpu-placements.json"
    if not args.check:
        output.write_text(json.dumps(metadata, indent=2) + "\n")
    print("Huangpu placements: seven templates; cruise ceiling", layout["cruise"], "m")


if __name__ == "__main__":
    main()
