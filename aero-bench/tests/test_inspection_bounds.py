from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    ArtifactRequirement,
    ClockSpec,
    EnvironmentSpec,
    FileRef,
    GatewaySpec,
    ImplementationIdentity,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
)
from aero_bench.tasks.inspection import (
    ImagingBoundSpec,
    InspectionBoundSpec,
    InspectionTaskPackage,
    LinkBoundSpec,
    MissionBoundSpec,
    calculate_theoretical_bounds,
    resolve_inspection_bounds,
)
from tests.test_inspection_business import _package


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bound_environment(tmp_path, package: InspectionTaskPackage) -> EnvironmentSpec:
    config_path = tmp_path / "configs" / "business.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"inspection_bounds": '
        + json.dumps(
            package.bounds.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "}\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schemas" / "business.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text('{"type":"object"}\n', encoding="utf-8")
    protocol_path = tmp_path / "schemas" / "protocol.json"
    protocol_path.write_text('{"type":"object"}\n', encoding="utf-8")

    def runtime(digest_character: str, component_id: str) -> RuntimeSpec:
        return RuntimeSpec(
            runtime=RuntimeImage(
                image=f"registry.invalid/aero@sha256:{digest_character * 64}",
                command=("provider",),
            ),
            resources=ResourceBudget(
                cpu_millicores=1,
                memory_mib=64,
                gpu_count=0,
            ),
            implementation=ImplementationIdentity(
                component_id=component_id,
                kind="mechanical_fixture",
                source_uri="https://example.invalid/inspection-bound-test",
                source_revision=digest_character * 40,
                version="test-fixture-1",
            ),
        )

    provider = ProviderRef(
        provider_id="business",
        adapter="inspection.business",
        port=30001,
        workload=runtime("d", "inspection.business"),
        config=SchemaBoundFile(
            file=FileRef(path="configs/business.json", sha256=_digest(config_path)),
            schema_file=FileRef(
                path="schemas/business.json", sha256=_digest(schema_path)
            ),
        ),
        protocol_schema=FileRef(
            path="schemas/protocol.json", sha256=_digest(protocol_path)
        ),
        capabilities=("business.work-order",),
        artifact_requirements=(
            ArtifactRequirement(
                artifact_id="artifact.business",
                artifact_type="business.state",
                producer_id="business",
                visibility="private",
                relative_path="business/state.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
        ),
    )
    observation_provider = ProviderRef(
        provider_id="observation.provider",
        adapter="inspection.observation",
        port=30002,
        workload=runtime("c", "inspection.observation"),
        config=provider.config,
        protocol_schema=provider.protocol_schema,
        capabilities=("observation.capture",),
        artifact_requirements=(
            ArtifactRequirement(
                artifact_id="artifact.observation",
                artifact_type="observation.metadata",
                producer_id="observation.provider",
                visibility="private",
                relative_path="observation/metadata.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
        ),
    )
    return EnvironmentSpec(
        schema_version="aero-bench.environment/v1",
        environment_id="inspection.environment",
        harness=runtime("e", "aero-bench.harness"),
        clock=ClockSpec(
            authority="provider_barrier",
            step_ns=1,
            max_steps=1,
            provider_timeout_ms=1_000,
        ),
        gateway=GatewaySpec(
            protocol_schema=FileRef(
                path="schemas/protocol.json", sha256=_digest(protocol_path)
            ),
            port=30000,
        ),
        providers=(provider, observation_provider),
        harness_artifact_requirements=(
            ArtifactRequirement(
                artifact_id="artifact.event-log",
                artifact_type="event.log",
                producer_id="harness",
                visibility="private",
                relative_path="harness/event.log",
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
        ),
    )


def _bounds(*, deadline_s: float = 20.0) -> InspectionBoundSpec:
    return InspectionBoundSpec(
        mission=MissionBoundSpec(
            shortest_path_m=100.0,
            max_speed_mps=10.0,
            fixed_time_s=2.0,
            inspection_dwell_s=3.0,
            deadline_s=deadline_s,
        ),
        link=LinkBoundSpec(
            minimum_payload_bytes=1_000_000,
            max_bandwidth_bps=10_000_000.0,
            minimum_latency_s=0.1,
            upload_deadline_s=1.0,
        ),
        imaging=ImagingBoundSpec(
            defect_size_m=0.01,
            standoff_distance_m=10.0,
            focal_length_m=0.02,
            pixel_pitch_m=2e-6,
            minimum_resolvable_pixels=8.0,
        ),
        total_defects=4,
        geometrically_visible_defects=2,
    )


def test_strict_bound_model_reports_computed_limits() -> None:
    result = calculate_theoretical_bounds(_bounds(), network_delivery_required=True)

    assert result.success_upper_bound == 1.0
    assert result.mission_minimum_time_s == pytest.approx(15.0)
    assert result.upload_minimum_time_s == pytest.approx(0.9)
    assert result.projected_defect_pixels == pytest.approx(10.0)
    assert result.recall_upper_bound == pytest.approx(0.5)
    assert result.detection_f1_upper_bound == pytest.approx(2 / 3)


def test_impossible_bound_zeroes_each_affected_metric_ceiling() -> None:
    result = calculate_theoretical_bounds(
        _bounds(deadline_s=14.0), network_delivery_required=True
    )

    assert result.success_upper_bound == 0.0
    assert result.failed_conditions == ("mission_deadline",)
    assert result.detection_f1_upper_bound == pytest.approx(2 / 3)


def test_imaging_resolution_failure_zeroes_detection_ceiling() -> None:
    result = calculate_theoretical_bounds(
        InspectionBoundSpec(
            mission=_bounds().mission,
            link=_bounds().link,
            imaging=ImagingBoundSpec(
                defect_size_m=0.01,
                standoff_distance_m=10.0,
                focal_length_m=0.02,
                pixel_pitch_m=2e-6,
                minimum_resolvable_pixels=12.0,
            ),
            total_defects=4,
            geometrically_visible_defects=2,
        ),
        network_delivery_required=True,
    )

    assert result.success_upper_bound == 0.0
    assert result.detection_f1_upper_bound == 0.0
    assert result.recall_upper_bound == 0.0


def test_upload_deadline_applies_only_when_network_delivery_is_required() -> None:
    slow_link = _bounds().model_copy(
        update={
            "link": _bounds().link.model_copy(update={"upload_deadline_s": 0.5})
        }
    )

    required = calculate_theoretical_bounds(slow_link, network_delivery_required=True)
    assert required.failed_conditions == ("upload_deadline",)
    assert required.success_upper_bound == 0.0

    local = calculate_theoretical_bounds(slow_link, network_delivery_required=False)
    assert local.failed_conditions == ()
    assert local.success_upper_bound == 1.0


def test_bounds_reject_nan_inf_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MissionBoundSpec(
            shortest_path_m=float("nan"),
            max_speed_mps=10.0,
            fixed_time_s=0.0,
            inspection_dwell_s=0.0,
            deadline_s=20.0,
        )
    with pytest.raises(ValidationError):
        InspectionBoundSpec.model_validate(
            {**_bounds().model_dump(), "unexpected": True}
        )


def test_resolver_reads_every_bound_from_pinned_provider_config(tmp_path) -> None:
    package = _package()
    environment = _bound_environment(tmp_path, package)

    resolved = resolve_inspection_bounds(
        package,
        environment=environment,
        reader=BundleReader(tmp_path),
    )

    assert resolved.bounds == package.bounds
    assert {item.provider_id for item in resolved.provider_configs} == {
        "business",
        "observation.provider",
    }
    assert {item.config_digest for item in resolved.provider_configs} == {
        environment.providers[0].config.file.sha256
    }


def test_resolver_rejects_task_bound_tampering(tmp_path) -> None:
    package = _package()
    environment = _bound_environment(tmp_path, package)
    tampered_bounds = package.bounds.model_copy(
        update={
            "mission": package.bounds.mission.model_copy(update={"deadline_s": 21.0})
        }
    )
    tampered_package = package.model_copy(update={"bounds": tampered_bounds})

    with pytest.raises(ValueError, match="disagree"):
        resolve_inspection_bounds(
            tampered_package,
            environment=environment,
            reader=BundleReader(tmp_path),
        )


def test_resolver_rejects_provider_config_tampering_even_when_re_pinned(
    tmp_path,
) -> None:
    package = _package()
    environment = _bound_environment(tmp_path, package)
    config_path = tmp_path / "configs" / "business.json"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '"deadline_s":20.0', '"deadline_s":21.0'
        ),
        encoding="utf-8",
    )
    repinned_providers = tuple(
        provider.model_copy(
            update={
                "config": provider.config.model_copy(
                    update={
                        "file": FileRef(
                            path="configs/business.json",
                            sha256=_digest(config_path),
                        )
                    }
                )
            }
        )
        for provider in environment.providers
    )
    repinned_environment = environment.model_copy(
        update={"providers": repinned_providers}
    )

    with pytest.raises(ValueError, match="disagree"):
        resolve_inspection_bounds(
            package,
            environment=repinned_environment,
            reader=BundleReader(tmp_path),
        )
