from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import EnvironmentSpec, ProviderRef
from aero_bench.tasks.inspection.contracts import (
    INSPECTION_BOUND_FIELDS,
    InspectionBoundResolution,
    InspectionBoundSpec,
    InspectionProviderConfigBinding,
    InspectionTheoreticalBounds,
    ImagingBoundSpec,
    LinkBoundSpec,
    MissionBoundSpec,
)

if TYPE_CHECKING:
    from aero_bench.tasks.inspection.contracts import InspectionTaskPackage


def _decode_pointer(pointer: str) -> tuple[str, ...]:
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")
    )


def _pointer_value(document: object, pointer: str) -> object:
    current = document
    for token in _decode_pointer(pointer):
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"provider config pointer does not exist: {pointer}")
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdecimal():
                raise ValueError(
                    f"provider config pointer uses a non-index list token: {pointer}"
                )
            index = int(token)
            if index >= len(current):
                raise ValueError(f"provider config pointer is out of range: {pointer}")
            current = current[index]
            continue
        raise ValueError(f"provider config pointer does not address a value: {pointer}")
    return current


def _load_provider_config(reader: BundleReader, provider: ProviderRef) -> object:
    config = provider.config
    config_path = reader.resolve_file(config.file)
    reader.resolve_file(config.schema_file)
    if config_path.suffix.lower() != ".json":
        raise ValueError(
            f"inspection bound provider config must be JSON: {config.file.path}"
        )

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite provider config value: {value}")

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError(f"duplicate provider config key: {key}")
            document[key] = value
        return document

    try:
        return json.loads(
            config_path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(
            f"provider config is not strict JSON: {config.file.path}"
        ) from error


def _numeric_value(field: str, value: object) -> int | float:
    if field in {
        "total_defects",
        "geometrically_visible_defects",
        "link.minimum_payload_bytes",
    }:
        if type(value) is not int or value < 0:
            raise ValueError(
                f"provider config value for {field} must be a nonnegative integer"
            )
        if field in {"total_defects", "link.minimum_payload_bytes"} and value == 0:
            raise ValueError(f"provider config {field} must be positive")
        return value
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"provider config value for {field} must be a finite number")
    return float(value)


def _bound_spec_from_values(values: dict[str, int | float]) -> InspectionBoundSpec:
    if set(values) != set(INSPECTION_BOUND_FIELDS):
        raise ValueError(
            "provider config does not provide every inspection bound input"
        )
    return InspectionBoundSpec(
        mission=MissionBoundSpec(
            shortest_path_m=float(values["mission.shortest_path_m"]),
            max_speed_mps=float(values["mission.max_speed_mps"]),
            fixed_time_s=float(values["mission.fixed_time_s"]),
            inspection_dwell_s=float(values["mission.inspection_dwell_s"]),
            deadline_s=float(values["mission.deadline_s"]),
        ),
        link=LinkBoundSpec(
            minimum_payload_bytes=int(values["link.minimum_payload_bytes"]),
            max_bandwidth_bps=float(values["link.max_bandwidth_bps"]),
            minimum_latency_s=float(values["link.minimum_latency_s"]),
            upload_deadline_s=float(values["link.upload_deadline_s"]),
        ),
        imaging=ImagingBoundSpec(
            defect_size_m=float(values["imaging.defect_size_m"]),
            standoff_distance_m=float(values["imaging.standoff_distance_m"]),
            focal_length_m=float(values["imaging.focal_length_m"]),
            pixel_pitch_m=float(values["imaging.pixel_pitch_m"]),
            minimum_resolvable_pixels=float(
                values["imaging.minimum_resolvable_pixels"]
            ),
        ),
        total_defects=int(values["total_defects"]),
        geometrically_visible_defects=int(values["geometrically_visible_defects"]),
    )


