"""Capture 640x360 template thumbnails with the real console Viewport and Huangpu 1.1.

Run with PYTHONPATH=aerokernel:src and AEROAGENTSIM_CHROMIUM set. --run reuses
committed journals; otherwise templates run headlessly. Runs, bundles and optional
review captures go under --out-run-root (outside the checkout by default).
Requires frontend npm dependencies and Pillow for palette PNGs. Only camera,
cut, selection and the console day/dusk switch change; simulation poses and
models come from the normal console feed and installed city pack.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import mimetypes
import struct
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aerokernel import Cut, EntityRef, Stamp
from aerokernel.codec import decode_record
from aerokernel.time import cut_data

from aeroagentsim.assets import selected
from aeroagentsim.authoring.demo import city_file, detailed_city
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.observations.renderer import BrowserRenderer, browser_executable_path
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.artifacts import scope_capture_storage
from aeroagentsim.services.node_deps import node_modules_root
from aeroagentsim.services.projector import header, project
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.template_execution import execution_document

WIDTH, HEIGHT, MAX_BYTES = 640, 360, 150_000
REPOSITORY = Path(__file__).resolve().parents[2]

# Authored camera offsets use render coordinates (east, up, south), relative to
# the selected actor at the committed cut. Targets may include a nearby site.
PRESETS = {
    "parcel-delivery": {
        "seconds": 77,
        "subject": "drone-6",
        "eye": [6, 5, 7],
        "target": [0, -3, 0],
    },
    "medical-delivery": {
        "seconds": 53,
        "subject": "drone-2",
        "eye": [-5, 1.8, 6],
        "target": [0, -0.2, 0],
    },
    "powerline-inspection": {
        "seconds": 110,
        "subject": "drone-1",
        "eye": [-5, 3, -6],
        "target": [0, -0.5, 0],
    },
    "crop-spraying": {
        "seconds": 65,
        "subject": "drone-1",
        "eye": [5, 3, 6],
        "target": [0, -0.6, 0],
    },
    "air-taxi": {
        "seconds": 90,
        "subject": "drone-1",
        "eye": [7, 4, -10],
        "target": [0, -1.5, 0],
    },
    "security-patrol": {
        "seconds": 98,
        "subject": "drone-1",
        "eye": [6, 3, 7],
        "target": [0, -0.5, 0],
        "dusk": True,
    },
    "city-day": {
        "seconds": 40,
        "subject": "drone-1",
        "eye": [8, 4, 7],
        "target": [-1, -1, 0],
    },
    "traffic-accident": {
        "seconds": 45,
        "subject": "vehicle.incident.a",
        "eye": [-10, 3, -15],
        "target": [0, 2, 5],
        "fov": 35,
    },
}


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


def capture_request(
    path: Path, cut: Cut, preset: dict[str, Any], feed: dict[str, Any]
) -> CaptureRequest:
    # Fold the already projected committed feed instead of replaying the whole
    # simulation a second time merely to position an observer's camera.
    entities: dict[tuple[str, int], dict[str, Any]] = {}
    for commit in feed["commits"]:
        for entity in commit["created"]:
            entities[(entity["id"], entity["generation"])] = {**entity, "facts": {}}
        for fact in commit["retracted"]:
            key = fact["entity"]
            entities[(key["id"], key["generation"])]["facts"].pop(fact["fieldId"], None)
        for fact in commit["facts"]:
            key = fact["entity"]
            entities[(key["id"], key["generation"])]["facts"][fact["fieldId"]] = fact
        for entity in commit["removed"]:
            del entities[(entity["id"], entity["generation"])]
    subject = next(
        entity for entity in entities.values() if entity["id"] == preset["subject"]
    )
    types = {row["typeId"]: row for row in feed["header"]["types"]}
    binding = next(
        row
        for row in feed["header"]["presentation"]
        if row["typeId"] in [subject["typeId"], *types[subject["typeId"]]["ancestors"]]
    )
    if binding["frame"] != "enu":
        raise ValueError("Thumbnail subject must have a measured ENU pose")
    pose = subject["facts"][binding["positionField"]]
    e, n, u = pose["value"]
    position = [e, u, -n]
    if not all(
        type(value) in (int, float) and math.isfinite(value) for value in position
    ):
        raise ValueError("Thumbnail subject has no finite committed pose")
    metadata = RunStorage(path).metadata()
    actor = EntityRef(
        metadata["kernel_run_id"],
        metadata["epoch"],
        subject["id"],
        subject["generation"],
        subject["typeId"],
    )
    stamp = pose["acquired"]
    acquired = Stamp(
        stamp["clockId"],
        int(stamp["numerator"]),
        int(stamp["denominator"]),
        stamp["mappingId"],
    )
    camera = {
        "revision": "template-console/v2",
        "fov": preset.get("fov", 48),
        "dusk": preset.get("dusk", False),
        "overview": {
            "eye": [a + b for a, b in zip(position, preset["eye"])],
            "target": [a + b for a, b in zip(position, preset["target"])],
        },
        "subject": {"id": subject["id"], "generation": subject["generation"]},
        "snapshot": {
            "cut": cut_data(cut),
            "position": pose["value"],
            "version": pose["version"],
        },
    }
    return CaptureRequest(
        f"thumbnail-{uuid.uuid4().hex}",
        actor.run_id,
        actor,
        cut,
        camera,
        "huangpu-city/1.1.0",
        acquired,
        WIDTH,
        HEIGHT,
        180,
    )


def viewer_feed(path: Path, cut: Cut) -> dict[str, Any]:
    """The console's own WAL projection, ending at the exact requested cut."""
    run_header = header(path)
    if run_header.get("cityDetail") != "detailed" or not run_header.get("modelCatalog"):
        raise ValueError(
            "Thumbnails require the installed detailed Huangpu 1.1 model pack"
        )
    commits = []
    start = 1
    storage = RunStorage(path)
    while start <= cut.index:
        records = storage.records(start, min(512, cut.index - start + 1))
        if not records:
            raise ValueError(f"Journal ends before thumbnail cut {cut.index}")
        commits.extend(project(record) for record in records)
        start = records[-1]["index"] + 1
    return {"header": run_header, "commits": commits}


