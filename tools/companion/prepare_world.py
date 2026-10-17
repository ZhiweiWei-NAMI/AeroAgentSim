#!/usr/bin/env python3
"""Build a physical public-map world and separate evaluator-only person layout.

Run during asset preparation. Runtime agents receive the public YAML only;
world SDF and evaluator labels must never be copied into companion images.
"""
from __future__ import annotations

import argparse
import json
import random
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml


def element(parent: ET.Element, name: str, text: Any) -> ET.Element:
    item = ET.SubElement(parent, name)
    item.text = str(text)
    return item


def box(world: ET.Element, name: str, lower: list[float], upper: list[float], color: str,
        collision: bool = True) -> ET.Element:
    model = ET.SubElement(world, "model", name=name)
    element(model, "static", "true")
    element(model, "pose", " ".join(str((a+b)/2) for a, b in zip(lower, upper))+" 0 0 0")
    link = ET.SubElement(model, "link", name="body")
    size = " ".join(str(b-a) for a, b in zip(lower, upper))
    for kind in (("visual", "collision") if collision else ("visual",)):
        part = ET.SubElement(link, kind, name=kind)
        element(ET.SubElement(ET.SubElement(part, "geometry"), "box"), "size", size)
        if kind == "visual":
            material = ET.SubElement(part, "material")
            element(material, "ambient", color)
            element(material, "diffuse", color)
    return model


