"""Source binding tests with unit-only manifests, not toolchain attestations."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import AssetAudience as TaskAssetAudience, AssetRef
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.provenance import URBAN_PROVENANCE_ASSETS, validate_urban_provenance
from aero_bench.world.contracts import AssetAudience
from tools.build_urban_recovery_demo import _asset, _validate_sumo_inputs, artifact_selector

VERIFIER = "urban.recovery.verifier"


def _provenance(root):
    license_path = root / "unit-license.txt"
    license_path.write_text("unit fixture; not an asset license for delivery")
    license_ref = artifact_selector(root, "asset.license", license_path)
    paths = dict(URBAN_PROVENANCE_ASSETS)
    paths.update({
        "asset.osm-source": "world/source.json", "asset.osm-scene": "world/effective.json",
        "asset.gazebo-scene": "world/scene.sdf", "asset.osm-mesh": "world/mesh.json",
    })
    assets = {}

    def put(asset_id, document):
        relative = paths[asset_id]
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(document) + b"\n")
        if asset_id in dict(URBAN_PROVENANCE_ASSETS):
            import hashlib
            assets[asset_id] = AssetRef(
                asset_id=asset_id, file={"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                classification="private", audiences=(TaskAssetAudience(role="verifier", workload_ids=(VERIFIER,)),),
            )
        else:
            assets[asset_id] = _asset(
                root, asset_id=asset_id, relative=relative, role="other", media_type="application/json",
                visibility="private", frame="non_spatial",
                audiences=(AssetAudience(audience_kind="verifier", audience_id=VERIFIER),), license_ref=license_ref,
            )

    for asset_id in ("asset.osm-source", "asset.osm-scene", "asset.gazebo-scene", "asset.osm-mesh"):
        put(asset_id, {"unit_only": asset_id})
    put("asset.scene-object-provenance", {"schema_version": "aero-bench.urban-scene-objects/v1", "coordinate_frame": "ENU"})
    put("asset.scene-compiler-provenance", {
        "schema_version": "aero-bench.urban-scene-compiler/v1",
        "source": {"sha256": assets["asset.osm-source"].artifact.sha256},
        "outputs": [{"path": relative, "sha256": (assets[asset_id].file.sha256 if isinstance(assets[asset_id], AssetRef) else assets[asset_id].artifact.sha256), "byte_size": (root / paths[asset_id]).stat().st_size} for relative, asset_id in (
            ("gazebo/scene.sdf", "asset.gazebo-scene"), ("metadata/objects.json", "asset.scene-object-provenance"),
            ("osm/effective.osm.json", "asset.osm-scene"),
        )],
    })
    put("asset.osm-mesh-lock", {"sha256": assets["asset.osm-mesh"].artifact.sha256, "size_bytes": assets["asset.osm-mesh"].byte_size})
    put("asset.sumo-toolchain", {"schema_version": "aero-bench.sumo-toolchain/v1", "sumo_version": "1.27.1", "netconvert_version": "1.27.1"})
    return assets, paths, put


def _validate(root, assets):
    task = SimpleNamespace(
        assets=tuple(asset for asset in assets.values() if isinstance(asset, AssetRef)),
        verifier=SimpleNamespace(verifier_id=VERIFIER),
    )
    world = SimpleNamespace(assets=tuple(asset for asset in assets.values() if not isinstance(asset, AssetRef)))
    validate_urban_provenance(reader=BundleReader(root), world=world, task=task)


def test_source_manifests_are_bound_as_private_verifier_inputs(tmp_path):
    assets, _, _ = _provenance(tmp_path)
    _validate(tmp_path, assets)
    for asset_id, _ in URBAN_PROVENANCE_ASSETS:
        assert assets[asset_id].classification == "private"
        assert assets[asset_id].audiences == (TaskAssetAudience(role="verifier", workload_ids=(VERIFIER,)),)


@pytest.mark.parametrize("asset_id", [asset_id for asset_id, _ in URBAN_PROVENANCE_ASSETS])
def test_provenance_cannot_be_omitted_or_changed_without_new_input_digest(tmp_path, asset_id):
    assets, paths, _ = _provenance(tmp_path)
    missing = dict(assets)
    missing.pop(asset_id)
    with pytest.raises(ValueError, match="verifier-only"):
        _validate(tmp_path, missing)
    (tmp_path / paths[asset_id]).write_bytes(b'{"replacement":true}\n')
    with pytest.raises(ValueError):
        _validate(tmp_path, assets)


@pytest.mark.parametrize("mutation", ["source", "output-digest", "output-size", "duplicate-output", "mesh-lock", "old-sumo"])
def test_rehashed_provenance_still_must_bind_its_sources_and_outputs(tmp_path, mutation):
    assets, paths, put = _provenance(tmp_path)
    asset_id = "asset.osm-mesh-lock" if mutation == "mesh-lock" else "asset.sumo-toolchain" if mutation == "old-sumo" else "asset.scene-compiler-provenance"
    document = json.loads((tmp_path / paths[asset_id]).read_bytes())
    if mutation == "source":
        document["source"]["sha256"] = "f" * 64
    elif mutation == "output-digest":
        document["outputs"][0]["sha256"] = "f" * 64
    elif mutation == "output-size":
        document["outputs"][0]["byte_size"] += 1
    elif mutation == "duplicate-output":
        document["outputs"].append(document["outputs"][0])
    elif mutation == "mesh-lock":
        document["sha256"] = "f" * 64
    else:
        document["netconvert_version"] = "1.15.0"
    put(asset_id, document)
    with pytest.raises(ValueError):
        _validate(tmp_path, assets)


def test_private_provenance_cannot_become_public_inputs(tmp_path):
    assets, _, _ = _provenance(tmp_path)
    asset_id = "asset.sumo-toolchain"
    assets[asset_id] = assets[asset_id].model_copy(update={"classification": "public", "audiences": (TaskAssetAudience(role="agent", workload_ids=("uav.policy.01",)),)})
    with pytest.raises(ValueError, match="verifier-only"):
        _validate(tmp_path, assets)


def _sumo_xml(root):
    (root / "toolchain.json").write_bytes(canonical_json_bytes({"schema_version": "aero-bench.sumo-toolchain/v1", "sumo_version": "1.27.1", "netconvert_version": "1.27.1"}))
    (root / "network.net.xml").write_text('<net><edge id="unit-only" /></net>')
    (root / "routes.rou.xml").write_text('<routes />')
    (root / "additional.add.xml").write_text('<additional />')
    config = '<configuration><input><net-file value="network.net.xml"/><route-files value="routes.rou.xml"/><additional-files value="additional.add.xml"/></input></configuration>'
    (root / "simulation.sumocfg").write_text(config)
    return config


def test_sumo_xml_inputs_reference_exactly_the_declared_bundle_files(tmp_path):
    _sumo_xml(tmp_path)
    _validate_sumo_inputs(tmp_path)


@pytest.mark.parametrize("replacement", ["/private/other.net.xml", "../other.net.xml", "other.net.xml", "network.net.xml,other.net.xml", "network.net.xml?download=true"])
def test_sumo_config_cannot_redirect_to_undeclared_input_bytes(tmp_path, replacement):
    config = _sumo_xml(tmp_path)
    (tmp_path / "simulation.sumocfg").write_text(config.replace('value="network.net.xml"', f'value="{replacement}"'))
    with pytest.raises(ValueError, match="references differ"):
        _validate_sumo_inputs(tmp_path)


@pytest.mark.parametrize("replacement", [
    '<input><net-file value="network.net.xml"/><net-file value="network.net.xml"/><route-files value="routes.rou.xml"/></input>',
    '<input><net-file value="network.net.xml"/><route-files value="routes.rou.xml"/><weights value="elsewhere.xml"/></input>',
    '<input/><input/>',
])
def test_sumo_config_rejects_duplicate_missing_and_extra_input_roles(tmp_path, replacement):
    _sumo_xml(tmp_path)
    (tmp_path / "simulation.sumocfg").write_text(f"<configuration>{replacement}</configuration>")
    with pytest.raises(ValueError):
        _validate_sumo_inputs(tmp_path)


@pytest.mark.parametrize("payload", [
    b'<!DOCTYPE net [<!ENTITY injected "unit-only">]><net><edge id="&injected;"/></net>',
    '<!DOCTYPE net><net><edge id="unit-only"/></net>'.encode("utf-16"),
    b'<net><include href="/private/other.xml"/><edge id="unit-only"/></net>',
    b'<net xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="other.xml"/><edge id="unit-only"/></net>',
    b'<routes><edge id="unit-only"/></routes>',
    b'<net><edge',
])
def test_sumo_xml_cannot_expand_undeclared_bytes_or_use_wrong_roots(tmp_path, payload):
    _sumo_xml(tmp_path)
    (tmp_path / "network.net.xml").write_bytes(payload)
    with pytest.raises(ValueError):
        _validate_sumo_inputs(tmp_path)
