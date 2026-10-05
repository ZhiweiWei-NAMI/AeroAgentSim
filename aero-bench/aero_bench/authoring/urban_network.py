"""Build a selected ground road network from sealed scene-compiler interchange.

This is a static authoring artifact. It does not claim a SUMO traffic run.
"""

from __future__ import annotations

import hashlib
import json
import re
import runpy
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from aero_bench.authoring.selection import SceneSelection
from aero_bench.authoring.publication_evidence import verify_ground_network_closure
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.scene_compiler import _sumo_projection_document


_SCRIPT = Path(__file__).resolve().parents[2] / "frontend/scripts/build-urban-ground-network.py"
_FILTER_SCRIPT = _SCRIPT.with_name("build-ground-city-network.py")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class SelectedUrbanNetwork:
    network_path: Path
    engineering_inputs_path: Path
    ground_filter_report_path: Path
    source_osm_path: Path
    network_sha256: str
    source_osm_sha256: str
    projection: str
    sumo_image_id: str
    receipt_path: Path


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict:
    document = json.loads(path.read_bytes())
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _checked_output(directory: Path, reference: dict) -> Path:
    name = reference.get("path")
    size = reference.get("byte_size")
    digest = reference.get("sha256")
    if (not isinstance(name, str) or not name or name.startswith("/")
            or "\\" in name or any(part in {"", ".", ".."} for part in name.split("/"))
            or type(size) is not int or size < 1
            or not isinstance(digest, str) or not _HEX.fullmatch(digest)):
        raise ValueError("Invalid compiler output reference")
    path = directory / name
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Compiler output missing or linked: {name}")
    raw = path.read_bytes()
    if len(raw) != size or _sha(raw) != digest:
        raise ValueError(f"Compiler output digest mismatch: {name}")
    return path


def _verified_input(interchange: Path) -> tuple[Path, str, str, str]:
    if not interchange.is_dir() or interchange.is_symlink():
        raise ValueError("Compiler interchange directory is missing or linked")
    manifest_path = interchange / "manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = _read_json(manifest_path)
    receipt = _read_json(interchange / "authoring-receipt.json")
    selection = SceneSelection.from_json_bytes((interchange / "authoring-selection.json").read_bytes())
    source_record = manifest.get("source")
    crop_record = manifest.get("crop")
    origin = manifest.get("origin")
    if not isinstance(source_record, dict) or not isinstance(crop_record, dict) or not isinstance(origin, dict):
        raise ValueError("Compiler manifest source, crop or origin is malformed")
    if (manifest.get("schema_version") != "aero-bench.urban-scene-compiler/v1"
            or receipt.get("schema_version") != "aero-bench.authoring-scene-interchange/v1"
            or receipt.get("state") != "compiled_interchange_only"
            or receipt.get("compiler_manifest") != {
                "path": "manifest.json", "sha256": _sha(manifest_raw), "size_bytes": len(manifest_raw)}
            or receipt.get("selection_sha256") != selection.sha256
            or receipt.get("source_id") != selection.source_id
            or receipt.get("source_sha256") != selection.source_sha256
            or source_record.get("sha256") != selection.source_sha256
            or crop_record.get("enu_bounds_m") != selection.to_document()["bounds_enu_m"]):
        raise ValueError("Compiler manifest, selection and authoring receipt disagree")
    if any(origin.get(key) != value for key, value in selection.to_document()["origin"].items()):
        raise ValueError("Compiler origin differs from selected origin")
    if manifest.get("netconvert") != {
            "generated": False, "required_version": "1.27.1",
            "command_record": "sumo/netconvert-command.json"}:
        raise ValueError("Compiler netconvert state is not the supported ungenerated interchange")
    refs = manifest.get("outputs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Compiler output list is missing")
    indexed: dict[str, Path] = {}
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("Compiler output reference is invalid")
        path = _checked_output(interchange, ref)
        name = ref["path"]
        if name in indexed:
            raise ValueError(f"Duplicate compiler output: {name}")
        indexed[name] = path
    source = indexed.get("osm/sumo-network.osm")
    record = indexed.get("sumo/netconvert-command.json")
    if source is None or record is None:
        raise ValueError("Compiler omitted SUMO OSM or command record")
    command = _read_json(record)
    projection_record = command.get("projection")
    if not isinstance(projection_record, dict):
        raise ValueError("Compiler projection record is malformed")
    projection = projection_record.get("projection")
    expected_projection = _sumo_projection_document(selection.origin)["projection"]
    expected_command = [
        "netconvert", "--osm-files", "osm/sumo-network.osm",
        "--output-file", "sumo/network.net.xml", "--proj", expected_projection,
        "--offset.disable-normalization",
    ]
    if (command.get("schema_version") != "aero-bench.urban-scene-netconvert/v1"
            or command.get("status") != "not_invoked"
            or command.get("required_netconvert_version") != "1.27.1"
            or command.get("input") != "osm/sumo-network.osm"
            or command.get("output") != "sumo/network.net.xml"
            or command.get("command") != expected_command
            or projection != expected_projection
            or projection_record.get("normalization") != "disabled"):
        raise ValueError("Compiler netconvert command or projection drifted")
    if (interchange / "authoring-selection.json").read_bytes() != canonical_json_bytes(selection.to_document()):
        raise ValueError("Compiler selection is not canonical")
    return source, _sha(source.read_bytes()), projection, _sha(manifest_raw)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ref(path: Path) -> dict:
    raw = path.read_bytes()
    return {"sha256": _sha(raw), "size_bytes": len(raw)}


