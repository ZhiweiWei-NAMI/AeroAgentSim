#!/usr/bin/env python3
"""Generate the lite-city scene for the traffic-accident demo.

Builds a small, OSM-derived geometry bundle (buildings with real footprints +
the incident road geometry) for the frontend worker, without any GLB/textures
and without any runtime download dependency for the demo itself.

Sources & mapping
-----------------
* Building ids / local centers / heights come from the original traffic-demo city scene
  (READ ONLY): each building entry supplies id, x, y, z, height. Local frame:
  E = x, N = -z, U = y (verified against the incident, which the demo renders
  at X=92, Z=-93 while the scene stores position x=91.95, z=+93.04).
* Real footprints are fetched from the public OpenStreetMap API
  (way/<id>/full and relation/<id>/full for multipolygon relations). The
  source OSM node ring is projected to metres around the ring's own centroid
  (equirectangular: east = R*cos(lat0)*dlon_rad, north = R*dlat_rad, R=6378137)
  and then translated so the projected centroid lands on the original local
  center (E = x, N = -z). Original centers and heights are preserved as
  supplied; nothing is scaled or rotated.
* Roads use the actual scene incident lane / adjacent lane / vehicle route
  geometry, mapped as E = X, N = -Z, U = Y.

Caching
-------
Fetched OSM API responses are cached as raw JSON under
<out>/osm-source/way-<id>.json and relation-<id>.json (committed excerpt,
licensed ODbL). On regeneration the cache is used first, so the script runs
fully offline once a bundle exists.

Usage
-----
python tools/demos/generate_lite_city.py --scene <path/to/scene.json> \
    --out scenarios/demos/traffic-accident/inputs/lite-city [--limit 32]

Pure stdlib. Build-time network only (OSM API, max 3 attempts per request);
failures surface as real errors, footprints are never fabricated.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

OSM_API = "https://api.openstreetmap.org/api/0.6"
EARTH_R = 6378137.0
FORMAT = "traffic-city-lite/v1"
MAX_ATTEMPTS = 3


def parse_building_id(bid: str):
    """'building.way.123.component.0' -> ('way', '123'); None otherwise."""
    m = re.match(r"building\.(way|relation)\.(\d+)\.component", bid)
    if not m:
        return None
    return m.group(1), m.group(2)


def http_json(url: str) -> dict:
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "aeroagentsim-lite-city-generator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_err = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 * attempt)
    raise RuntimeError(
        f"OSM fetch failed after {MAX_ATTEMPTS} attempts for {url}: "
        f"{type(last_err).__name__}: {last_err}"
    )


def load_osm(kind: str, osm_id: str, cache_dir: str) -> dict:
    """Load a way/relation + its member nodes, from cache or the OSM API."""
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{kind}-{osm_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    url = f"{OSM_API}/{kind}/{osm_id}/full.json"
    data = http_json(url)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return data


def ring_from_osm(kind: str, osm_id: str, data: dict):
    """Return the outer ring(s) [[lat, lon], ...] of an OSM way/relation."""
    nodes = {
        e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"
    }
    ways = {e["id"]: e.get("nodes", []) for e in data["elements"] if e["type"] == "way"}
    rings = []
    osm_id = int(osm_id)
    if kind == "way":
        if osm_id not in ways:
            raise ValueError(f"way {osm_id} not found in API response")
        rings.append(ways[osm_id])
    else:  # relation: collect outer multipolygon member ways
        outer = []
        for e in data["elements"]:
            if e["type"] != "relation":
                continue
            for mem in e.get("members", []):
                if mem.get("type") == "way" and mem.get("role", "outer") == "outer":
                    outer.append(mem["ref"])
        if not outer:
            raise ValueError(f"relation {osm_id} has no outer ways")
        rings.extend(outer)
    out = []
    for ref in rings:
        try:
            ring = [nodes[n] for n in ref if n in nodes]
        except KeyError as exc:
            raise ValueError(
                f"missing node {exc} in response for {kind} {osm_id}"
            )
        if len(ring) >= 3:
            out.append(ring)
    if not out:
        raise ValueError(f"no closed outer ring (>=3 nodes) for {kind} {osm_id}")
    return out


def project_ring_m(ring_latlon):
    """Equirectangular projection of a [[lat, lon], ...] ring to metres
    about the ring's own centroid centroid. Returns [[east, north], ...]."""
    lat0 = sum(p[0] for p in ring_latlon) / len(ring_latlon)
    lon0 = sum(p[1] for p in ring_latlon) / len(ring_latlon)
    cos0 = math.cos(math.radians(lat0))
    east, north = [], []
    for lat, lon in ring_latlon:
        east.append(EARTH_R * math.radians(lon - lon0) * cos0)
        north.append(EARTH_R * math.radians(lat - lat0))
    return [[e, n] for e, n in zip(east, north)]


