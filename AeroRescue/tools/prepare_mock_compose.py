#!/usr/bin/env python3
"""Prepare mount-separated mock container inputs; does not launch a simulator."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

import yaml

PUBLIC_KEYS = (
    "format", "profile", "seed", "recovery_timeout_ns", "public_map",
    "incident", "perception", "mock_scope",
)
PUBLIC_PAYLOAD_KEYS = (
    "package_id", "carrier", "receiver_id", "receiver_pose", "receiver_size",
    "package_size_m", "package_mass_kg", "visual_marker",
)


def prepare(scenario: Path, destination: Path, run_id: str) -> None:
    scene = yaml.safe_load(scenario.read_text())
    if set(scene["vehicles"]) != {"u1", "u2", "u3", "u4"}:
        raise ValueError("This Compose deployment requires u1/u2/u3/u4")
    if scene.get("mock_backend", {}).get("visual_backend") != "platform_city":
        raise ValueError("Supply an authored platform_city scenario; the legacy photo-billboard scene is not accepted")
    from aerorescue.companion.platform_city import prepare_platform_scene, validate_geometry
    scene = prepare_platform_scene(scene, scene["mock_backend"]["platform_city"]["roof_id"])
    validate_geometry(scene)  # actual source-city bounds; no browser or simulation
    destination.mkdir(parents=True, exist_ok=False)
    private = destination / "private"
    private.mkdir()
    scene["execution_backend"] = "mock"
    scene["agent_mode"] = "rule"
    scene["model_path"] = "/models/rtdetrv2_r18vd_120e_coco.onnx"
    scene["embedding_path"] = "/models/bge-small-zh-v1.5"
    scene.setdefault("mock_backend", {}).update(
        expected_clients=["u1", "u2", "u3", "u4", "centre"],
    )
    (private / "scenario.yaml").write_text(yaml.safe_dump(scene, sort_keys=False))
    (destination / "coordinator").mkdir()
    (destination / "ipc").mkdir()
    for uid in ["u1", "u2", "u3", "u4", "centre"]:
        (destination / "ipc" / uid).mkdir()
        (destination / "agents" / uid).mkdir(parents=True)
        cfgdir = destination / "config" / uid
        cfgdir.mkdir(parents=True)
        config = {key: copy.deepcopy(scene[key]) for key in PUBLIC_KEYS if key in scene}
        config.update(execution_backend="mock", agent_mode="rule",
                      model_path=scene["model_path"], embedding_path=scene["embedding_path"])
        config["vehicles"] = {}
        for name, profile in scene["vehicles"].items():
            row = {"capabilities": copy.deepcopy(profile.get("capabilities", []))}
            if name == uid:
                row = copy.deepcopy(profile)
            elif uid == "centre":
                # IDs of initial notices published on behalf of the centre, not
                # the task body, commitment, inventory or private energy state.
                task = profile.get("initial_task", {})
                row["initial_task"] = {key: task[key] for key in ("task_id", "task_revision") if key in task}
            config["vehicles"][name] = row
        if scene.get("payload"):
            config["payload"] = {key: copy.deepcopy(scene["payload"][key])
                                 for key in PUBLIC_PAYLOAD_KEYS if key in scene["payload"]}
        (cfgdir / "scenario.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    (destination / "preparation.json").write_text(json.dumps({
        "run_id": run_id, "execution_backend": "mock", "prepared_only": True,
        "containers_started": False, "source_scenario": str(scenario.resolve()),
        "uid": os.getuid(), "gid": os.getgid(),
        "agent_configuration": "own private profile plus public capabilities, map, incident and inventory geometry",
        "centre_configuration": "public capabilities and initial notice task IDs, no other vehicle profiles",
    }, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    prepare(args.scenario, args.run_root, args.run_id)
    print(f"Prepared only: {args.run_root.resolve()}; no container or simulation was started.")


if __name__ == "__main__":
    main()