def build_world(scene: dict[str, Any], asset_root: Path, seed: int, people_count: int,
                variant: str = "default", output_root: Path | None = None) -> dict[str, Any]:
    human = asset_root / "scene" / "makehuman_person" / "model.sdf"
    if not human.is_file():
        raise FileNotFoundError("prepare_assets.py must install the actual MakeHuman mesh first")
    public = scene["public_map"]
    sdf = ET.Element("sdf", version="1.9")
    world = ET.SubElement(sdf, "world", name="rooftop_flood_companion")
    physics = ET.SubElement(world, "physics", name="lockstep", type="ignored")
    element(physics, "max_step_size", .004)
    element(physics, "real_time_factor", 0)
    element(world, "gravity", "0 0 -9.80665")
    element(world, "magnetic_field", "2.15e-5 0.0 -4.27e-5")
    atmosphere = ET.SubElement(world, "atmosphere", type="adiabatic")
    element(atmosphere, "temperature", 288.15)
    element(atmosphere, "pressure", 101325)
    spherical = ET.SubElement(world, "spherical_coordinates")
    for key, value in (("surface_model", "EARTH_WGS84"), ("world_frame_orientation", "ENU"),
                       ("latitude_deg", 47.397742), ("longitude_deg", 8.545594),
                       ("elevation", 0), ("heading_deg", 0)):
        element(spherical, key, value)
    # Explicit research datum: world elevation 0 is the simulated AMSL zero.
    # No measured real-world geoid calibration is claimed by this fixture.
    for system, library in (("Physics", "physics"), ("UserCommands", "user-commands"),
                            ("SceneBroadcaster", "scene-broadcaster"), ("Sensors", "sensors"),
                            ("Imu", "imu"), ("NavSat", "navsat"), ("Magnetometer", "magnetometer"),
                            ("AirPressure", "air-pressure"), ("Contact", "contact")):
        plugin = ET.SubElement(world, "plugin", filename=f"gz-sim-{library}-system", name=f"gz::sim::systems::{system}")
        if system == "Sensors":
            element(plugin, "render_engine", "ogre2")
    light = ET.SubElement(world, "light", name="daylight", type="directional")
    for key, value in (("cast_shadows", "true"), ("pose", "0 0 100 0 0 0"),
                       ("diffuse", "0.9 0.9 0.9 1"), ("specular", "0.2 0.2 0.2 1"),
                       ("direction", "-0.4 0.2 -0.9")):
        element(light, key, value)
    visual_scene = ET.SubElement(world, "scene")
    element(visual_scene, "ambient", "0.4 0.4 0.4 1")
    element(visual_scene, "background", "0.6 0.75 0.85 1")
    element(visual_scene, "shadows", "true")
    box(world, "ground", [-150, -150, -1], [150, 150, 0], "0.35 0.37 0.32 1")
    # Visual shallow flood: no buoyancy/water dynamics are asserted.
    box(world, "flood_surface", [25, 25, .35], [110, 110, .4], "0.12 0.34 0.43 1", collision=False)
    for obstacle in public["obstacles"]:
        box(world, obstacle["id"], obstacle["lower"], obstacle["upper"], "0.65 0.65 0.62 1")
    roof_id = public.get("rescue_roof_id", "roof-1")
    roof = next(obstacle for obstacle in public["obstacles"] if obstacle["id"] == roof_id)
    lower, upper = roof["lower"], roof["upper"]
    rng = random.Random(seed)
    people = []
    count = 0 if variant == "no_person" else people_count
    receiver = public["receiver_polygon"]
    rx1, rx2 = min(p[0] for p in receiver), max(p[0] for p in receiver)
    ry1, ry2 = min(p[1] for p in receiver), max(p[1] for p in receiver)
    for index in range(count):
        for _ in range(1000):
            x, y = rng.uniform(lower[0]+3, upper[0]-3), rng.uniform(lower[1]+3, upper[1]-3)
            if rx1-6 <= x <= rx2+6 and ry1-6 <= y <= ry2+6:
                continue
            if all((x-p["position_enu_m"][0])**2+(y-p["position_enu_m"][1])**2 >= 4 for p in people):
                break
        else:
            raise ValueError("not enough roof area for the requested people")
        yaw = rng.uniform(-3.141592653589793, 3.141592653589793)
        record = {"entity_id": f"person-{index+1}", "position_enu_m": [x, y, upper[2]],
                  "yaw_rad": yaw, "height_m": 1.75, "position_reference": "mesh feet ground origin",
                  "evaluation_note": "horizontal visible-surface localization may differ by body shape"}
        people.append(record)
        include = ET.SubElement(world, "include")
        element(include, "uri", "/assets/scene/makehuman_person/model.sdf")
        element(include, "name", record["entity_id"])
        element(include, "pose", f"{x} {y} {upper[2]} 0 0 {yaw}")
        if variant in {"occluded_then_visible", "still_uncertain"}:
            width = 1.4 if variant == "occluded_then_visible" else 3.0
            box(world, f"canopy-{index+1}", [x-width/2, y-width/2, upper[2]+1.95],
                [x+width/2, y+width/2, upper[2]+2.05], "0.25 0.3 0.35 1")
    output_root = output_root or asset_root
    world_dir, truth_dir = output_root / "worlds", output_root / "evaluator"
    world_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)
    output = world_dir / "rooftop-flood.sdf"
    ET.indent(sdf)
    ET.ElementTree(sdf).write(output, encoding="utf-8", xml_declaration=True)
    truth = {"seed": seed, "variant": variant, "world": "rooftop_flood_companion", "people": people,
             "scope": "evaluator only; never mount in companion or centre", "native_render_status": "NOT_RUN"}
    (truth_dir / "scene-truth.json").write_text(json.dumps(truth, indent=2))
    manifest = {"world": str(output), "simulator_world_path": "/worlds/rooftop-flood.sdf",
                "altitude_datum": "amsl", "world_elevation_m": 0,
                "height_datum_note": "declared local simulation AMSL zero; no real geoid survey",
                "public_geometry_source": "scenario public_map obstacles",
                "rescue_roof_id": roof_id,
                "human_model": "scene/makehuman_person/model.sdf", "human_license": "CC0-1.0",
                "hidden_geometry": "evaluator/scene-truth.json and simulator world only",
                "water_model": "visual surface only; no hydrodynamics",
                "payload_receiver": "attached by native payload profile",
                "native_execution_status": "NOT_RUN"}
    (world_dir / "world-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("scenarios/rescue/rooftop-flood-companion.yaml"))
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, help="per-run output root; defaults to the asset cache")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--people", type=int, default=2)
    parser.add_argument("--variant", choices=["default", "no_person", "occluded_then_visible", "still_uncertain"], default="default")
    args = parser.parse_args()
    scene = yaml.safe_load(args.scenario.read_text())
    print(json.dumps(build_world(scene, args.asset_root, args.seed, args.people, args.variant, args.output_root), indent=2))


if __name__ == "__main__":
    main()
