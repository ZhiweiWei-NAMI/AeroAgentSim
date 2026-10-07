"""Extract the pinned native pose convention without starting a simulation.

Only model/frame/contact facts and one already-recorded touchdown sample are
written. No model, city asset, credential or full trajectory is exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

FLIGHT_IMAGE = (
    "localhost:5000/aero-bench/flight@sha256:"
    "661dc7420eb130147ff422349e2bf097bd951c89f37a2f02e6f7aec088018abc"
)

EXTRACT = r'''
import hashlib, json, pathlib, subprocess, xml.etree.ElementTree as E
root = pathlib.Path('/opt/px4-gazebo/share/gz/models')
sources = []
for name in ('x500_base', 'x500', 'x500_mono_cam', 'mono_cam'):
    p = root / name / 'model.sdf'; b = p.read_bytes()
    model = E.fromstring(b).find('model')
    sources.append({'path': str(p), 'sha256': hashlib.sha256(b).hexdigest(),
                    'bytes': len(b), 'model_name': model.attrib['name'],
                    'model_pose': model.findtext('pose'),
                    'includes': [E.tostring(x, encoding='unicode') for x in model.findall('include')]})
result = subprocess.run(['gz', 'sdf', '-p', str(root/'x500_mono_cam'/'model.sdf')],
                        capture_output=True, text=True, check=True)
model = E.fromstring(result.stdout).find('model')
frames = [{'name': f.attrib['name'], 'attached_to': f.attrib.get('attached_to'),
           'pose': f.findtext('pose'), 'relative_to': f.find('pose').attrib.get('relative_to')}
          for f in model.findall('frame')]
base = model.find("link[@name='base_link']")
feet = []
for name in ('base_link_collision_3', 'base_link_collision_4'):
    c = base.find("collision[@name='%s']" % name)
    feet.append({'name': name, 'pose': c.findtext('pose'),
                 'box_size': c.findtext('geometry/box/size'),
                 'fragment': E.tostring(c, encoding='unicode')})
service = pathlib.Path('/opt/aero-bench/service.py').read_bytes()
print(json.dumps({'sources': sources,
    'expanded': {'sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
                 'bytes': len(result.stdout.encode()), 'model_name': model.attrib['name'],
                 'frames': frames, 'base_link_pose': base.findtext('pose'),
                 'base_link_relative_to': base.find('pose').attrib['relative_to'], 'feet': feet},
    'provider_service': {'path': '/opt/aero-bench/service.py',
                         'sha256': hashlib.sha256(service).hexdigest(), 'bytes': len(service)},
    'sdformat_stderr': result.stderr}, indent=2))
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def vector(text: str) -> list[float]:
    return [float(part) for part in text.split()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-sdf", type=Path, required=True)
    parser.add_argument("--observed-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    proc = subprocess.run([
        "docker", "run", "--rm", "-i", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "32", "--user", f"{os.getuid()}:{os.getgid()}",
        "--entrypoint", "python3", "-e",
        "SDF_PATH=/opt/px4-gazebo/share/gz/models", FLIGHT_IMAGE, "-",
    ], input=EXTRACT, text=True, capture_output=True, check=True)
    extracted = json.loads(proc.stdout)
    args.output.joinpath("pinned-model-extract.json").write_text(proc.stdout)
    expanded = extracted["expanded"]
    frames = {f["name"]: f for f in expanded["frames"]}
    base = frames["_merged__x500_base__model__"]
    parent = frames["_merged__x500__model__"]
    assert parent["relative_to"] == "__model__" and vector(parent["pose"]) == [0.0]*6
    assert base["relative_to"] == parent["name"] and vector(base["pose"]) == [0, 0, .24, 0, 0, 0]
    assert expanded["base_link_relative_to"] == base["name"]
    assert vector(expanded["base_link_pose"]) == [0.0]*6
    bottoms = []
    for foot in expanded["feet"]:
        pose = vector(foot["pose"]); size = vector(foot["box_size"])
        assert pose[3:] == [0.0]*3
        bottoms.append(.24 + pose[2] - size[2]/2)
    assert abs(bottoms[0]-bottoms[1]) < 1e-12
    signed_offset = -bottoms[0]
    world_bytes = args.world_sdf.read_bytes()
    world = ET.fromstring(world_bytes).find("world")
    pad = world.find("model[@name='launch.huangpu-research']")
    assert pad.attrib["name"] == "launch.huangpu-research"
    pad_pose = vector(pad.findtext("pose"))
    collision = pad.find("link/collision")
    size = vector(collision.findtext("geometry/box/size"))
    assert pad_pose[3:] == [0.0]*3 and collision.find("pose") is None
    pad_top = pad_pose[2] + size[2]/2
    receipt_bytes = args.observed_receipt.read_bytes()
    receipt = json.loads(receipt_bytes)
    provider_hash = next(s["sha256"] for s in receipt["pinned_container_sources"]
                         if s["path"] == "/opt/aero-bench/service.py")
    assert provider_hash == extracted["provider_service"]["sha256"]
    aircraft = next(a for a in receipt["final_scene"]["aircraft"] if a["entity_id"] == "uav.inspector")
    up = aircraft["pose"]["position"]["enu"]["up_m"]
    record = {
        "schema_version": "p02.native-source-pose-calibration/v1",
        "status": "source-derived-model-specific-calibration; new logistics run not executed",
        "flight_oci": FLIGHT_IMAGE, "provider_pose_reference": {
            "definition": "configured Gazebo model's root pose from /world/{world}/dynamic_pose/info Pose_V",
            "service": extracted["provider_service"],
            "code_path": "containers/px4-gazebo/service.py",
            "functions": ["_on_pose_topic_message", "_parse_pose_message", "_model_poses", "telemetry"],
            "frame": "Gazebo world ENU, metres; not MAVSDK AMSL/AGL or base_link pose",
            "model_source_name": "x500_mono_cam", "spawned_model_name": "x500_mono_cam_0",
        },
        "source_transform": {
            "model_sources": extracted["sources"],
            "expanded_sdf_sha256": expanded["sha256"],
            "expanded_sdf_bytes": expanded["bytes"],
            "model_to_base_link_translation_enu_m": [0, 0, .24],
            "base_link_to_level_contact_plane_z_m": vector(expanded["feet"][0]["pose"])[2] - vector(expanded["feet"][0]["box_size"])[2]/2,
            "model_to_level_contact_plane_z_m": bottoms[0],
            "pose_reference_above_contact_m": signed_offset,
            "applicability": "this pinned model and root-pose convention, level settled contact; not a universal UAV offset",
        },
        "pad_source": {"world_sdf_path": str(args.world_sdf), "sha256": digest(world_bytes),
                       "bytes": len(world_bytes), "world_name": world.attrib["name"],
                       "model_name": pad.attrib["name"], "pose_enu_m_rad": pad_pose,
                       "size_m": size, "contact_top_enu_up_m": pad_top},
        "observed_touchdown_check": {
            "source_receipt_path": str(args.observed_receipt), "sha256": digest(receipt_bytes),
            "run_id": receipt["run_id"], "compilation_id": receipt["compilation_id"],
            "sample": aircraft,
            "expected_root_up_from_source_m": pad_top + signed_offset,
            "measured_root_up_m": up,
            "contact_residual_m": up - (pad_top + signed_offset),
            "role": "independent observed check of source calculation; not used to fit calibration or authorize new dwell",
        },
        "required_compile_binding": ["carrier/provider/native_vehicle", "flight OCI digest", "provider service SHA256",
                                     "spawned model and source model hashes", "world/pad hash", "pose-reference frame",
                                     "source calibration record digest"],
        "unchanged": ["presence tolerance", "speed/contact criteria", "current-tick contiguous dwell", "original sealed run"],
    }
    payload = (json.dumps(record, indent=2)+"\n").encode()
    args.output.joinpath("calibration.json").write_bytes(payload)
    manifest = {"files": [{"path": p.name, "bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
                           for p in sorted(args.output.iterdir()) if p.is_file()]}
    args.output.joinpath("manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps({"output": str(args.output), "calibration_sha256": digest(payload),
                      "signed_offset_m": signed_offset, "contact_residual_m": record["observed_touchdown_check"]["contact_residual_m"]}))


if __name__ == "__main__":
    main()
