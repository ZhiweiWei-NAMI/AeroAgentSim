#!/usr/bin/env python3
"""Build reusable local engineering images; never push or invent registry digests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "flight": ("px4-gazebo", "px4.gazebo"), "network": ("ns3", "ns3.rpc"),
    "traffic": ("sumo", "sumo.traci"), "harness": ("harness", "aero-bench.harness"),
    "verifier": ("urban-recovery-verifier", "urban.recovery.verifier"),
    "groundstation.rule": ("urban-recovery-participant", "groundstation.rule"),
    "uav.policy.01": ("urban-recovery-participant", "uav.policy.01"),
    "uav.policy.02": ("urban-recovery-participant", "uav.policy.02"),
}


def _inspect(reference: str) -> dict | None:
    result = subprocess.run(["docker", "image", "inspect", reference], capture_output=True, text=True)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def _freeze_source_snapshot(source: Path) -> None:
    """Keep owned build inputs readable but not editable after capture."""
    for path in source.rglob("*"):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    source.chmod(stat.S_IMODE(source.stat().st_mode) & ~0o222)


def _capture_build_context(source: Path, destination: Path) -> None:
    # Docker hashes COPY permissions too. Keep the original build modes in the
    # archive while protecting the diagnostic source snapshot against edits.
    def metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        return member

    with destination.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.GNU_FORMAT) as archive:
            for path in sorted(source.rglob("*")):
                archive.add(path, arcname=path.relative_to(source).as_posix(),
                            recursive=False, filter=metadata)
    destination.chmod(stat.S_IMODE(destination.stat().st_mode) & ~0o222)


def build_images(output: Path) -> Path:
    output = output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    source = output / "source"
    source.mkdir(mode=0o700)
    for directory in ("aero_bench", "containers"):
        shutil.copytree(ROOT / directory, source / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    shutil.copyfile(ROOT / ".dockerignore", source / ".dockerignore")
    context = output / "build-context.tar.gz"
    try:
        _capture_build_context(source, context)
    finally:
        _freeze_source_snapshot(source)
    files = [
        {"path": path.relative_to(source).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(source.rglob("*")) if path.is_file()
    ]
    revision = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (output / "build-inputs.json").write_text(json.dumps({
        "source_revision": revision, "files": files,
        "build_context": {"path": context.name,
                          "sha256": hashlib.sha256(context.read_bytes()).hexdigest(),
                          "permissions": "original_source_modes_before_write_protection"},
    }, sort_keys=True) + "\n")
    built = []
    result = {"status": "incomplete", "source_revision": revision, "built": built, "pushed": False, "simulator_executed": False}
    print(f"Build output: {output}\nSource revision: {revision}", flush=True)
    try:
        for component, (directory, component_id) in COMPONENTS.items():
            tag = f"aero-bench/urban-engineering-{component}:source-{revision[:16]}"
            inspected = _inspect(tag)
            if inspected is None:
                command = ["docker", "build", "--progress=plain", "--tag", tag,
                           "--build-arg", f"AERO_BENCH_REVISION={revision}",
                           "--file", f"containers/{directory}/Dockerfile"]
                if directory == "urban-recovery-participant":
                    command.extend(["--build-arg", f"AERO_BENCH_AGENT_ID={component}"])
                command.append("-")
                print(f"Building {component}", flush=True)
                with (output / f"{component}.build.log").open("xb") as log:
                    with context.open("rb") as build_input:
                        subprocess.run(command, stdin=build_input, stdout=log,
                                       stderr=subprocess.STDOUT, check=True,
                                       env={**os.environ, "DOCKER_BUILDKIT": "1"})
                inspected = _inspect(tag)
            labels = inspected["Config"].get("Labels", {}) if inspected else {}
            if labels.get("org.opencontainers.image.revision") != revision or labels.get("io.aero-bench.component") != component_id:
                raise ValueError(f"local image identity differs from build inputs; not overwriting {tag}")
            built.append({"component": component, "tag": tag, "image_id": inspected["Id"]})
            print(f"Ready {component}: {inspected['Id']}", flush=True)
        lock = output / "images-lock.json"
        lock.write_text(json.dumps({
            "reference_kind": "local_docker_config_id", "execution_scope": "executor_validation",
            "runtime_source_revision": revision,
            "images": {item["component"]: item["image_id"] for item in built},
        }, sort_keys=True) + "\n")
        result["status"] = "local_images_built"
        result["images_lock"] = str(lock)
        return lock
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (output / "build-result.json").write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="fresh local build directory")
    print(build_images(parser.parse_args().output))
