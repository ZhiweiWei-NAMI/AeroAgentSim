"""Run-bound source manifests; byte closure is not tool execution attestation."""
from __future__ import annotations

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import AssetAudience, TaskSpec
from aero_bench.world.contracts import WorldPackage

URBAN_PROVENANCE_ASSETS = (
    ("asset.scene-compiler-provenance", "world/provenance/scene-compiler.json"),
    ("asset.scene-object-provenance", "world/provenance/scene-objects.json"),
    ("asset.sumo-toolchain", "world/provenance/sumo-toolchain.json"),
    ("asset.osm-mesh-lock", "world/osm2world/manifest.lock.json"),
)


def validate_urban_provenance(*, reader: BundleReader, world: WorldPackage, task: TaskSpec) -> None:
    assets = {asset.artifact.artifact_id: asset for asset in world.assets}
    inputs = {asset.asset_id: asset for asset in task.assets}
    if not {"asset.osm-source", "asset.gazebo-scene", "asset.osm-scene", "asset.osm-mesh"} <= set(assets):
        raise ValueError("urban provenance lacks its declared source/output assets")
    documents = {}
    sizes = {}
    for asset_id, relative in URBAN_PROVENANCE_ASSETS:
        asset = inputs.get(asset_id)
        if asset is None or (
            asset.file.path != relative or asset.classification != "private"
            or asset.audiences != (AssetAudience(role="verifier", workload_ids=(task.verifier.verifier_id,)),)
        ):
            raise ValueError("urban provenance must be explicitly bound to verifier-only TaskSpec assets")
        path = reader.resolve_file(asset.file)
        sizes[asset_id] = path.stat().st_size
        document = reader.load_document(asset.file)
        if not isinstance(document, dict):
            raise ValueError("urban provenance must be a JSON object")
        documents[asset_id] = document

    scene = documents["asset.scene-compiler-provenance"]
    if scene.get("schema_version") != "aero-bench.urban-scene-compiler/v1":
        raise ValueError("urban scene compiler provenance schema is unsupported")
    source = scene.get("source")
    if not isinstance(source, dict) or source.get("sha256") != assets["asset.osm-source"].artifact.sha256:
        raise ValueError("urban scene provenance does not bind the declared raw Shanghai OSM")
    outputs = scene.get("outputs")
    if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
        raise ValueError("urban scene provenance lacks its output inventory")
    by_path = {item.get("path"): item for item in outputs}
    if len(by_path) != len(outputs):
        raise ValueError("urban scene provenance repeats an output path")
    expected = {
        "gazebo/scene.sdf": (assets["asset.gazebo-scene"].artifact.sha256, assets["asset.gazebo-scene"].byte_size),
        "osm/effective.osm.json": (assets["asset.osm-scene"].artifact.sha256, assets["asset.osm-scene"].byte_size),
        "metadata/objects.json": (inputs["asset.scene-object-provenance"].file.sha256, sizes["asset.scene-object-provenance"]),
    }
    for relative, (digest, size) in expected.items():
        output = by_path.get(relative)
        if output is None or output.get("sha256") != digest or output.get("byte_size") != size:
            raise ValueError("urban scene provenance output differs from its declared asset bytes")
    objects = documents["asset.scene-object-provenance"]
    if objects.get("schema_version") != "aero-bench.urban-scene-objects/v1" or objects.get("coordinate_frame") != "ENU":
        raise ValueError("urban scene object provenance is not the declared ENU inventory")
    mesh = assets["asset.osm-mesh"]
    lock = documents["asset.osm-mesh-lock"]
    if lock.get("sha256") != mesh.artifact.sha256 or lock.get("size_bytes") != mesh.byte_size:
        raise ValueError("urban OSM2World lock differs from the declared manifest bytes")
    toolchain = documents["asset.sumo-toolchain"]
    if (
        toolchain.get("schema_version") != "aero-bench.sumo-toolchain/v1"
        or toolchain.get("sumo_version") != "1.27.1"
        or toolchain.get("netconvert_version") != "1.27.1"
    ):
        raise ValueError("urban SUMO toolchain declarations differ from the required versions")
