#!/usr/bin/env python3
"""Fetch explicit OSM areas, or compile supplied OSM into independent viewer inputs.

Use an installed aeroagentsim[server] or PYTHONPATH=src. Heights derived from
building:levels use the caller's explicit level-height assumption. Unmeasured
heights remain absent with diagnostics. This is the Studio ENU geometry pipeline,
not the historical textured OSM2World mesh-pack producer.
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from aeroagentsim.authoring.scene import compile_scene, extract_bounds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--osm", type=Path, help="Explicit local ODbL OSM XML")
    inputs.add_argument(
        "--fetch-bounds", nargs=4, type=float, metavar=("W", "S", "E", "N")
    )
    parser.add_argument(
        "--endpoint", default="https://api.openstreetmap.org/api/0.6/map"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--alt-m", type=float, required=True)
    parser.add_argument("--level-height-m", type=float, required=True)
    parser.add_argument(
        "--public-url", required=True, help="Public URL of city.geojson"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = args.out / "source.osm.xml"
    query: str | None = None
    if args.osm is not None:
        if args.osm.resolve() != source.resolve():
            shutil.copyfile(args.osm, source)
        origin = "explicit local OSM: " + args.osm.name
    else:
        w, s, e, n = args.fetch_bounds
        if not (-180 <= w < e <= 180 and -90 < s < n < 90):
            parser.error("--fetch-bounds requires valid west/south/east/north bounds")
        query = urllib.parse.urlencode({"bbox": f"{w},{s},{e},{n}"})
        request = urllib.request.Request(
            args.endpoint + "?" + query,
            headers={
                "User-Agent": "AeroAgentSim/1.1 map-input-builder",
                "Accept": "application/xml",
            },
        )
        with urllib.request.urlopen(request, timeout=150) as response:
            data = response.read()
        source.write_bytes(data)
        origin = args.endpoint
    bounds = extract_bounds(source) if args.osm is not None else args.fetch_bounds
    scene = compile_scene(
        source, bounds, alt=args.alt_m, level_height_m=args.level_height_m
    )
    for name, data_out in {
        "city.geojson": scene["geojson"],
        "scene.json": scene,
        "presentation.json": {
            "origin": scene["origin"],
            "scene": {
                "city": {"kind": "geojson", "url": args.public_url},
                "attribution": scene["attribution"]
                + " · https://www.openstreetmap.org/copyright",
            },
        },
        "provenance.json": {
            "source": origin,
            "query": query,
            "license": "ODbL-1.0",
            "bounds": bounds,
            "assumptions": {"alt_m": args.alt_m, "level_height_m": args.level_height_m},
        },
    }.items():
        (args.out / name).write_text(
            json.dumps(data_out, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        )
    (args.out / "ATTRIBUTION.txt").write_text(
        "© OpenStreetMap contributors\nhttps://www.openstreetmap.org/copyright\n"
        "Source database: ODbL 1.0 https://opendatacommons.org/licenses/odbl/1.0/\n"
        "Source data supplied as source.osm.xml; generated geometry uses Studio ENU.\n"
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "features": len(scene["geojson"]["features"]),
                "diagnostics": scene["diagnostics"],
            }
        )
    )


if __name__ == "__main__":
    main()
