"""Run the explicit Docker-backed SUMO/TraCI validation.

The ordinary pytest integration test remains usable on hosts without Docker.
This entry point is the only command that may report a SUMO integration
evidence result: it requires a locally available digest-pinned image and
promotes skips from the test into an explicit BLOCKED failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


_DIGEST_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_TEST = (
    "tests/providers/test_sumo_container_integration.py::"
    "test_sumo_default_service_container_rpc_round_trip"
)


def _report(status: str, **fields: object) -> None:
    print(json.dumps({"status": status, **fields}, sort_keys=True))


def _blocked(reason: str, *, image: str | None = None) -> int:
    _report("BLOCKED", image=image, reason=reason)
    return 2


def _inspect_image(image: str) -> tuple[dict[str, object] | None, int]:
    if shutil.which("docker") is None:
        return None, _blocked("docker CLI is unavailable", image=image)
    try:
        info = subprocess.run(
            ("docker", "info"), capture_output=True, text=True, check=False
        )
    except OSError as error:
        return None, _blocked(f"Docker daemon cannot be queried: {error}", image=image)
    if info.returncode != 0:
        detail = (info.stderr or info.stdout).strip()
        return None, _blocked(
            f"Docker daemon is unavailable{': ' + detail if detail else ''}",
            image=image,
        )
    inspected = subprocess.run(
        ("docker", "image", "inspect", "--format", "{{json .}}", image),
        capture_output=True,
        text=True,
        check=False,
    )
    if inspected.returncode != 0:
        detail = (inspected.stderr or inspected.stdout).strip()
        return None, _blocked(
            f"digest-pinned SUMO image is not locally available{': ' + detail if detail else ''}",
            image=image,
        )
    try:
        metadata = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        return None, _blocked(
            f"Docker returned invalid image metadata: {error}", image=image
        )
    if not isinstance(metadata, dict) or not isinstance(metadata.get("Id"), str):
        return None, _blocked("Docker image metadata has no image ID", image=image)
    return metadata, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the real digest-pinned SUMO/TraCI container round-trip"
    )
    parser.add_argument(
        "--image",
        help="digest-pinned SUMO image; defaults to AERO_BENCH_SUMO_IMAGE",
    )
    arguments = parser.parse_args(argv)
    image = arguments.image or os.environ.get("AERO_BENCH_SUMO_IMAGE")
    if image is None:
        return _blocked(
            "--image or AERO_BENCH_SUMO_IMAGE is required; no SUMO evidence was produced"
        )
    if _DIGEST_IMAGE.fullmatch(image) is None:
        return _blocked(
            "SUMO image must be an OCI digest reference (name@sha256:<64 hex>)",
            image=image,
        )

    metadata, preflight_status = _inspect_image(image)
    if preflight_status:
        return preflight_status
    assert metadata is not None

    repository_digests = metadata.get("RepoDigests", [])
    if not isinstance(repository_digests, list) or not all(
        isinstance(value, str) for value in repository_digests
    ):
        return _blocked(
            "Docker image metadata has invalid repository digests", image=image
        )

    environment = os.environ.copy()
    environment["AERO_BENCH_SUMO_IMAGE"] = image
    environment["AERO_BENCH_SUMO_RUNTIME_IMAGE"] = image
    environment["AERO_BENCH_SUMO_FORMAL"] = "1"
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            _TEST,
        ),
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        _report(
            "FAILED",
            image=image,
            image_id=metadata["Id"],
            pytest_returncode=result.returncode,
            repository_digests=sorted(repository_digests),
        )
        return result.returncode
    if re.search(r"\b1 passed\b", result.stdout) is None:
        _report(
            "FAILED",
            image=image,
            image_id=metadata["Id"],
            reason="strict SUMO test did not report exactly one passed test",
            repository_digests=sorted(repository_digests),
        )
        return 1
    _report(
        "real-traci-rpc-ok",
        image=image,
        image_id=metadata["Id"],
        repository_digests=sorted(repository_digests),
        validation="Docker container RPC round-trip with real SUMO TraCI",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
