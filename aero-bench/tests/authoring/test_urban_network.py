from __future__ import annotations

import hashlib
import json
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aero_bench.authoring import SceneSelection, compile_scene_selection
from aero_bench.authoring import urban_network
from aero_bench.authoring.publication_evidence import verify_ground_network_closure
from aero_bench.authoring.urban_network import _verified_input, build_selected_urban_network


@pytest.fixture(scope="module")
def interchange(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("selected-urban-network")
    selection = SceneSelection.from_document({
        "schema_version": "aero-bench.scene-selection/v1",
        "source_id": "shanghai-central-osm-v1",
        "source_sha256": "d0f3f30f846e8145aecd497e20ee95bc58e5cb0b3d4b4f46d1339e285217b847",
        "origin": {
            "latitude_deg": 31.2304, "longitude_deg": 121.4737,
            "ellipsoid_height_m": 50.0, "geoid_undulation_m": 30.0, "amsl_m": 20.0,
        },
        "bounds_enu_m": {
            "min_east_m": -300.0, "max_east_m": 300.0,
            "min_north_m": -300.0, "max_north_m": 300.0,
        },
    })
    return compile_scene_selection(selection, output_root=root).output_dir


def test_network_input_is_bound_to_compiler_receipt_and_aeqd_command(interchange: Path) -> None:
    source, source_sha, projection, manifest_sha = _verified_input(interchange)
    assert source == interchange / "osm/sumo-network.osm"
    assert source_sha == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest_sha == hashlib.sha256((interchange / "manifest.json").read_bytes()).hexdigest()
    assert projection == "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"


def test_modified_sumo_osm_fails_before_docker(interchange: Path, tmp_path: Path) -> None:
    copied = tmp_path / "interchange"
    shutil.copytree(interchange, copied)
    with (copied / "osm/sumo-network.osm").open("ab") as output:
        output.write(b"\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        build_selected_urban_network(copied, tmp_path / "network")
    assert not (tmp_path / "network").exists()


def test_rewritten_declared_command_fails_even_with_updated_hashes(interchange: Path, tmp_path: Path) -> None:
    copied = tmp_path / "interchange"
    shutil.copytree(interchange, copied)
    record_path = copied / "sumo/netconvert-command.json"
    record = json.loads(record_path.read_bytes())
    record["command"][-1] = "--offset.normalize"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    reference = next(item for item in manifest["outputs"] if item["path"] == "sumo/netconvert-command.json")
    reference["sha256"] = hashlib.sha256(record_path.read_bytes()).hexdigest()
    reference["byte_size"] = record_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path = copied / "authoring-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["compiler_manifest"]["sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    receipt["compiler_manifest"]["size_bytes"] = manifest_path.stat().st_size
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="command or projection drifted"):
        build_selected_urban_network(copied, tmp_path / "network")


def test_missing_docker_records_failed_version_launch(interchange: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_docker(*args, **kwargs):
        raise FileNotFoundError("docker unavailable")

    monkeypatch.setattr(urban_network.subprocess, "run", missing_docker)
    output = tmp_path / "network"
    with pytest.raises(FileNotFoundError, match="docker unavailable"):
        build_selected_urban_network(interchange, output)
    failure = json.loads((output / "initial/netconvert-version.json").read_bytes())
    assert failure["status"] == "launch_failed"
    assert failure["command"][0] == "docker"
    assert not (output / "selected-network-receipt.json").exists()


@pytest.mark.skipif(os.getenv("AERO_TEST_SELECTED_NETWORK_DOCKER") != "1",
                    reason="opt in to the digest-pinned SUMO Docker integration test")
def test_real_selected_network_uses_current_osm_and_has_ground_roads(interchange: Path, tmp_path: Path) -> None:
    result = build_selected_urban_network(interchange, tmp_path / "network")
    report = json.loads(result.ground_filter_report_path.read_bytes())
    receipt = json.loads(result.receipt_path.read_bytes())
    network = ET.parse(result.network_path).getroot()
    assert result.source_osm_path == tmp_path / "network/source/sumo-network.osm"
    assert result.source_osm_sha256 == report["source_osm_sha256"]
    assert receipt["schema_version"] == "aero-bench.selected-urban-network/v2"
    proof_path = result.receipt_path.parent / receipt["network_closure"]["path"]
    proof_raw = proof_path.read_bytes()
    proof = json.loads(proof_raw)
    assert receipt["network_closure"] == {
        "path": "urban/ground-network-proof.json",
        "sha256": hashlib.sha256(proof_raw).hexdigest(), "size_bytes": len(proof_raw),
    }
    assert proof["network_sha256"] == result.network_sha256
    assert proof["filtered_osm_sha256"] == report["filtered_osm_sha256"]
    assert result.network_sha256 == report["network_sha256"] == receipt["network"]["sha256"]
    assert report["network_after"]["normal_edges"] > 0
    assert len(network.findall("connection")) > 0
    assert receipt["initial_network"]["sha256"] == report["reference_network_sha256"]
    assert receipt["initial_stderr"]["size_bytes"] >= 0
    assert receipt["sumo_image_id"].startswith("sha256:")
    moved = tmp_path / "published"
    shutil.move(str(tmp_path / "network"), moved)
    portable_source = moved / receipt["source_osm"]["path"]
    assert portable_source.is_file()
    assert hashlib.sha256(portable_source.read_bytes()).hexdigest() == receipt["source_osm"]["sha256"]
    engineering_path = moved / "urban/engineering-inputs.json"
    engineering = json.loads(engineering_path.read_bytes())
    assert engineering["source_osm_relative_to"] == "engineering-inputs.json directory"
    assert (engineering_path.parent / engineering["source_osm"]).resolve() == portable_source
    location = ET.parse(moved / receipt["network"]["path"]).getroot().find("location")
    assert location is not None
    assert engineering["net_offset"] == location.get("netOffset")
    assert tuple(map(float, engineering["net_offset_required"].split(","))) == tuple(
        map(float, location.get("netOffset", "").split(","))
    )
    assert hashlib.sha256((moved / receipt["network"]["path"]).read_bytes()).hexdigest() == result.network_sha256
    verify_ground_network_closure(
        moved, network_closure=receipt["network_closure"],
        ground_filter_report=receipt["ground_filter_report"],
        network_sha256=result.network_sha256,
    )
