from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.serialization import canonical_json_bytes  # noqa: E402


MANAGED_SOURCE = "https://github.com/ZhiweiWei-NAMI/AERO_BENCH"
REQUIRED_LABELS = (
    "io.aero-bench.component",
    "io.aero-bench.implementation-kind",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _inspect(image: str, docker_binary: str) -> dict[str, Any]:
    completed = subprocess.run(
        [docker_binary, "image", "inspect", image],
        check=True,
        text=True,
        capture_output=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError(f"Docker returned an invalid inspection for {image}")
    return value[0]


def _runtime_specs(run: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    environment = run["environment"]
    task = run["task"]
    entries: list[tuple[str, dict[str, Any]]] = [
        ("harness", environment["harness"]),
        ("verifier", task["verifier"]["workload"]),
    ]
    entries.extend(
        (f"provider:{provider['provider_id']}", provider["workload"])
        for provider in environment["providers"]
    )
    for agent in run["agents"]:
        entries.append((f"agent:{agent['agent_id']}", agent["workload"]))
        driver = agent.get("driver")
        if driver is not None:
            if not isinstance(driver, dict):
                raise ValueError("resolved Agent driver must be an object")
            driver_id = driver.get("driver_id")
            workload = driver.get("workload")
            if not isinstance(driver_id, str) or not driver_id:
                raise ValueError("resolved Agent driver_id must be a non-empty string")
            if not isinstance(workload, dict):
                raise ValueError("resolved Agent driver workload must be an object")
            entries.append((f"agent_driver:{driver_id}", workload))
    return entries


def _identity_record(
    *,
    role: str,
    spec: dict[str, Any],
    inspected: dict[str, Any],
) -> dict[str, Any]:
    image = spec["runtime"]["image"]
    implementation = spec["implementation"]
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ValueError(f"image has no OCI labels: {image}")
    missing = [name for name in REQUIRED_LABELS if not labels.get(name)]
    if missing:
        raise ValueError(f"image is missing required labels {missing}: {image}")
    expected = {
        "io.aero-bench.component": implementation["component_id"],
        "io.aero-bench.implementation-kind": implementation["kind"],
        "org.opencontainers.image.revision": implementation["source_revision"],
        "org.opencontainers.image.source": implementation["source_uri"],
        "org.opencontainers.image.version": implementation["version"],
    }
    drift = {
        name: {"expected": expected_value, "actual": labels.get(name)}
        for name, expected_value in expected.items()
        if labels.get(name) != expected_value
    }
    if drift:
        raise ValueError(f"image identity differs from resolved run: {image}: {drift}")
    repository_digests = inspected.get("RepoDigests")
    if not isinstance(repository_digests, list) or image not in repository_digests:
        raise ValueError(f"resolved image is not a local repository digest: {image}")
    image_id = inspected.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ValueError(f"Docker image ID is invalid: {image}")
    return {
        "role": role,
        "component_id": implementation["component_id"],
        "repository_digest": image,
        "image_id": image_id,
        "source_uri": implementation["source_uri"],
        "source_revision": implementation["source_revision"],
        "version": implementation["version"],
        "implementation_kind": implementation["kind"],
        "identity_validation": "passed",
    }


def build(args: argparse.Namespace) -> None:
    resolved_path = Path(args.resolved_run).resolve()
    summary_path = Path(args.runner_summary).resolve()
    output_path = Path(args.output).resolve()
    run = _load_object(resolved_path)
    summary = _load_object(summary_path)
    runs = summary.get("runs")
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise ValueError("runner summary must contain exactly one run")
    result = runs[0]
    if result.get("run_id") != run.get("run_id"):
        raise ValueError("runner summary does not belong to resolved run")
    if result.get("status") != "passed":
        raise ValueError("release lock requires a passed formal run")
    if result.get("execution_scope") != "formal_benchmark":
        raise ValueError("release lock requires formal_benchmark scope")

    managed: list[dict[str, Any]] = []
    participants: list[dict[str, Any]] = []
    for role, spec in _runtime_specs(run):
        image = spec["runtime"]["image"]
        record = _identity_record(
            role=role,
            spec=spec,
            inspected=_inspect(image, args.docker_binary),
        )
        if record["source_uri"] == MANAGED_SOURCE:
            managed.append(record)
        else:
            participants.append(record)
    revisions = {record["source_revision"] for record in managed}
    if len(revisions) != 1:
        raise ValueError("managed release images do not share one source revision")
    managed_revision = next(iter(revisions))

    keeper = _inspect(args.volume_keeper_image, args.docker_binary)
    keeper_id = keeper.get("Id")
    keeper_digests = keeper.get("RepoDigests")
    if (
        not isinstance(keeper_id, str)
        or not isinstance(keeper_digests, list)
        or args.volume_keeper_image not in keeper_digests
    ):
        raise ValueError("volume keeper image identity is invalid")

    keeper_release_member = next(
        (
            record["role"]
            for record in managed + participants
            if record["repository_digest"] == args.volume_keeper_image
        ),
        None,
    )
    if keeper_release_member is None:
        raise ValueError("volume keeper image is not a locked release workload")

    lock = {
        "schema_version": "aero-bench.release-lock/v1",
        "release_id": args.release_id,
        "managed_source_uri": MANAGED_SOURCE,
        "managed_source_revision": managed_revision,
        "resolved_run_sha256": __import__("hashlib").sha256(
            resolved_path.read_bytes()
        ).hexdigest(),
        "managed_images": sorted(managed, key=lambda item: item["role"]),
        "participant_submissions": sorted(
            participants, key=lambda item: item["role"]
        ),
        "executor_auxiliary_images": [
            {
                "role": "executor.volume-keeper",
                "repository_digest": args.volume_keeper_image,
                "image_id": keeper_id,
                "release_member_role": keeper_release_member,
            }
        ],
        "formal_run": {
            "run_id": result["run_id"],
            "status": result["status"],
            "seal": result["seal"],
            "verification": result["verification"],
            "public_trace": result["public_trace"],
            "runner_summary_sha256": __import__("hashlib").sha256(
                summary_path.read_bytes()
            ).hexdigest(),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_json_bytes(lock) + b"\n")
    print(
        json.dumps(
            {
                "managed_source_revision": managed_revision,
                "managed_image_count": len(managed),
                "participant_count": len(participants),
                "run_id": result["run_id"],
                "status": "locked",
            },
            sort_keys=True,
        )
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--release-id", default="inspection.v1.reference")
    result.add_argument("--resolved-run", required=True)
    result.add_argument("--runner-summary", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--volume-keeper-image", required=True)
    result.add_argument("--docker-binary", default="docker")
    return result


def main() -> None:
    build(parser().parse_args())


if __name__ == "__main__":
    main()
