from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import pytest

from aero_bench.authoring.traffic_preview import parse_workspace_preview_demand
from aero_bench.authoring.traffic_preview_contracts import (
    TrafficPreviewJob,
    TrafficPreviewRequest,
)
from aero_bench.authoring.traffic_preview_jobs import (
    PinnedImplementationInput,
    PinnedRepositoryFile,
    SourceLicenseEvidence,
    TrafficPreviewError,
    TrafficPreviewJobManager,
    TrafficPreviewProfileDefinition,
    TrafficPreviewProfileRegistry,
    _profile_sha,
)
from aero_bench.serialization import canonical_json_bytes


IMAGE = "sha256:" + "6" * 64
SCENE_PATH = "/city-presentation/test-scene.json"
ROOT = Path(__file__).resolve().parents[1]


def workspace(*, vehicles: int = 4, pedestrians: int = 2, bicycles: int = 1) -> dict:
    return {
        "purpose": "scenario-authoring",
        "schema_version": "aero-bench.city-workspace/v3",
        "name": "Traffic preview test",
        "scenePath": SCENE_PATH,
        "seed": 17,
        "environment": {
            "cloudCover": 0, "precipitation": "none", "precipitationRateMmPerH": 0,
            "visibilityM": 10000, "windMps": 0, "windDirectionDeg": 0,
            "timeOfDay": "day", "reflectionsEnabled": True,
        },
        "fleet": [], "traffic": {
            "vehicles": vehicles, "pedestrians": pedestrians, "bicycles": bicycles,
        },
        "facilities": [], "airspace": [],
        "algorithms": {
            "mode": "centralized", "assignment": "greedy", "routing": "astar",
            "energy": "reserve_threshold", "parameters": {},
        },
        "deployment": {"executor": "docker_reference", "imageRef": ""},
        "events": [], "actionRules": [], "stateKeyframes": [], "labelRules": [],
        "orders": [],
        "orderGeneration": {
            "seed": 17, "maxOrders": 0, "startAtS": 0, "endAtS": 3600,
            "cargoMinKg": 0.1, "cargoMaxKg": 1, "deadlineLeadS": 600,
        },
        "performanceProfiles": [], "authoredLandscape": [],
    }


def _pin(root: Path, relative: str, raw: bytes) -> PinnedRepositoryFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return PinnedRepositoryFile(
        path=relative, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
    )


def registry(root: Path) -> TrafficPreviewProfileRegistry:
    scene = _pin(root, "frontend/public/city-presentation/test-scene.json", b'{"scene":1}\n')
    direct = {
        name: _pin(root, f"inputs/{name}.dat", name.encode())
        for name in (
            "network", "engineering_inputs", "mesh_pack_manifest", "road",
            "rendered_objects", "render_manifest", "source_osm", "effective_fixtures",
        )
    }
    provenance = canonical_json_bytes({
        "source_dataset": "OpenStreetMap test extract",
        "license": "OpenStreetMap ODbL 1.0",
        "attribution": "© OpenStreetMap contributors",
        "offline": True, "runtime_remote_requests": False,
    })
    source_license = _pin(root, "inputs/source-provenance.json", provenance)
    implementations = tuple(PinnedImplementationInput(role=role, file=_pin(
        root, f"code/{role}.py",
        (f'SUMO_IMAGE = "{IMAGE}"\n' if role in {"builder", "auditor"} else f"# {role}\n").encode(),
    )) for role in ("builder", "auditor", "geometry"))
    definition = TrafficPreviewProfileDefinition(
        schema_version="aero-bench.traffic-preview-profile/v1",
        profile_id="test.traffic",
        profile_sha256="1" * 64,
        scene_path=SCENE_PATH, scene=scene,
        source_license=SourceLicenseEvidence(
            file=source_license, status="documented",
            source_dataset="OpenStreetMap test extract",
            license="OpenStreetMap ODbL 1.0",
            attribution="© OpenStreetMap contributors",
            offline=True, runtime_remote_requests=False,
        ),
        sumo_image_id=IMAGE, implementation_inputs=implementations,
        **direct,
    )
    definition = definition.model_copy(update={"profile_sha256": _profile_sha(definition)})
    manifest = {
        "schema_version": "aero-bench.traffic-preview-profile-registry/v1",
        "profiles": [definition.model_dump(mode="json")],
    }
    path = root / "profiles.json"
    path.write_bytes(canonical_json_bytes(manifest))
    return TrafficPreviewProfileRegistry.from_manifest(path, repository_root=root)


def request(registry_value: TrafficPreviewProfileRegistry, **counts: int) -> TrafficPreviewRequest:
    profile = registry_value.catalog().profiles[0]
    return TrafficPreviewRequest.model_validate({
        "schema_version": "aero-bench.traffic-preview-request/v1",
        "profile_id": profile.profile_id, "profile_sha256": profile.profile_sha256,
        "duration_seconds": 30, "draft": workspace(**counts),
    })


