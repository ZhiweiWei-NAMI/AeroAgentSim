#!/usr/bin/env python3
"""Verify pinned Huangpu sources and expose native-city execution blockers."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeReadinessReport,
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.serialization import canonical_json_bytes  # noqa: E402


RUNTIME_PROBE_SCHEMA_VERSION = "aero-bench.city-native-runtime-probe/v1"


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(document) + b"\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _probe_sumo_source_image(
    report: CityNativeReadinessReport,
    *,
    formal_execution_declared: bool,
) -> dict[str, Any]:
    requested_id = report.source_evidence.sumo_image_id
    command = ["docker", "image", "inspect", requested_id]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        image: dict[str, Any] = {
            "requested_id": requested_id,
            "present": False,
            "failure": "docker-client-not-found",
        }
        docker_reachable = False
    except subprocess.TimeoutExpired:
        image = {
            "requested_id": requested_id,
            "present": False,
            "failure": "docker-image-inspect-timeout",
        }
        docker_reachable = False
    else:
        docker_reachable = completed.returncode == 0
        if completed.returncode != 0:
            image = {
                "requested_id": requested_id,
                "present": False,
                "failure": "docker-image-inspect-failed",
            }
        else:
            try:
                values = json.loads(completed.stdout)
                inspected = values[0]
                actual_id = inspected["Id"]
                configured_user = inspected["Config"].get("User", "")
                repo_digests = inspected.get("RepoDigests") or []
            except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise CityNativeRegistrationError(
                    "docker returned an invalid image-inspect document"
                ) from exc
            exact_id = actual_id == requested_id
            image = {
                "requested_id": requested_id,
                "present": exact_id,
                "actual_id": actual_id,
                "configured_user": configured_user,
                "configured_non_root": bool(configured_user)
                and configured_user not in {"0", "0:0", "root", "root:root"},
                "repo_digests": sorted(repo_digests),
            }
            if not exact_id:
                image["failure"] = "docker-returned-another-image-id"

    return {
        "schema_version": RUNTIME_PROBE_SCHEMA_VERSION,
        "registration_id": report.registration_id,
        "registration_sha256": report.registration_sha256,
        "docker_client_reached_daemon": docker_reachable,
        "source_sumo_image": image,
        "formal_execution_declared": formal_execution_declared,
        "formal_city_workload_images_verified": False,
        "conclusion": (
            "The accepted SUMO source image is locally present, but it is not a "
            "city formal-run image lock or Provider-barrier result."
            if image["present"]
            else "The accepted SUMO source image is not locally available."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root used to resolve every pinned file.",
    )
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--runtime-probe",
        type=Path,
        help="Optional output for a read-only Docker availability probe.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        definition = load_city_native_input_lock(args.lock)
        report = assess_city_native_registration(args.root, definition)
        _write_atomic(args.report, report.model_dump(mode="json"))
        if args.runtime_probe is not None:
            probe = _probe_sumo_source_image(
                report,
                formal_execution_declared=definition.execution is not None,
            )
            _write_atomic(args.runtime_probe, probe)
    except (CityNativeRegistrationError, OSError) as exc:
        print(f"city native assessment failed: {exc}", file=sys.stderr)
        return 2

    print(
        canonical_json_bytes(
            {
                "blocking_codes": list(report.blocking_codes),
                "registration_sha256": report.registration_sha256,
                "report": str(args.report),
                "status": report.status,
            }
        ).decode("utf-8")
    )
    return 0 if report.status == "ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
