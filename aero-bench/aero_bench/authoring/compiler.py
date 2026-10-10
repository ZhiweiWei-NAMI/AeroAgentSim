"""Publish a selected offline OSM interchange package for authoring."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import ctypes
from errno import EEXIST, ENOTEMPTY
from dataclasses import dataclass
from pathlib import Path

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.scene_compiler import compile_urban_scene

from .selection import (
    SceneSelection,
    SceneSelectionError,
    SceneSourceRegistry,
    default_scene_source_registry,
)


COMPILED_INTERCHANGE_ONLY = "compiled_interchange_only"


def _publish_directory_no_replace(staged: Path, final: Path) -> None:
    """Atomically publish on Linux only if no directory already has this ID."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    if renameat2(at_fdcwd, os.fsencode(staged), at_fdcwd, os.fsencode(final), rename_noreplace) == 0:
        return
    error = ctypes.get_errno()
    if error in (EEXIST, ENOTEMPTY):
        raise SceneSelectionError(f"selection output already exists: {final.name}")
    raise OSError(error, os.strerror(error), str(final))


@dataclass(frozen=True, slots=True)
class CompiledSceneSelection:
    state: str
    output_dir: Path
    selection_sha256: str
    source_id: str
    source_sha256: str
    origin: dict[str, float]
    bounds_enu_m: dict[str, float]
    manifest_path: Path
    manifest_sha256: str
    effective_osm_path: Path
    effective_osm_sha256: str
    building_count: int
    road_count: int


def compile_scene_selection(
    selection: SceneSelection | dict[str, object],
    *,
    output_root: Path,
    registry: SceneSourceRegistry | None = None,
) -> CompiledSceneSelection:
    """Compile one verified selection and atomically publish its interchange.

    This emits no viewer mesh and does not run SUMO netconvert. The caller owns
    ``output_root``; the client supplies only the strict SceneSelection fields.
    """

    if isinstance(selection, dict):
        selection = SceneSelection.from_document(selection)
    if not isinstance(selection, SceneSelection):
        raise SceneSelectionError("selection must be a SceneSelection document")
    verified = (registry or default_scene_source_registry()).verify(selection)

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / selection.sha256
    if final.exists():
        raise SceneSelectionError(f"selection output already exists: {selection.sha256}")

    staging_parent = Path(tempfile.mkdtemp(prefix=f".{selection.sha256[:12]}-", dir=root))
    staged = staging_parent / "interchange"
    try:
        compiled = compile_urban_scene(
            source_path=verified.registration.path,
            output_dir=staged,
            origin=selection.origin,
            bounds=selection.bounds_enu_m,
            run_netconvert=False,
        )
        if compiled.source_sha256 != selection.source_sha256:
            raise SceneSelectionError("registered source changed during scene compilation")

        manifest_path = staged / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest["source"]["sha256"] != selection.source_sha256:
            raise SceneSelectionError("compiler manifest source digest differs from selection")
        if manifest["crop"]["enu_bounds_m"] != selection.bounds_enu_m.manifest_document():
            raise SceneSelectionError("compiler manifest ENU crop differs from selection")
        for key, value in selection.to_document()["origin"].items():
            if manifest["origin"][key] != value:
                raise SceneSelectionError("compiler manifest origin differs from selection")
        if manifest["renderer_policy"]["custom_viewer_mesh_emitted"] is not False:
            raise SceneSelectionError("compiler interchange unexpectedly claims viewer mesh")
        if manifest["netconvert"]["generated"] is not False:
            raise SceneSelectionError("compiler interchange unexpectedly claims a SUMO network")

        effective = staged / "osm/effective.osm.json"
        effective_bytes = effective.read_bytes()
        effective_sha256 = hashlib.sha256(effective_bytes).hexdigest()
        outputs = {item["path"]: item for item in manifest["outputs"]}
        if outputs["osm/effective.osm.json"]["sha256"] != effective_sha256:
            raise SceneSelectionError("effective OSM digest differs from compiler manifest")
        if outputs["osm/effective.osm.json"]["byte_size"] != len(effective_bytes):
            raise SceneSelectionError("effective OSM size differs from compiler manifest")

        selection_bytes = canonical_json_bytes(selection.to_document())
        if hashlib.sha256(selection_bytes).hexdigest() != selection.sha256:
            raise SceneSelectionError("selection digest changed before publication")
        (staged / "authoring-selection.json").write_bytes(selection_bytes)
        (staged / "authoring-receipt.json").write_bytes(canonical_json_bytes({
            "schema_version": "aero-bench.authoring-scene-interchange/v1",
            "state": COMPILED_INTERCHANGE_ONLY,
            "selection_sha256": selection.sha256,
            "source_id": selection.source_id,
            "source_sha256": selection.source_sha256,
            "source_byte_size": verified.byte_size,
            "origin": selection.to_document()["origin"],
            "bounds_enu_m": selection.bounds_enu_m.manifest_document(),
            "compiler_manifest": {
                "path": "manifest.json",
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "size_bytes": len(manifest_bytes),
            },
            "effective_osm": {
                "path": "osm/effective.osm.json",
                "sha256": effective_sha256,
                "size_bytes": len(effective_bytes),
            },
        }))

        # The complete package stays hidden until all hashes and provenance pass.
        if final.exists():
            raise SceneSelectionError(f"selection output already exists: {selection.sha256}")
        _publish_directory_no_replace(staged, final)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)

    return CompiledSceneSelection(
        state=COMPILED_INTERCHANGE_ONLY,
        output_dir=final,
        selection_sha256=selection.sha256,
        source_id=selection.source_id,
        source_sha256=selection.source_sha256,
        origin=selection.to_document()["origin"],
        bounds_enu_m=selection.bounds_enu_m.manifest_document(),
        manifest_path=final / "manifest.json",
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        effective_osm_path=final / "osm/effective.osm.json",
        effective_osm_sha256=effective_sha256,
        building_count=compiled.building_count,
        road_count=compiled.road_count,
    )