def passing_runner(profile, paths, duration, auditing) -> None:
    demand = parse_workspace_preview_demand(paths.workspace.read_bytes())
    source = demand.source_identity()
    traffic = source["traffic"]
    trace = {
        "schema_version": "aero-bench.city-sumo-preview/v2",
        "artifact_class": "offline-engineering-preview",
        "source_kind": "offline-sumo-engineering-preview",
        "seed": source["seed"], "duration_seconds": duration,
        "demand_authoring": source,
        "demand": {
            "authored": {"sedan": traffic["vehicles"], "bicycle": traffic["bicycles"]},
            "persons": traffic["pedestrians"], "observed": {},
        },
        "frames": [], "signals": [],
    }
    raw = canonical_json_bytes(trace)
    paths.trace.write_bytes(raw)
    (paths.private_evidence / "native-secret.json").write_text("private", encoding="utf-8")
    auditing()
    audit = {
        "schema_version": "aero-bench.city-canonical-motion-audit/v2",
        "artifact_class": "offline-engineering-preview-audit",
        "status": "PASS", "failures": [], "demand_authoring": source,
        "inputs": {"traffic": {
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        }},
    }
    paths.audit.write_bytes(canonical_json_bytes(audit))


def wait(manager: TrafficPreviewJobManager, job_id: str) -> TrafficPreviewJob:
    for _ in range(200):
        result = manager.status(job_id)
        if result.state in {"ready", "failed"}:
            return result
        time.sleep(0.01)
    raise AssertionError("traffic preview job did not finish")


def test_registry_catalog_and_ready_job_publish_only_the_audited_trace(tmp_path: Path) -> None:
    profiles = registry(tmp_path / "repo")
    with TrafficPreviewJobManager(tmp_path / "jobs", profiles, runner=passing_runner) as manager:
        submitted = manager.submit(request(profiles))
        result = wait(manager, submitted.job_id)
        assert result.state == "ready"
        assert result.trace is not None and result.canonical_audit is not None
        raw = manager.published_trace(result.job_id, result.trace.sha256)
        assert hashlib.sha256(raw).hexdigest() == result.trace.sha256
        assert result.trace.url == (
            f"/authoring/v1/traffic-previews/{result.job_id}/assets/{result.trace.sha256}"
        )
        assert (manager.output_root / result.job_id / "private" / "native-secret.json").is_file()
        assert not (manager.output_root / result.job_id / "public" / "native-secret.json").exists()
        with pytest.raises(TrafficPreviewError, match="not published"):
            manager.published_trace(result.job_id, "f" * 64)


def test_checked_in_huangpu_profile_verifies_every_pinned_input() -> None:
    profiles = TrafficPreviewProfileRegistry.from_manifest(
        ROOT / "aero_bench/authoring/traffic_preview_profiles.json",
        repository_root=ROOT,
    )
    assert profiles.catalog().model_dump(mode="json") == {
        "schema_version": "aero-bench.traffic-preview-profile-catalog/v1",
        "profiles": [{
            "profile_id": "huangpu.sumo-preview.v1",
            "profile_sha256": "5200960197e684290adf43945105bdd797adffa93d65d10cf3749eabd7fcf407",
            "scene_path": "/city-presentation/default-scene-v1.json",
            "scene_sha256": "1df4de6d8a175bfb71c385f0f422a012135dbae890db382578c0706d44d61099",
            "source_license_status": "documented",
            "preview_scope": "offline-engineering-preview",
        }],
    }


def test_job_identity_changes_with_actual_workspace_demand_and_is_idempotent(tmp_path: Path) -> None:
    profiles = registry(tmp_path / "repo")
    with TrafficPreviewJobManager(tmp_path / "jobs", profiles, runner=passing_runner) as manager:
        low = manager.submit(request(profiles, vehicles=1, pedestrians=0, bicycles=0))
        repeated = manager.submit(request(profiles, vehicles=1, pedestrians=0, bicycles=0))
        high = manager.submit(request(profiles, vehicles=2, pedestrians=1, bicycles=1))
        assert low.job_id == repeated.job_id
        assert low.job_id != high.job_id
        assert low.workspace_sha256 != high.workspace_sha256
        assert wait(manager, low.job_id).state == "ready"
        assert wait(manager, high.job_id).state == "ready"


def test_exact_profile_pin_and_scene_are_required_before_a_job_starts(tmp_path: Path) -> None:
    profiles = registry(tmp_path / "repo")
    valid = request(profiles)
    with TrafficPreviewJobManager(tmp_path / "jobs", profiles, runner=passing_runner) as manager:
        with pytest.raises(TrafficPreviewError, match="pin has changed"):
            manager.submit(valid.model_copy(update={"profile_sha256": "f" * 64}))
        wrong = valid.model_copy(update={
            "draft": valid.draft.model_copy(update={
                "scenePath": "/city-presentation/other-scene.json",
            }),
        })
        with pytest.raises(TrafficPreviewError, match="scenePath"):
            manager.submit(wrong)


