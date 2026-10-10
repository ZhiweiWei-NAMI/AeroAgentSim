"""Build an optional Huangpu city pack; asset bytes stay out of the repository."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from aeroagentsim.assets import FORMAT, PACK_ID, VERSION

NOTICE = """Redistributed by the AeroAgentSim project owner.
Building footprints and roads derived from OpenStreetMap:
© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright).
Holybro X500 model from the PX4 Gazebo models; see any accompanying model notices.
License for the building meshes and textures: <to be stated by the project owner>
"""


def build_pack(source: Path, output: Path) -> dict[str, object]:
    if not (source / "scene.json").is_file():
        raise FileNotFoundError("City source must contain scene.json")
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.name != "NOTICE.txt")
    if any(p.is_symlink() for p in files):
        raise ValueError("City source symlinks are unsupported")
    notice = NOTICE
    # Preserve factual source metadata without inventing a missing copyright notice.
    model = source / "models/uav.glb"
    if model.is_file():
        import struct

        data = model.read_bytes()
        length = struct.unpack_from("<I", data, 12)[0]
        gltf = json.loads(data[20:20+length])
        extra = gltf.get("asset", {}).get("extras", gltf.get("extras", {}))
        if "source_archive" in extra:
            notice += "\nX500 source archive recorded in the supplied glTF: " + extra["source_archive"] + "\n"
        if not any(p.name.lower().startswith(("license", "copying")) for p in source.rglob("*")):
            notice += "No upstream BSD-3 copyright/notice text accompanies the supplied asset directory.\n"
    original = source / "NOTICE.txt"
    if original.is_file():
        notice += "\nOriginal source notice:\n" + original.read_text()
    for path in files:
        if path.name.lower().startswith(("license", "copying")):
            notice += f"\nModel/source notice ({path.relative_to(source)}):\n" + path.read_text()
    manifest = {
        "format": FORMAT, "id": PACK_ID, "version": VERSION,
        "title": "Shanghai Huangpu — detailed city",
        "frame": {"convention": "enu", "unit": "m", "origin": [0.0, 0.0, 0.0],
                  "mapping": "E = scene x, N = -scene z, U = scene y", "revision": "traffic.local/v1"},
        "files": [{"path": "assets/" + p.relative_to(source).as_posix(), "bytes": p.stat().st_size} for p in files]
                 + [{"path": "NOTICE.txt", "bytes": len(notice.encode())}],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pack.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("NOTICE.txt", notice)
        for path in files:
            archive.write(path, "assets/" + path.relative_to(source).as_posix())
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build_pack(args.source, args.output)
    print(json.dumps({"pack": str(args.output), "files": len(result["files"]), "bytes": args.output.stat().st_size}))
