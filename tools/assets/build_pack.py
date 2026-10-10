"""Build an optional Huangpu city pack; asset bytes stay out of the repository."""
from __future__ import annotations

import argparse
import json
import struct
import zipfile
from pathlib import Path

from aeroagentsim.assets import FORMAT, PACK_ID, VERSION

NOTICE = """Redistributed by the AeroAgentSim project owner.
Building footprints and roads derived from OpenStreetMap:
© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright).
Holybro X500 model from the PX4 Gazebo models; see any accompanying model notices.
License for the building meshes and textures: <to be stated by the project owner>
"""


def glb_json(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path.name}: not a binary glTF")
    length = struct.unpack_from("<I", data, 12)[0]
    result = json.loads(data[20:20 + length])
    if not isinstance(result, dict):
        raise ValueError(f"{path.name}: invalid glTF JSON chunk")
    return result


def _model_files(catalog: dict[str, dict[str, object]], model_source: Path) -> dict[str, Path]:
    """Pack path (relative to assets/) -> source file, including relative textures of each GLB."""
    out: dict[str, Path] = {}

    def add(pack_path: str, file: Path) -> None:
        if not file.is_file() or file.is_symlink():
            raise FileNotFoundError(f"Model source is missing: {file}")
        if out.setdefault(pack_path, file) != file:
            raise ValueError(f"Two sources claim {pack_path}")

    for ident, row in catalog.items():
        for source_key, path_key in (("source", "path"), ("lod_source", "lod")):
            if source_key not in row:
                continue
            file, pack_path = model_source / str(row[source_key]), str(row[path_key])
            add(pack_path, file)
            for image in glb_json(file).get("images", []):  # type: ignore[attr-defined]
                uri = image.get("uri") if isinstance(image, dict) else None
                if uri is None:
                    continue
                if not isinstance(uri, str) or uri.startswith(("data:", "/", "..")) or "://" in uri:
                    raise ValueError(f"{ident}: unsupported texture URI {uri!r}")
                add(str(Path(pack_path).parent / uri), file.parent / uri)
    return out


def _provenance(catalog: dict[str, dict[str, object]], model_source: Path) -> str:
    """Factual per-model provenance copied from each glTF; nothing is invented."""
    lines = ["", "Display models (asset catalog `models`). Provenance below is copied verbatim from the",
             "glTF files supplied by the project owner; no upstream licence or author text was embedded",
             "in them unless quoted here. Licence for these models: <to be stated by the project owner>", ""]
    seen: set[str] = set()
    for ident, row in catalog.items():
        for key in ("source", "lod_source"):
            if key not in row or str(row[key]) in seen:
                continue
            seen.add(str(row[key]))
            asset = glb_json(model_source / str(row[key])).get("asset", {})
            assert isinstance(asset, dict)
            lines.append(f"- {row['path'] if key == 'source' else row['lod']} ({row.get('label', ident)}, kind {row['kind']})")
            lines.append(f"    glTF generator: {asset.get('generator', 'not recorded')}")
            for field in ("copyright",):
                if field in asset:
                    lines.append(f"    glTF {field}: {asset[field]}")
            extras = asset.get("extras")
            if isinstance(extras, dict):
                for name, value in extras.items():
                    text = json.dumps(value, ensure_ascii=False)
                    lines.append(f"    glTF extras.{name}: {text if len(text) < 300 else text[:297] + '...'}")
            else:
                lines.append("    glTF extras: none (no source archive, author or licence recorded)")
    return "\n".join(lines) + "\n"


def build_pack(source: Path, output: Path, *, catalog: Path | None = None,
               model_source: Path | None = None) -> dict[str, object]:
    if not (source / "scene.json").is_file():
        raise FileNotFoundError("City source must contain scene.json")
    files = sorted(p for p in source.rglob("*") if p.is_file() and p.name != "NOTICE.txt")
    if any(p.is_symlink() for p in files):
        raise ValueError("City source symlinks are unsupported")
    models: dict[str, dict[str, object]] = {}
    model_files: dict[str, Path] = {}
    if (catalog is None) != (model_source is None):
        raise ValueError("--catalog and --model-source go together")
    if catalog is not None and model_source is not None:
        models = json.loads(catalog.read_text())
        model_files = _model_files(models, model_source)
        clash = set(model_files) & {p.relative_to(source).as_posix() for p in files}
        if clash:
            raise ValueError(f"Model files collide with the city source: {sorted(clash)}")
    notice = NOTICE
    # Preserve factual source metadata without inventing a missing copyright notice.
    model = source / "models/uav.glb"
    if model.is_file():
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
    if models:
        assert model_source is not None
        notice += _provenance(models, model_source)
    entries = {p.relative_to(source).as_posix(): p for p in files} | model_files
    manifest: dict[str, object] = {
        "format": FORMAT, "id": PACK_ID, "version": VERSION,
        "title": "Shanghai Huangpu — detailed city",
        "frame": {"convention": "enu", "unit": "m", "origin": [0.0, 0.0, 0.0],
                  "mapping": "E = scene x, N = -scene z, U = scene y", "revision": "traffic.local/v1"},
        "files": [{"path": "assets/" + name, "bytes": path.stat().st_size} for name, path in sorted(entries.items())]
                 + [{"path": "NOTICE.txt", "bytes": len(notice.encode())}],
    }
    if models:
        manifest["models"] = {
            ident: {key: ("assets/" + str(value) if key in ("path", "lod") else value)
                    for key, value in row.items() if key not in ("source", "lod_source")}
            for ident, row in models.items()
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pack.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("NOTICE.txt", notice)
        for name, path in sorted(entries.items()):
            archive.write(path, "assets/" + name)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--catalog", type=Path, help="model catalog JSON (tools/assets/model_catalog.json)")
    parser.add_argument("--model-source", type=Path, help="directory the catalog `source` paths are relative to")
    args = parser.parse_args()
    result = build_pack(args.source, args.output, catalog=args.catalog, model_source=args.model_source)
    print(json.dumps({"pack": str(args.output), "files": len(result["files"]), "bytes": args.output.stat().st_size}))