def build_viewer(destination: Path) -> Path:
    """Bundle the shared console renderer, keeping build products outside git."""
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination / "thumbnail-viewer.js"
    subprocess.run(
        [
            str(REPOSITORY / "frontend/node_modules/.bin/esbuild"),
            str(Path(__file__).with_name("thumbnail-viewer.ts")),
            "--bundle",
            "--format=esm",
            "--platform=browser",
            "--target=es2022",
            f"--outfile={bundle}",
        ],
        cwd=REPOSITORY / "frontend",
        check=True,
    )
    return bundle


def capture_png(
    request: CaptureRequest, browser: Path | None, *, feed: dict[str, Any], bundle: Path
) -> bytes:
    modules = node_modules_root()
    html = b"""<!doctype html><html><head><meta charset="utf-8">
<style>html,body,#viewport{margin:0;width:100%;height:100%;overflow:hidden}
canvas{display:block}.viewport-labels{display:none}</style></head>
<body><div id="viewport"></div><script type="module" src="/viewer.js"></script></body></html>"""
    assets = {
        "/": (html, "text/html"),
        "/viewer.js": (bundle.read_bytes(), "text/javascript"),
        "/feed.json": (json.dumps(feed, allow_nan=False).encode(), "application/json"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            from urllib.parse import unquote, urlsplit

            name = unquote(urlsplit(self.path).path)
            if name in assets:
                data, mime = assets[name]
            elif name.startswith("/v1/studio/demo-assets/"):
                try:
                    path = city_file(name.removeprefix("/v1/studio/demo-assets/"))
                except (ValueError, FileNotFoundError):
                    self.send_error(404)
                    return
                data = path.read_bytes()
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            elif name.startswith("/addons/") or name.startswith("/decoders/"):
                prefix = "/addons/" if name.startswith("/addons/") else "/decoders/"
                root = (
                    modules / "three/examples/jsm"
                    if prefix == "/addons/"
                    else REPOSITORY / "frontend/public/decoders"
                ).resolve()
                path = (root / name[len(prefix) :]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    self.send_error(404)
                    return
                data = path.read_bytes()
                mime = (
                    "text/javascript"
                    if path.suffix == ".js"
                    else "application/octet-stream"
                )
            else:
                self.send_error(404)
                return
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
            timeout_s=180,
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
            "Frame exceeds 150 KB; install Pillow to quantize it"
        ) from exc
    image = Image.open(io.BytesIO(png)).convert("RGB").quantize(colors=256)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    result = output.getvalue()
    if len(result) > MAX_BYTES:
        raise ValueError("Captured thumbnail exceeds 150 KB after quantization")
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
    parser.add_argument(
        "--out-run-root", type=Path, default=Path("/tmp/aas-q/thumbs/runs")
    )
    parser.add_argument(
        "--templates-root", type=Path, default=REPOSITORY / "scenarios/templates"
    )
    parser.add_argument(
        "--at-seconds", type=float, help="Override the per-template action cut"
    )
    parser.add_argument(
        "--preset-file", type=Path, help="JSON camera overrides keyed by template id"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Review captures here before replacing shipped assets",
    )
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    if args.run and len(args.run) != len(args.templates):
        parser.error("Provide one --run per template, or omit all --run options")
    if not detailed_city() or not (pack := selected()) or pack["version"] != "1.1.0":
        parser.error(
            "Install the detailed Huangpu city pack 1.1 before capturing thumbnails"
        )
    overrides = json.loads(args.preset_file.read_text()) if args.preset_file else {}
    bundle = build_viewer(args.out_run_root.parent / "viewer")
    registry = TemplateRegistry(args.templates_root)
    for index, identifier in enumerate(args.templates):
        registry.get(identifier)
        run = (
            args.run[index]
            if args.run
            else run_template(registry, identifier, args.out_run_root)
        )
        preset = {**PRESETS[identifier], **overrides.get(identifier, {})}
        cut = source_cut(
            run, args.at_seconds if args.at_seconds is not None else preset["seconds"]
        )
        feed = viewer_feed(run, cut)
        png = bounded_png(
            capture_png(
                capture_request(run, cut, preset, feed),
                args.browser,
                feed=feed,
                bundle=bundle,
            )
        )
        destination = (
            args.output_dir / f"{identifier}.png"
            if args.output_dir
            else args.templates_root / identifier / "thumbnail.png"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(png)
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
