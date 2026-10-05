#!/usr/bin/env python3
"""Build and audit a SUMO road preview before replacing the published asset."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from city_preview_scene import ROOT, read_scene_paths

SCRIPTS = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--building-objects", type=Path, required=True)
    parser.add_argument("--building-render", type=Path, required=True)
    parser.add_argument("--source-osm", type=Path)
    parser.add_argument("--report", type=Path, default=ROOT / "frontend/validation/city-road-audit.json")
    parser.add_argument("--tile-size-m", type=int, default=60)
    args = parser.parse_args()
    output = read_scene_paths(args.scene).road
    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".json", dir=output.parent)
    os.close(file_descriptor)
    staged = Path(temporary_name)
    source_osm_option = ["--source-osm", str(args.source_osm)] if args.source_osm is not None else []
    try:
        subprocess.run([sys.executable, str(SCRIPTS / "build-city-road-preview.py"),
                        "--scene", str(args.scene), "--network", str(args.network),
                        "--building-objects", str(args.building_objects),
                        "--building-render", str(args.building_render),
                        *source_osm_option, "--tile-size-m", str(args.tile_size_m),
                        "--output", str(staged)], check=True)
        subprocess.run([sys.executable, str(SCRIPTS / "audit-city-road-preview.py"),
                        "--scene", str(args.scene), "--network", str(args.network),
                        *source_osm_option, "--report", str(args.report), "--road", str(staged)], check=True)
        subprocess.run(["node", str(SCRIPTS / "audit-city-road-triangulation.mjs"), str(staged)], check=True)
        staged.replace(output)
        print(f"Published audited SUMO road preview: {output}")
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