def test_failed_independent_audit_never_publishes_a_trace(tmp_path: Path) -> None:
    profiles = registry(tmp_path / "repo")

    def failing_audit(profile, paths, duration, auditing) -> None:
        passing_runner(profile, paths, duration, auditing)
        audit = json.loads(paths.audit.read_text())
        audit["status"] = "FAIL"
        audit["failures"] = ["measured failure"]
        paths.audit.write_bytes(canonical_json_bytes(audit))

    with TrafficPreviewJobManager(tmp_path / "jobs", profiles, runner=failing_audit) as manager:
        result = wait(manager, manager.submit(request(profiles)).job_id)
        assert result.state == "failed"
        assert result.error is not None and result.error.code == "traffic_preview_audit_failed"
        assert result.trace is None and result.canonical_audit is None
        with pytest.raises(TrafficPreviewError, match="not ready"):
            manager.published_trace(result.job_id, "f" * 64)
        assert (manager.output_root / ".failures" / result.job_id / "private" / "audit.log").parent.is_dir()


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("source_kind", "unbound-preview"),
        ("duration_seconds", 31),
        ("authored", [1, 2, 3]),
        ("boolean_authoring", True),
    ],
)
def test_publication_rejects_trace_identity_or_shape_drift(
    tmp_path: Path, mutation: str, value: object,
) -> None:
    profiles = registry(tmp_path / "repo")

    def drifting_runner(profile, paths, duration, auditing) -> None:
        passing_runner(profile, paths, duration, auditing)
        trace = json.loads(paths.trace.read_text())
        if mutation == "authored":
            trace["demand"]["authored"] = value
        elif mutation == "boolean_authoring":
            trace["demand_authoring"]["traffic"]["bicycles"] = value
        else:
            trace[mutation] = value
        raw = canonical_json_bytes(trace)
        paths.trace.write_bytes(raw)
        audit = json.loads(paths.audit.read_text())
        if mutation == "boolean_authoring":
            audit["demand_authoring"]["traffic"]["bicycles"] = value
        audit["inputs"]["traffic"] = {
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        }
        paths.audit.write_bytes(canonical_json_bytes(audit))

    with TrafficPreviewJobManager(tmp_path / "jobs", profiles, runner=drifting_runner) as manager:
        result = wait(manager, manager.submit(request(profiles)).job_id)
        assert result.state == "failed"
        assert result.error is not None
        assert result.error.code == "traffic_preview_integrity_failed"
        assert result.trace is None


def test_registry_rechecks_every_pinned_input_before_submission(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    profiles = registry(root)
    (root / "inputs/network.dat").write_bytes(b"changed")
    output = tmp_path / "jobs"
    with TrafficPreviewJobManager(output, profiles, runner=passing_runner) as manager:
        with pytest.raises(TrafficPreviewError, match="input drifted"):
            manager.submit(request(profiles))


def test_safe_recording_failure_reports_a_lost_declared_fleet_type(tmp_path: Path) -> None:
    log = tmp_path / "build.log"
    log.write_text(
        "recorder output\n"
        "ValueError: Rendered traffic lost a declared observed fleet type: ['bus']\n",
        encoding="utf-8",
    )
    assert TrafficPreviewJobManager._safe_recording_failure(log) == (
        "Rendered traffic lost a declared observed fleet type: ['bus']"
    )


@pytest.mark.parametrize("path", ("../escape", "code/../escape.py", "./code.py", "/code.py"))
def test_pinned_repository_path_rejects_navigation(path: str) -> None:
    with pytest.raises(ValueError):
        PinnedRepositoryFile(path=path, sha256="1" * 64, size_bytes=1)


def test_public_job_contract_rejects_premature_and_formal_claims() -> None:
    common = {
        "schema_version": "aero-bench.traffic-preview-job/v1",
        "job_id": "1" * 64, "profile_id": "test.traffic", "profile_sha256": "2" * 64,
        "workspace_sha256": "3" * 64, "workspace_size_bytes": 2,
        "duration_seconds": 30, "trace": None, "canonical_audit": None, "error": None,
        "preview_scope": "offline-engineering-preview",
        "formal_provider_bound": False, "executed": False, "verified": False,
    }
    with pytest.raises(ValueError, match="ready traffic preview"):
        TrafficPreviewJob.model_validate({**common, "state": "ready"})
    with pytest.raises(ValueError):
        TrafficPreviewJob.model_validate({**common, "state": "queued", "verified": True})
    for field_name in ("preview_scope", "formal_provider_bound", "executed", "verified"):
        missing = {**common, "state": "queued"}
        del missing[field_name]
        with pytest.raises(ValueError):
            TrafficPreviewJob.model_validate(missing)
