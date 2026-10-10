"""Build browser previews from the neutral CAD files in Quark UAV ZIPs.

Run with the asset conversion environment described in frontend/README.md.
Each ZIP is handled in a fresh process so a failed CAD import does not discard
the rest of the inventory. The report records source selection and uncertainty:
a tessellated CAD file is not proof that an aircraft assembly is complete.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ARCHIVES = ROOT.parent / "validation/downloaded-assets/quark-drone-models/003 无人机"
OUTPUT = ROOT / "public/models/uav/cad"
REPORT = ROOT / "public/models/uav/cad-conversion-report.json"
SCAN_REPORT = ROOT / "public/models/uav/cad-scan-report.json"
STEP_EXTENSIONS = {".step", ".stp"}
IGES_EXTENSIONS = {".iges", ".igs"}
NEUTRAL_EXTENSIONS = STEP_EXTENSIONS | IGES_EXTENSIONS
MESH_EXTENSIONS = {".stl", ".obj", ".fbx", ".3dxml", ".ply", ".3mf"}
NATIVE_EXTENSIONS = {".sldprt", ".sldasm", ".ipt", ".iam", ".3dm", ".catpart", ".catproduct", ".x_t", ".x_b", ".creo-prt", ".creo-asm"}
PREFERRED_MEMBERS = {
    "davinci-h2 STP.zip": "davinci-h2 STP/Dabin_Kim_EVTOL_assembly_flight_mode.STEP",
}


def member_extension(name: str) -> str:
    lowered = name.lower()
    match = re.search(r"\.(prt|asm)\.\d+$", lowered)
    return f".creo-{match.group(1)}" if match else Path(lowered).suffix


def previewed_archives() -> set[str]:
    entries = json.loads((ROOT / "scripts/uav-preview-models.json").read_text())
    return {item["archive"] for item in entries}


def rar_contents(raw: bytes, selected: str | None = None, output: Path | None = None) -> list[dict]:
    """List or extract one member of an embedded RAR via the system libarchive."""
    library_name = ctypes.util.find_library("archive")
    if library_name is None:
        raise RuntimeError("Nested RAR found but libarchive is unavailable")
    library = ctypes.CDLL(library_name)

    def bind(name, arguments, result):
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result
        return function

    new = bind("archive_read_new", [], ctypes.c_void_p)
    enable_filters = bind("archive_read_support_filter_all", [ctypes.c_void_p], ctypes.c_int)
    enable_formats = bind("archive_read_support_format_all", [ctypes.c_void_p], ctypes.c_int)
    open_memory = bind("archive_read_open_memory", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_int)
    next_header = bind("archive_read_next_header", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int)
    pathname = bind("archive_entry_pathname_utf8", [ctypes.c_void_p], ctypes.c_char_p)
    size = bind("archive_entry_size", [ctypes.c_void_p], ctypes.c_longlong)
    read_data = bind("archive_read_data", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_longlong)
    skip_data = bind("archive_read_data_skip", [ctypes.c_void_p], ctypes.c_int)
    error = bind("archive_error_string", [ctypes.c_void_p], ctypes.c_char_p)
    close = bind("archive_read_close", [ctypes.c_void_p], ctypes.c_int)
    free = bind("archive_read_free", [ctypes.c_void_p], ctypes.c_int)
    reader = new()
    if not reader:
        raise RuntimeError("libarchive could not allocate a RAR reader")
    buffer = ctypes.create_string_buffer(raw)
    found = []
    extracted = False
    try:
        if enable_filters(reader) < 0 or enable_formats(reader) < 0 \
                or open_memory(reader, buffer, len(raw)) != 0:
            raise ValueError(f"Nested RAR open failed: {error(reader)!r}")
        while True:
            entry = ctypes.c_void_p()
            code = next_header(reader, ctypes.byref(entry))
            if code == 1:
                break
            if code < 0:
                raise ValueError(f"Nested RAR header failed: {error(reader)!r}")
            name_bytes = pathname(entry)
            if not name_bytes:
                raise ValueError("Nested RAR member has no UTF-8 pathname")
            name = name_bytes.decode("utf-8")
            declared_size = int(size(entry))
            if not name.endswith("/"):
                found.append({"path": name, "bytes": declared_size, "extension": member_extension(name)})
            if selected != name:
                if skip_data(reader) < 0:
                    raise ValueError(f"Nested RAR skip failed for {name}: {error(reader)!r}")
                continue
            if output is None:
                raise ValueError("RAR extraction output is missing")
            if extracted:
                raise ValueError(f"Duplicate nested RAR member: {name}")
            copied = 0
            chunk = ctypes.create_string_buffer(1024 * 1024)
            with output.open("wb") as target:
                while True:
                    count = read_data(reader, chunk, len(chunk))
                    if count == 0:
                        break
                    if count < 0:
                        raise ValueError(f"Nested RAR extraction failed for {name}: {error(reader)!r}")
                    target.write(chunk.raw[:count])
                    copied += count
            if copied != declared_size:
                raise ValueError(f"Nested RAR size mismatch for {name}: {copied} != {declared_size}")
            extracted = True
        if selected is not None and not extracted:
            raise ValueError(f"Nested RAR member is missing: {selected}")
    finally:
        close(reader)
        free(reader)
    return found


def members(archive_path: Path) -> list[dict]:
    with zipfile.ZipFile(archive_path) as archive:
        listed = [{"path": item.filename, "bytes": item.file_size, "extension": member_extension(item.filename)}
                  for item in archive.infolist() if not item.is_dir()]
        if any(item["extension"] in NEUTRAL_EXTENSIONS for item in listed):
            return listed
        for container in [item for item in listed if item["extension"] == ".zip"]:
            with archive.open(container["path"]) as source:
                with zipfile.ZipFile(io.BytesIO(source.read())) as nested:
                    listed.extend({"path": f"{container['path']}::{item.filename}",
                                   "bytes": item.file_size, "extension": member_extension(item.filename)}
                                  for item in nested.infolist() if not item.is_dir())
        for container in [item for item in listed if item["extension"] == ".rar"]:
            for item in rar_contents(archive.read(container["path"])):
                listed.append({**item, "path": f"{container['path']}::{item['path']}"})
        return listed


def candidate_score(item: dict) -> tuple[int, int]:
    name = Path(item["path"]).stem.lower()
    words = re.sub(r"[^a-z0-9]+", " ", name).split()
    positive = {"assembly", "assy", "assem", "asm", "complete", "full", "final", "aircraft", "drone", "uav"}
    negative = {"bolt", "nut", "washer", "motor", "propeller", "battery", "arm", "bracket", "screw", "case"}
    score = 10 if item["extension"] in STEP_EXTENSIONS else 0
    score += sum(5 for word in words if word in positive)
    score -= sum(4 for word in words if word in negative)
    return score, item["bytes"]


def is_assembly_member(item: dict) -> bool:
    stem = Path(item["path"]).stem.lower()
    return bool(re.search(r"(^|[^a-z])(assembly|assy|assem|ass|asm|assambly|complete|full|final)([^a-z]|$)", stem))


def inspect_archive(path: Path) -> dict:
    listed = members(path)
    neutral = sorted((item for item in listed if item["extension"] in NEUTRAL_EXTENSIONS),
                     key=candidate_score, reverse=True)
    preferred = PREFERRED_MEMBERS.get(path.name)
    if preferred:
        if preferred not in {item["path"] for item in neutral}:
            raise ValueError(f"Preferred CAD member is missing: {path.name}#{preferred}")
        neutral.sort(key=lambda item: item["path"] != preferred)
    extensions = sorted({item["extension"] for item in listed})
    answer = {"archive": path.name, "archive_bytes": path.stat().st_size,
              "neutral_candidates": neutral, "extensions": extensions}
    if path.name in previewed_archives():
        answer["status"] = "existing_preview"
    elif len(neutral) > 5 and not any(is_assembly_member(item) for item in neutral):
        answer["status"] = "neutral_components_require_assembly"
    elif neutral:
        answer["status"] = "queued"
        answer["selected_member"] = neutral[0]["path"]
    elif any(item["extension"] in MESH_EXTENSIONS for item in listed):
        answer["status"] = "mesh_without_neutral_cad"
    elif any(item["extension"] in NATIVE_EXTENSIONS for item in listed):
        answer["status"] = "native_cad_requires_export"
    else:
        answer["status"] = "unsupported_or_nested_source"
    return answer


def safe_stem(archive_name: str) -> str:
    stem = unicodedata.normalize("NFKD", Path(archive_name).stem).encode("ascii", "ignore").decode().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")[:68].rstrip("-") or "cad-preview"
    return f"{stem}-{sha256(archive_name.encode()).hexdigest()[:8]}"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def import_livery_module():
    path = ROOT / "scripts/build-uav-previews.py"
    spec = importlib.util.spec_from_file_location("build_uav_previews", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cad_shape(path: Path, kind: str):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDF import TDF_LabelSequence
    if kind in STEP_EXTENSIONS:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        app = XCAFApp_Application.GetApplication_s()
        document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        app.NewDocument(TCollection_ExtendedString("XmlOcaf"), document)
        reader = STEPCAFControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(document):
            raise ValueError(f"STEP import failed: {path.name}")
        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)
        if roots.Length() == 0:
            raise ValueError("STEP contains no free shapes")
        shape = tool.GetShape_s(roots.Value(1)) if roots.Length() == 1 else tool.GetOneShape_s(roots)
        return shape, {"free_roots": roots.Length(), "assembly_roots": sum(
            bool(tool.IsAssembly_s(roots.Value(i))) for i in range(1, roots.Length() + 1))}
    from OCP.IGESControl import IGESControl_Reader
    reader = IGESControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or reader.TransferRoots() <= 0:
        raise ValueError(f"IGES import failed: {path.name}")
    return reader.OneShape(), {"free_roots": reader.NbShapes(), "assembly_roots": 0,
                               "iges_unit": reader.WS().Model().GlobalSection().UnitName().String().ToCString()}


def tessellate(shape) -> tuple[np.ndarray, dict]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    if bounds.IsVoid():
        raise ValueError("CAD shape has no bounding box")
    x0, y0, z0, x1, y1, z1 = map(float, bounds.Get())
    spans = np.array((x1 - x0, y1 - y0, z1 - z0))
    if not np.isfinite(spans).all() or max(spans) <= 0:
        raise ValueError("CAD bounding box is invalid")
    deflection = float(np.clip(max(spans) * 0.0005, 0.25, 2.0))
    mesher = BRepMesh_IncrementalMesh(shape, deflection, False, 0.5, False)
    if not mesher.IsDone():
        raise ValueError("CAD tessellation failed")
    triangles: list[np.ndarray] = []
    face_count = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None and triangulation.NbTriangles():
            transform = location.Transformation()
            vertices = np.array([
                tuple(map(float, (point.X(), point.Y(), point.Z())))
                for point in (triangulation.Node(i).Transformed(transform)
                              for i in range(1, triangulation.NbNodes() + 1))
            ], dtype=np.float32)
            indexes = np.array([triangulation.Triangle(i).Get()
                                for i in range(1, triangulation.NbTriangles() + 1)], dtype=np.int32) - 1
            if face.Orientation() == TopAbs_REVERSED:
                indexes[:, [1, 2]] = indexes[:, [2, 1]]
            triangles.append(vertices[indexes])
        face_count += 1
        explorer.Next()
    if not triangles:
        raise ValueError("CAD has no tessellated faces")
    solid_count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solid_count += 1
        explorer.Next()
    return np.concatenate(triangles), {"cad_bbox_mm": [x0, y0, z0, x1, y1, z1],
                                       "cad_faces": face_count, "cad_solids": solid_count,
                                       "mesh_deflection_mm": deflection}


def orient_for_preview(triangles: np.ndarray) -> tuple[np.ndarray, str]:
    spans = np.ptp(triangles.reshape(-1, 3), axis=0)
    vertical = int(np.argmin(spans))
    if vertical == 2:
        return triangles[..., [0, 2, 1]] * np.array([1, 1, -1], dtype=np.float32), "source Z to viewer Y"
    if vertical == 0:
        return triangles[..., [1, 0, 2]] * np.array([1, 1, -1], dtype=np.float32), "source X to viewer Y"
    return triangles, "source Y retained"


def livery_scheme(archive_name: str) -> str:
    return "multirotor" if re.search(r"quad|hex|octo|tri.?copter|multirotor|multi.rotor|x500|x-500", archive_name, re.I) else "fixedwing"


def convert_archive(info: dict, output: Path) -> dict:
    import trimesh
    candidates = [item["path"] for item in info["neutral_candidates"]]
    errors = []
    for member_name in candidates:
        try:
            with tempfile.TemporaryDirectory(prefix="aero-quark-cad-") as directory:
                path = Path(directory) / f"source{Path(member_name).suffix.lower()}"
                with zipfile.ZipFile(ARCHIVES / info["archive"]) as archive, path.open("wb") as target:
                    container, separator, nested_member = member_name.partition("::")
                    if separator:
                        with archive.open(container) as packed:
                            if container.lower().endswith(".rar"):
                                target.close()
                                rar_contents(packed.read(), nested_member, path)
                            else:
                                with zipfile.ZipFile(io.BytesIO(packed.read())) as nested, nested.open(nested_member) as source:
                                    while chunk := source.read(1024 * 1024):
                                        target.write(chunk)
                    else:
                        with archive.open(member_name) as source:
                            while chunk := source.read(1024 * 1024):
                                target.write(chunk)
                if path.open("rb").read(4).startswith(b"PK"):
                    raise ValueError("Member is a nested ZIP despite its CAD filename")
                shape, document = cad_shape(path, path.suffix)
                triangles, geometry = tessellate(shape)
            triangle_count = len(triangles)
            if triangle_count > 350_000:
                mesh = trimesh.Trimesh(vertices=triangles.reshape(-1, 3),
                                       faces=np.arange(triangle_count * 3).reshape(-1, 3), process=False)
                mesh = mesh.simplify_quadric_decimation(face_count=350_000, aggression=7)
                triangles = np.asarray(mesh.vertices[mesh.faces], dtype=np.float32)
            triangles, orientation = orient_for_preview(triangles)
            livery = import_livery_module()
            positions, normals, uv, indices = livery.geometry(triangles.reshape(-1, 3))
            glb = livery.make_glb(positions, normals, uv, indices,
                                  info["archive"], [member_name],
                                  livery.linear_color(livery.LIVERY_COLORS["airframe"]),
                                  livery_scheme(info["archive"]))
            output.mkdir(parents=True, exist_ok=True)
            output_name = f"{safe_stem(info['archive'])}.glb"
            temporary = output / (output_name + ".tmp")
            temporary.write_bytes(glb)
            os.replace(temporary, output / output_name)
            return {**info, "status": "converted", "selected_member": member_name,
                    "model_path": f"public/models/uav/cad/{output_name}", "model_bytes": len(glb),
                    "original_triangles": triangle_count, "preview_triangles": len(triangles),
                    "orientation_inference": orientation, "source_completeness": "unverified",
                    "source_surface_only": geometry["cad_solids"] == 0,
                    "source_selection": "highest ranked neutral CAD member; other members are listed but not merged",
                    **document, **geometry, "attempt_errors": errors}
        except Exception as error:
            errors.append({"member": member_name, "error": f"{type(error).__name__}: {error}"})
    return {**info, "status": "conversion_failed", "attempt_errors": errors}


def batch_convert(infos: list[dict], output: Path, report: Path, jobs: int, timeout: int) -> None:
    by_name = {item["archive"]: item for item in infos}
    if report.is_file():
        previous = json.loads(report.read_text())
        if previous.get("schema_version") != "aero-bench.cad-conversion/v1":
            raise ValueError("Invalid existing CAD conversion report")
        for old in previous["entries"]:
            current = by_name.get(old["archive"])
            model_path = old.get("model_path")
            if current and old.get("status") == "converted" and model_path \
                    and old.get("archive_bytes") == current["archive_bytes"] \
                    and (ROOT / model_path).is_file():
                by_name[old["archive"]] = old

    def run_one(item: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="aero-cad-report-") as directory:
            result_path = Path(directory) / "result.json"
            command = [sys.executable, __file__, "one", "--archive", item["archive"],
                       "--output", str(output), "--result", str(result_path)]
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                return {**item, "status": "conversion_timeout", "timeout_seconds": timeout}
            if result.returncode or not result_path.exists():
                return {**item, "status": "conversion_process_failed", "returncode": result.returncode,
                        "stderr_tail": result.stderr[-2000:]}
            return json.loads(result_path.read_text())

    pending = [item for item in by_name.values() if item["status"] == "queued"]
    write_json(report, {"schema_version": "aero-bench.cad-conversion/v1", "entries": list(by_name.values())})
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, item): item["archive"] for item in pending}
        for future in as_completed(futures):
            item = future.result()
            by_name[item["archive"]] = item
            write_json(report, {"schema_version": "aero-bench.cad-conversion/v1",
                                "entries": sorted(by_name.values(), key=lambda row: row["archive"].lower())})
            print(f"[{len(infos)-sum(value['status']=='queued' for value in by_name.values())}/{len(infos)}] "
                  f"{item['status']}: {item['archive']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "one", "batch"))
    parser.add_argument("--archive")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()
    if args.command == "one":
        if not args.archive or not args.result:
            parser.error("one requires --archive and --result")
        info = inspect_archive(ARCHIVES / args.archive)
        if info["status"] != "queued":
            raise ValueError(f"Archive is not queued for neutral CAD conversion: {args.archive}")
        write_json(args.result, convert_archive(info, args.output))
        return
    infos = [inspect_archive(path) for path in sorted(ARCHIVES.glob("*.zip"))]
    if args.command == "scan":
        write_json(args.report or SCAN_REPORT, {"schema_version": "aero-bench.cad-conversion/v1", "entries": infos})
        return
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be positive")
    batch_convert(infos, args.output, args.report or REPORT, args.jobs, args.timeout)


if __name__ == "__main__":
    main()