def resolve_inspection_bounds(
    package: "InspectionTaskPackage",
    *,
    environment: EnvironmentSpec,
    reader: BundleReader,
) -> InspectionBoundResolution:
    """Resolve every bound input from the resolved environment.

    The task package values are a declaration that must exactly match the
    provider-config values. They are never an authority by themselves. The
    caller must have already run the bundle's SchemaBoundFile JSON-Schema
    validation; this function additionally rechecks both pinned files and
    reads the exact JSON document used by each source pointer.
    """

    providers = {provider.provider_id: provider for provider in environment.providers}
    values: dict[str, int | float] = {}
    if not package.network_delivery_required:
        values.update(
            {
                "link.minimum_payload_bytes": package.bounds.link.minimum_payload_bytes,
                "link.max_bandwidth_bps": package.bounds.link.max_bandwidth_bps,
                "link.minimum_latency_s": package.bounds.link.minimum_latency_s,
                "link.upload_deadline_s": package.bounds.link.upload_deadline_s,
            }
        )
    documents: dict[str, object] = {}
    for source in package.bound_sources:
        provider = providers.get(source.provider_id)
        if provider is None:
            raise ValueError(
                "inspection bound source names an undeclared provider: "
                f"{source.provider_id}"
            )
        document = documents.get(source.provider_id)
        if document is None:
            document = _load_provider_config(reader, provider)
            documents[source.provider_id] = document
        if source.bound_field in values:
            raise ValueError(
                f"inspection bound input has duplicate source: {source.bound_field}"
            )
        values[source.bound_field] = _numeric_value(
            source.bound_field,
            _pointer_value(document, source.config_pointer),
        )
    for provider_id in {
        observation.trigger.provider_id for observation in package.observations
    }:
        provider = providers.get(provider_id)
        if provider is None:
            raise ValueError(
                "inspection observation names an undeclared provider: " f"{provider_id}"
            )
        if provider_id not in documents:
            documents[provider_id] = _load_provider_config(reader, provider)
    resolved = _bound_spec_from_values(values)
    if resolved != package.bounds:
        raise ValueError(
            "resolved provider bounds disagree with the inspection task package"
        )
    return InspectionBoundResolution(
        bounds=resolved,
        provider_configs=tuple(
            InspectionProviderConfigBinding(
                provider_id=provider_id,
                config_digest=providers[provider_id].config.file.sha256,
            )
            for provider_id in sorted(documents)
        ),
    )


def calculate_resolved_theoretical_bounds(
    package: "InspectionTaskPackage",
    *,
    environment: EnvironmentSpec,
    reader: BundleReader,
) -> InspectionTheoreticalBounds:
    return calculate_theoretical_bounds(
        resolve_inspection_bounds(
            package, environment=environment, reader=reader
        ).bounds,
        network_delivery_required=package.network_delivery_required,
    )


def calculate_theoretical_bounds(
    bounds: InspectionBoundSpec,
    *,
    network_delivery_required: bool,
) -> InspectionTheoreticalBounds:
    """Compute necessary-condition ceilings from resolved task parameters.

    A ceiling of one means only that these necessary conditions do not prove
    impossibility. It is not a prediction of benchmark success. The F1 ceiling
    assumes perfect precision for every geometrically visible defect. A sample
    below the configured pixel resolution cannot contribute to the detection
    ceiling.
    """

    mission_minimum = bounds.mission.minimum_time_s
    upload_minimum = bounds.link.minimum_transfer_time_s
    projected_pixels = bounds.imaging.projected_defect_pixels
    failed: list[str] = []
    if mission_minimum > bounds.mission.deadline_s:
        failed.append("mission_deadline")
    if (
        network_delivery_required
        and upload_minimum > bounds.link.upload_deadline_s
    ):
        failed.append("upload_deadline")
    if projected_pixels < bounds.imaging.minimum_resolvable_pixels:
        failed.append("imaging_resolution")

    recall = bounds.geometrically_visible_defects / bounds.total_defects
    if "imaging_resolution" in failed:
        recall = 0.0
    f1 = 2 * recall / (1 + recall) if recall else 0.0
    return InspectionTheoreticalBounds(
        mission_minimum_time_s=mission_minimum,
        upload_minimum_time_s=upload_minimum,
        projected_defect_pixels=projected_pixels,
        success_upper_bound=0.0 if failed else 1.0,
        recall_upper_bound=recall,
        detection_f1_upper_bound=f1,
        failed_conditions=tuple(failed),
    )


__all__ = [
    "calculate_resolved_theoretical_bounds",
    "calculate_theoretical_bounds",
    "resolve_inspection_bounds",
]
