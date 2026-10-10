"""Render real pinned template poses with the existing Chromium capture/lite city.

Run with PYTHONPATH=aerokernel:src. --run reuses a journal; otherwise the template
runs headlessly into a normal run directory. Pillow is only needed for PNGs over
120 KB (palette quantization changes color precision, not simulation poses).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import threading
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aerokernel import Cut, Fact
from aerokernel.codec import decode_record
from aerokernel.journal import replay
from aerokernel.registry import MemoryRegistry
from aerokernel.time import cut_data
from aerokernel.values import thaw

from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.observations.renderer import BrowserRenderer, browser_executable_path
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.artifacts import scope_capture_storage
from aeroagentsim.services.node_deps import node_modules_root
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.template_execution import execution_document

WIDTH, HEIGHT, MAX_BYTES = 640, 360, 120 * 1024
REPOSITORY = Path(__file__).resolve().parents[2]


def run_template(registry: TemplateRegistry, identifier: str, root: Path) -> Path:
    prepared = registry.apply(identifier)
    document = execution_document(identifier, prepared.document, prepared.base)
    document["run"]["pacing"] = "fast"
    path = root / f"thumbnail-{identifier}-{uuid.uuid4().hex[:10]}"
    scenario = scope_capture_storage(load_scenario(document, base=prepared.base), path)
    with RunSession(scenario, path) as session:
        session.storage._atomic(
            "manifest.json",
            {
                **session.storage.metadata(),
                "template": {
                    "id": identifier,
                    "title": prepared.metadata["title"],
                    "knobs": prepared.knobs,
                },
            },
        )
        session.run()
    if session.storage.metadata()["status"] != "completed":
        raise RuntimeError(f"Template run failed: {session.storage.metadata()}")
    return path


def source_cut(path: Path, seconds: float) -> Cut:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("--at-seconds: finite nonnegative time required")
    selected = None
    start = 1
    storage = RunStorage(path)
    while records := storage.records(start, 512):
        for record in records:
            instant = decode_record(record["instant"])
            if instant.ns > round(seconds * 1e9):
                if selected is None:
                    raise ValueError("No committed cut exists at the requested time")
                return selected
            selected = Cut(record["index"], instant)
        start = records[-1]["index"] + 1
    if selected is None:
        raise ValueError("Run has no committed simulation cuts")
    return selected


def capture_request(path: Path, cut: Cut) -> CaptureRequest:
    document = json.loads((path / "scenario.json").read_text())
    registry = MemoryRegistry.from_data(
        json.loads((path / "runtime.registry.json").read_text())
    )
    view = replay(path / "journal.jsonl").view(cut)
    rows: list[dict[str, Any]] = []
    actor = acquired = None
    for ref in sorted(
        view._store.lives, key=lambda value: (value.id, value.generation)
    ):
        if view._store.lives[ref].created.index > cut.index:
            continue
        if not view._store.alive(ref, cut, cut.instant):
            continue
        binding = next(
            (
                item
                for item in document.get("presentation", [])
                if registry.is_a(ref.type_id, item["typeId"])
            ),
            None,
        )
        if binding is None:
            continue  # Nonspatial entities have no presentation in this observer.
        field = registry.field(binding["positionField"])
        unit = field.metadata["unit"]
        unit = unit.get("symbol") if hasattr(unit, "get") else unit
        frame = field.metadata["frame"]
        if (
            binding["frame"] != "enu"
            or (isinstance(frame, str) and frame != "enu")
            or unit != "m"
        ):
            raise ValueError(f"{ref.id}: capture requires declared ENU poses in metres")
        fact = view.field((ref, field.id), cut.instant)
        if not isinstance(fact, Fact):
            raise ValueError(f"{ref.id}: no measured pose at cut {cut.index}")
        position = thaw(fact.value)
        if (
            not isinstance(position, list)
            or len(position) != 3
            or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in position
            )
        ):
            raise ValueError(f"{ref.id}: invalid committed position triple")
        aircraft = registry.is_a(ref.type_id, "oo:UAV")
        visual = binding["visual"]
        rows.append(
            {
                "id": ref.id,
                "generation": ref.generation,
                "position": position,
                "aircraft": aircraft,
                "color": visual["color"],
                "scale": visual["scale"],
                "field": field.id,
                "version": asdict(fact.version),
                "acquired": asdict(fact.acquired),
            }
        )
        if actor is None or aircraft:
            actor, acquired = ref, fact.acquired
    if actor is None or acquired is None:
        raise ValueError("No live entity has a declared, measured spatial presentation")
    east = [row["position"][0] for row in rows]
    north = [row["position"][1] for row in rows]
    up = [row["position"][2] for row in rows]
    center_e, center_n = (min(east) + max(east)) / 2, (min(north) + max(north)) / 2
    # Frame the shipped city when it overlaps the measured scene. Entities stay
    # at their actual poses; this observer may crop distant traffic, like a camera.
    city = json.loads(
        (demo_source("traffic-accident") / "inputs/lite-city/scene.json").read_text()
    )
    footprints = [
        point for building in city["buildings"] for point in building["footprint"]
    ]
    city_e = [point[0] for point in footprints]
    city_n = [point[1] for point in footprints]
    if (
        max(east) >= min(city_e)
        and min(east) <= max(city_e)
        and max(north) >= min(city_n)
        and min(north) <= max(city_n)
    ):
        east, north = city_e, city_n
        center_e, center_n = (min(east) + max(east)) / 2, (min(north) + max(north)) / 2
    span = max(max(east) - min(east), (max(north) - min(north)) * WIDTH / HEIGHT, 100)
    elevation = span / (2 * math.tan(math.radians(55 / 2))) * 1.25 + max(up)
    camera = {
        "revision": "template-overview/v1",
        "fov": 55,
        "overview": {
            "eye": [center_e, elevation, -center_n + elevation * 0.35],
            "target": [center_e, 0, -center_n],
        },
        "snapshot": {"cut": cut_data(cut), "entities": rows},
    }
    return CaptureRequest(
        f"thumbnail-{uuid.uuid4().hex}",
        actor.run_id,
        actor,
        cut,
        camera,
        "traffic-lite-city/v1",
        acquired,
        WIDTH,
        HEIGHT,
        90,
    )


def capture_png(request: CaptureRequest, browser: Path | None) -> bytes:
    from aeroagentsim.packs.traffic_accident import camera as traffic_camera

    modules = node_modules_root()
    html = Path(traffic_camera.__file__).with_name("camera.html").read_text()
    original = """  const p = actor.position;
  const camera = new THREE.PerspectiveCamera(request.camera.fov, request.width / request.height, 0.1, 2000);
  camera.position.set(p[0], p[2], -p[1]);
  camera.up.set(0, 0, -1);
  camera.lookAt(p[0], 0, -p[1]);"""
    if original not in html:
        raise ValueError("Existing capture viewer camera contract changed")
    html = html.replace(
        original,
        """  const camera = new THREE.PerspectiveCamera(request.camera.fov, request.width / request.height, 0.1, 16000);
  camera.position.set(...request.camera.overview.eye);
  camera.lookAt(...request.camera.overview.target);""",
    )
    html = html.replace(
        "    if (entity.id === actor.id && entity.generation === actor.generation) continue;",
        "",
    )
    html = html.replace(
        "const air = entity.position[2] > 10;", "const air = entity.aircraft;"
    )
    html = html.replace("color: air ? 0x50c9ff : 0xe8a444", "color: entity.color")
    html = html.replace(
        "    scene.add(mesh);\n    if (air && city)",
        "    mesh.scale.setScalar(Math.max(3, entity.scale));\n    scene.add(mesh);\n    if (air && city)",
    )
    assets = {
        "/": (html.encode(), "text/html"),
        "/three.js": (
            (modules / "three/build/three.module.js").read_bytes(),
            "text/javascript",
        ),
        "/camera-config.json": (b'{"city":true}', "application/json"),
        "/city.json": (
            (
                demo_source("traffic-accident") / "inputs/lite-city/scene.json"
            ).read_bytes(),
            "application/json",
        ),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in assets:
                self.send_error(404)
                return
            data, mime = assets[self.path]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with BrowserRenderer(
            f"http://127.0.0.1:{server.server_port}/",
            node_modules=modules,
            timeout_s=90,
            browser_executable=browser_executable_path(browser),
        ) as renderer:
            return renderer.render(request).png
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def bounded_png(png: bytes) -> bytes:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", png[16:24]) != (
        WIDTH,
        HEIGHT,
    ):
        raise ValueError("Capture must produce a 640x360 PNG")
    if len(png) <= MAX_BYTES:
        return png
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Frame exceeds 120 KB; install Pillow to quantize it"
        ) from exc
    image = Image.open(io.BytesIO(png)).convert("RGB").quantize(colors=128)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    result = output.getvalue()
    if len(result) > MAX_BYTES:
        raise ValueError("Captured thumbnail exceeds 120 KB after quantization")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("templates", nargs="+")
    parser.add_argument(
        "--run",
        action="append",
        type=Path,
        default=[],
        help="One pinned run per template, in order",
    )
    parser.add_argument("--out-run-root", type=Path, default=Path("runs"))
    parser.add_argument(
        "--templates-root", type=Path, default=REPOSITORY / "scenarios/templates"
    )
    parser.add_argument("--at-seconds", type=float, default=45)
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    if args.run and len(args.run) != len(args.templates):
        parser.error("Provide one --run per template, or omit all --run options")
    registry = TemplateRegistry(args.templates_root)
    for index, identifier in enumerate(args.templates):
        registry.get(identifier)
        run = (
            args.run[index]
            if args.run
            else run_template(registry, identifier, args.out_run_root)
        )
        cut = source_cut(run, args.at_seconds)
        png = bounded_png(capture_png(capture_request(run, cut), args.browser))
        destination = args.templates_root / identifier / "thumbnail.png"
        destination.write_bytes(png)
        manifest = destination.with_name("template.yaml")
        text = manifest.read_text()
        if "thumbnail:" not in text:
            manifest.write_text(text.rstrip() + "\nthumbnail: thumbnail.png\n")
        print(
            json.dumps(
                {
                    "template": identifier,
                    "run": str(run.resolve()),
                    "cut": cut.index,
                    "at_ns": str(cut.instant.ns),
                    "path": str(destination),
                    "bytes": len(png),
                    "sha256": hashlib.sha256(png).hexdigest(),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