def build_selected_urban_network(interchange_dir: Path, output_dir: Path) -> SelectedUrbanNetwork:
    """Build an initial SUMO network, then current ground-only urban design.

    The caller owns a private staging directory. Outputs are never sourced from
    another city's reference network, and existing outputs are not overwritten.
    """
    interchange = Path(interchange_dir).resolve()
    output = Path(output_dir).resolve()
    source, source_sha, projection, manifest_sha = _verified_input(interchange)
    if output.exists():
        raise FileExistsError(f"Selected network output already exists: {output}")
    urban = runpy.run_path(str(_SCRIPT))
    ground_filter = runpy.run_path(str(_FILTER_SCRIPT))
    if urban["SUMO_IMAGE"] != ground_filter["SUMO_IMAGE"]:
        raise ValueError("Initial and urban ground builders use different SUMO images")
    image = urban["SUMO_IMAGE"]
    if not image.startswith("sha256:") or not _HEX.fullmatch(image.removeprefix("sha256:")):
        raise ValueError("SUMO image must be pinned by digest")
    output.mkdir(parents=True)
    source_dir = output / "source"
    source_dir.mkdir()
    portable_source = source_dir / "sumo-network.osm"
    raw_source = source.read_bytes()
    if _sha(raw_source) != source_sha:
        raise ValueError("Compiler SUMO OSM changed after interchange verification")
    portable_source.write_bytes(raw_source)
    initial_dir = output / "initial"
    initial_dir.mkdir()
    initial = initial_dir / "network.net.xml"
    version_command = [*ground_filter["_docker_base"](), "--entrypoint", "netconvert", image, "--version"]
    try:
        version_result = subprocess.run(version_command, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        _write_json(initial_dir / "netconvert-version.json", {
            "command": version_command, "image": image, "status": "launch_failed",
            "error": str(exc), "host_path_scope": "build_time_only",
        })
        raise
    (initial_dir / "netconvert-version.stdout.log").write_text(version_result.stdout, encoding="utf-8")
    (initial_dir / "netconvert-version.stderr.log").write_text(version_result.stderr, encoding="utf-8")
    version_text = version_result.stdout + version_result.stderr
    match = re.search(r"\bnetconvert\s+(?:Version\s+)?(\d+\.\d+\.\d+)\b", version_text)
    version = match.group(1) if match else None
    _write_json(initial_dir / "netconvert-version.json", {
        "command": version_command, "image": image, "version": version,
        "exit_code": version_result.returncode, "host_path_scope": "build_time_only",
    })
    if version_result.returncode or version != ground_filter["SUMO_VERSION"]:
        raise ValueError(f"Pinned netconvert image did not report {ground_filter['SUMO_VERSION']}: {version_text.strip()}")
    command = [
        *ground_filter["_docker_base"](),
        "--mount", f"type=bind,source={portable_source},target=/input/sumo-network.osm,readonly",
        "--mount", f"type=bind,source={initial_dir},target=/output",
        "--entrypoint", "netconvert", image,
        "--osm-files", "/input/sumo-network.osm", "--output-file", "/output/network.net.xml",
        "--proj", projection, "--offset.disable-normalization",
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (initial_dir / "netconvert.stdout.log").write_text(result.stdout, encoding="utf-8")
    (initial_dir / "netconvert.stderr.log").write_text(result.stderr, encoding="utf-8")
    _write_json(initial_dir / "netconvert-command.json", {
        "command": command, "version": version, "image": image,
        "exit_code": result.returncode, "source_osm_sha256": source_sha,
        "host_path_scope": "build_time_only",
        "compiler_manifest_sha256": manifest_sha,
    })
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    initial_root = ET.parse(initial).getroot()
    location = initial_root.find("location")
    if (location is None or location.get("projParameter") != projection
            or location.get("netOffset") != "0.00,0.00"):
        raise ValueError("Initial SUMO network changed the compiler projection or offset")
    initial_net_offset = location.get("netOffset")
    if not any(edge.get("function", "normal") == "normal" for edge in initial_root.findall("edge")):
        raise ValueError("Initial SUMO network has no normal road edges")
    urban_dir = output / "urban"
    urban["build"](portable_source, initial, urban_dir)
    final_network = urban_dir / "network.net.xml"
    engineering_path = urban_dir / "engineering-inputs.json"
    report_path = urban_dir / "ground-filter-report.json"
    engineering = _read_json(engineering_path)
    report = _read_json(report_path)
    final_location = ET.parse(final_network).getroot().find("location")
    if (final_location is None or final_location.get("netOffset") != initial_net_offset):
        raise ValueError("Urban SUMO network changed or omitted the selected net offset")
    actual_net_offset = final_location.get("netOffset")
    if actual_net_offset is None:
        raise ValueError("Urban SUMO network omitted its selected net offset")
    required_net_offset = ",".join(format(float(value), "g")
                                   for value in actual_net_offset.split(","))
    engineering["source_osm"] = "../source/sumo-network.osm"
    engineering["source_osm_relative_to"] = "engineering-inputs.json directory"
    engineering["net_offset"] = actual_net_offset
    engineering["net_offset_required"] = required_net_offset
    _write_json(engineering_path, engineering)
    if (engineering.get("source_osm_sha256") != source_sha
            or engineering.get("network_sha256") != _ref(final_network)["sha256"]
            or engineering.get("projection") != projection
            or engineering.get("sumo_image_id") != image
            or report.get("source_osm_sha256") != source_sha
            or report.get("reference_network_sha256") != _ref(initial)["sha256"]
            or report.get("network_sha256") != _ref(final_network)["sha256"]
            or _sha(portable_source.read_bytes()) != source_sha
            or _sha(source.read_bytes()) != source_sha):
        raise ValueError("Urban network source, projection or output evidence drifted")
    receipt_path = output / "selected-network-receipt.json"
    closure_reference = {
        "path": "urban/ground-network-proof.json", **_ref(urban_dir / "ground-network-proof.json")}
    report_reference = {"path": "urban/ground-filter-report.json", **_ref(report_path)}
    verify_ground_network_closure(
        output, network_closure=closure_reference, ground_filter_report=report_reference,
        network_sha256=_ref(final_network)["sha256"],
    )
    _write_json(receipt_path, {
        "schema_version": "aero-bench.selected-urban-network/v2",
        "compiler_manifest": {"sha256": manifest_sha},
        "source_osm": {"path": "source/sumo-network.osm", **_ref(portable_source)},
        "initial_network": {"path": "initial/network.net.xml", **_ref(initial)},
        "network": {"path": "urban/network.net.xml", **_ref(final_network)},
        "network_closure": closure_reference,
        "engineering_inputs": {"path": "urban/engineering-inputs.json", **_ref(engineering_path)},
        "ground_filter_report": report_reference,
        "initial_netconvert": {"path": "initial/netconvert-command.json", **_ref(initial_dir / "netconvert-command.json")},
        "initial_version_check": {"path": "initial/netconvert-version.json", **_ref(initial_dir / "netconvert-version.json")},
        "initial_version_stdout": {"path": "initial/netconvert-version.stdout.log", **_ref(initial_dir / "netconvert-version.stdout.log")},
        "initial_version_stderr": {"path": "initial/netconvert-version.stderr.log", **_ref(initial_dir / "netconvert-version.stderr.log")},
        "initial_stdout": {"path": "initial/netconvert.stdout.log", **_ref(initial_dir / "netconvert.stdout.log")},
        "initial_stderr": {"path": "initial/netconvert.stderr.log", **_ref(initial_dir / "netconvert.stderr.log")},
        "urban_netconvert": {"path": "urban/netconvert-command.json", **_ref(urban_dir / "netconvert-command.json")},
        "urban_stdout": {"path": "urban/netconvert.stdout.log", **_ref(urban_dir / "netconvert.stdout.log")},
        "urban_stderr": {"path": "urban/netconvert.stderr.log", **_ref(urban_dir / "netconvert.stderr.log")},
        "projection": projection, "sumo_image_id": image, "netconvert_version": version,
    })
    return SelectedUrbanNetwork(
        final_network, engineering_path, report_path, portable_source,
        _ref(final_network)["sha256"], source_sha, projection, image, receipt_path)