def select_buildings(scene: dict, limit: int):
    """Buildings (way/relation only) closest to the incident in the local
    render frame E = x, N = -z."""
    # Scene frame: incident position x=91.95, z=+93.04 renders at X=92, Z=-93
    # (render Z = -scene z). Distance is measured in the scene frame.
    inc = scene["incident"]["position"]
    inc_x, inc_z = inc["x"], inc["z"]
    cands = []
    for b in scene["buildings"]:
        parsed = parse_building_id(b["id"])
        if parsed is None:
            continue
        kind, oid = parsed
        d2 = (b["x"] - inc_x) ** 2 + (b["z"] - inc_z) ** 2
        cands.append((d2, kind, oid, b))
    cands.sort(key=lambda c: c[0])
    return cands[:limit], (inc_x, -inc_z)


def dedupe(points, tol=1e-6):
    out = []
    for p in points:
        if out and all(abs(p[i] - out[-1][i]) <= tol for i in range(len(p))):
            continue
        out.append(p)
    return out


def route_segments_near(
    scene: dict, center_en: tuple, radius: float = 80.0, max_routes: int = 4
):
    """Deduplicated sub-paths of scene vehicle routes that pass near the
    incident. Points are mapped E = X, N = -Z, U = Y."""
    out = []
    for rt in scene["routes"]:
        pts = rt["points"]
        inside = [
            p
            for p in pts
            if math.hypot(p[0] - center_en[0], p[1] - center_en[1]) <= radius
        ]
        if len(inside) < 2:
            continue
        out.append((rt["id"], inside))
    out.sort(
        key=lambda t: min(
            math.hypot(p[0] - center_en[0], p[1] - center_en[1]) for p in t[1]
        )
    )
    # different vehicles can ride the same SUMO edge; keep unique geometry
    seen, uniq = set(), []
    for rt_id, seg in out:
        key = tuple(round(c, 3) for p in seg for c in (p[0], p[1]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((rt_id, seg))
    return uniq[:max_routes]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--scene", required=True, help="path to source scene.json (READ ONLY)"
    )
    ap.add_argument(
        "--out", required=True, help="output directory for the lite-city bundle"
    )
    ap.add_argument(
        "--limit", type=int, default=32, help="buildings to include (20-40)"
    )
    args = ap.parse_args(argv)
    if not 20 <= args.limit <= 40:
        ap.error("--limit must be within 20..40")

    with open(args.scene, "r", encoding="utf-8") as fh:
        scene = json.load(fh)

    out = os.path.abspath(args.out)
    cache_dir = os.path.join(out, "osm-source")
    inc_e, inc_n = (
        scene["incident"]["position"]["x"],
        -scene["incident"]["position"]["z"],
    )

    # ---- buildings -----------------------------------------------------
    cands, _ = select_buildings(scene, args.limit)
    buildings = []
    for _, kind, oid, b in cands:
        data = load_osm(kind, oid, cache_dir)
        rings = ring_from_osm(kind, oid, data)
        # largest outer ring wins (relations may carry courtyards)
        ring = max(rings, key=len)
        # source ring projected to metres about its own centroid, then
        # translated so the centroid lands on the supplied local center.
        local = project_ring_m(ring)
        cxE = sum(p[0] for p in local) / len(local)
        cyN = sum(p[1] for p in local) / len(local)
        footprint = [
            [round(p[0] - cxE + b["x"], 3), round(p[1] - cyN + (-b["z"]), 3)]
            for p in local
        ]
        buildings.append(
            {
                "id": b["id"],
                "osm": {"type": kind, "id": int(oid)},
                "footprint": footprint,
                "height_m": b["height"],
            }
        )
    if not buildings:
        raise SystemExit(
            "no buildings could be generated; aborting (no fabricated data)"
        )

    # ---- roads (actual scene incident/route geometry) ------------------
    roads = []
    lane_pts = scene["incident"]["points"]
    roads.append(
        {
            "id": "incident-lane",
            "source": "scene.incident.points (SUMO lane --2682428834550472112#0_1)",
            "points_enu_m": [
                [round(p[0], 3), round(-p[1], 3), 0.0] for p in dedupe(lane_pts)
            ],
            "width_m": 6.4,  # assumed: two 3.2 m lanes (scene adjacent lane width is 3.2 m)
        }
    )
    adj = scene["incident"].get("adjacent_lane")
    if adj and adj.get("points"):
        roads.append(
            {
                "id": "incident-adjacent-lane",
                "source": "scene.incident.adjacent_lane.points ({})".format(adj.get("id", "")),
                "points_enu_m": [
                    [round(p[0], 3), round(-p[1], 3), 0.0]
                    for p in dedupe(adj["points"])
                ],
                "width_m": adj.get("width_m", 3.2),
            }
        )
    for rt_id, seg in route_segments_near(scene, (inc_e, inc_n)):
        roads.append(
            {
                "id": f"route-{rt_id}",
                "source": f"scene route {rt_id} near incident (deduplicated segment)",
                "points_enu_m": [
                    [
                        round(p[0], 3),
                        round(-p[1], 3),
                        round(p[2] if len(p) > 2 else 0.0, 3),
                    ]
                    for p in dedupe(seg)
                ],
                "width_m": 6.4,  # assumed display width; original road width not in scene
            }
        )

    bundle = {
        "format": FORMAT,
        "frame": "enu",
        "frame_mapping": "E = scene x, N = -scene z, U = scene y (incident renders at X=92, Z=-93)",
        "incident": {
            "east_m": round(inc_e, 3),
            "north_m": round(inc_n, 3),
            "up_m": scene["incident"]["position"].get("y", 0.0),
        },
        "building_count": len(buildings),
        "road_count": len(roads),
        "buildings": buildings,
        "roads": roads,
        "sources": {
            "buildings_centers_heights": "source scene.json building entries (id/x/z/height, READ ONLY), unchanged",
            "building_footprints": "OpenStreetMap API way/<id>/full and relation/<id>/full; ring projected metres around source centroid, translated to original local center (E=x, N=-z); no scaling/rotation",
            "roads": "actual scene incident lane / adjacent lane / vehicle route geometry mapped E=X, N=-Z, U=Y; display width assumed 6.4 m (2 x 3.2 m lanes) where original width absent",
            "osm_cache": "osm-source/*.json committed API responses; regen runs offline from cache",
        },
    }

    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "scene.json"), "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=1)

    attribution = (
        f"""{FORMAT}
Generated from OpenStreetMap data.

© OpenStreetMap contributors, ODbL 1.0
https://www.openstreetmap.org/copyright
https://opendatacommons.org/licenses/odbl/1-0/

Building footprints were fetched from the public OSM API
(way/<id>/full and relation/<id>/full). Raw API responses are committed
verbatim under osm-source/ so regeneration needs no network.

Regeneration command:
  python tools/demos/generate_lite_city.py \\
      --scene <historical-export-scene.json> \\
      --out scenarios/demos/traffic-accident/inputs/lite-city
"""
    )
    with open(os.path.join(out, "ATTRIBUTION.txt"), "w", encoding="utf-8") as fh:
        fh.write(attribution)

    print(f"buildings: {len(buildings)}, roads: {len(roads)} -> {out}")


if __name__ == "__main__":
    main()
