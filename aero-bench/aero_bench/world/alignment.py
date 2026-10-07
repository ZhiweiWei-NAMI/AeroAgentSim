"""Alignment of WorldPackage v2 building assets to their declared source.

A ``BuildingSpec`` carries only source/render/collision asset IDs. This module
resolves those IDs through the exact ``WorldPackage.assets`` manifest, verifies
the three pinned files and declared byte sizes, and then binds — fail closed —
what the package declares:

- the declared source, rendering, and collision artifact identities,
- the building geometry and the world transform label the derivation used,
- the pinned identity and digest of the generator that produced the
  derived assets.

The manifest names the one `world_id` it was built for; `verify_alignment`
recomputes the binding from the declared `BuildingSpec`s of that same
world and fails closed on any world mismatch, on any mismatched source,
rendering, collision, geometry, transform, or generator field, on any
broken per-building or manifest digest, or on any missing/extra/duplicate
building coverage.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef, Identifier, Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.contracts import (
    ArtifactSelector,
    AssetRecord,
    BuildingSpec,
    WORLD_SCHEMA_VERSION,
    WorldPackage,
)

ALIGNMENT_SCHEMA_VERSION = "aero-bench.world-alignment/v2"

#: The one derivation convention this manifest format supports. A package
#: producer must declare the same transform label as the world frame.
SUPPORTED_TRANSFORM = "wgs84_geodetic_to_enu_v1"


def _require_real_digest(value: str) -> str:
    if value == "0" * 64:
        raise ValueError("sha256 must be a real digest, not a placeholder")
    return value


class GeneratorIdentity(StrictModel):
    """Pinned identity of the external tool that produced derived assets."""

    generator_id: Identifier
    version: Annotated[str, Field(min_length=1)]
    source_revision: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]

    @model_validator(mode="after")
    def not_a_placeholder(self) -> "GeneratorIdentity":
        if set(self.source_revision) == {"0"}:
            raise ValueError("generator source_revision cannot be a placeholder")
        return self


class BuildingAlignment(StrictModel):
    """The verifiable alignment record for one declared building.

    Every artifact field is the *declared* reference copied from the
    `BuildingSpec`; digests are whatever the producer sealed. The record
    binds them to the geometry and generator used for the derivation.
    """

    building_id: Identifier
    transform: Literal["wgs84_geodetic_to_enu_v1"]
    generator: GeneratorIdentity
    source_asset: ArtifactSelector
    rendering_artifact: ArtifactSelector
    collision_artifact: ArtifactSelector
    geometry_json_sha256: Sha256
    alignment_sha256: Sha256

    @model_validator(mode="after")
    def declared_digests_are_real(self) -> "BuildingAlignment":
        _require_real_digest(self.geometry_json_sha256)
        _require_real_digest(self.alignment_sha256)
        return self


class AlignmentManifest(StrictModel):
    """The sealed alignment document for exactly one declared world.

    `world_id` and `world_schema_version` are mandatory without defaults:
    the manifest is bound to one world and one world schema, and the
    manifest digest covers that binding.
    """

    schema_version: Literal["aero-bench.world-alignment/v2"]
    world_id: Identifier
    world_schema_version: Literal["aero-bench.world/v2"]
    world_digest: Sha256
    asset_digest: Sha256
    alignments: tuple[BuildingAlignment, ...] = Field(min_length=1)
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def declared_digests_are_real(self) -> "AlignmentManifest":
        _require_real_digest(self.world_digest)
        _require_real_digest(self.asset_digest)
        _require_real_digest(self.manifest_sha256)
        return self


def _selector_file_ref(selector: ArtifactSelector) -> FileRef:
    path = selector.selector.split("#", maxsplit=1)[0]
    return FileRef(path=path, sha256=selector.sha256)


def _verify_asset_bytes(
    reader: BundleReader,
    asset: AssetRecord,
    *,
    label: str,
) -> None:
    resolved = reader.resolve_file(_selector_file_ref(asset.artifact))
    size_bytes = resolved.stat().st_size
    if size_bytes != asset.byte_size:
        raise ValueError(
            f"{label} byte_size mismatch: expected {asset.byte_size}, got {size_bytes}"
        )


def _asset_manifest(
    assets: tuple[AssetRecord, ...],
) -> dict[str, AssetRecord]:
    by_id = {asset.artifact.artifact_id: asset for asset in assets}
    if len(by_id) != len(assets):
        raise ValueError("asset manifest contains duplicate asset IDs")
    return by_id


def _building_assets(
    building: BuildingSpec,
    assets: tuple[AssetRecord, ...],
) -> tuple[AssetRecord, AssetRecord, AssetRecord]:
    by_id = _asset_manifest(assets)
    resolved: list[AssetRecord] = []
    for field_name, asset_id, expected_role in (
        ("source_asset_id", building.source_asset_id, "building_source"),
        ("render_asset_id", building.render_asset_id, "building_render"),
        (
            "collision_asset_id",
            building.collision_asset_id,
            "building_collision",
        ),
    ):
        asset = by_id.get(asset_id)
        if asset is None or asset.asset_role != expected_role:
            raise ValueError(
                f"building {building.building_id!r} {field_name} must resolve to "
                f"an exact {expected_role} asset"
            )
        resolved.append(asset)
    return resolved[0], resolved[1], resolved[2]


def geometry_json_sha256(building: BuildingSpec) -> str:
    """SHA-256 over the canonical JSON of the declared building geometry."""
    return hashlib.sha256(
        canonical_json_bytes(building.geometry.model_dump(mode="json"))
    ).hexdigest()


def alignment_content(
    building: BuildingSpec,
    *,
    assets: tuple[AssetRecord, ...],
    transform: str,
    generator: GeneratorIdentity,
) -> dict[str, object]:
    """The content bound by the per-building alignment digest.

    This is exactly the building's declared artifact references, its
    canonical geometry document, the transform, and the generator.
    """
    source, rendering, collision = _building_assets(building, assets)
    return {
        "building_id": building.building_id,
        "transform": transform,
        "generator": generator.model_dump(mode="json"),
        "source_asset": source.artifact.model_dump(mode="json"),
        "rendering_artifact": rendering.artifact.model_dump(mode="json"),
        "collision_artifact": collision.artifact.model_dump(mode="json"),
        "geometry": building.geometry.model_dump(mode="json"),
    }


def _signed_alignment(
    building: BuildingSpec,
    *,
    assets: tuple[AssetRecord, ...],
    reader: BundleReader,
    generator: GeneratorIdentity,
) -> BuildingAlignment:
    """Bind a declared building without altering any declared identity."""
    source, rendering, collision = _building_assets(building, assets)
    for label, asset in (
        ("source asset", source),
        ("rendering asset", rendering),
        ("collision asset", collision),
    ):
        _verify_asset_bytes(
            reader,
            asset,
            label=f"building {building.building_id!r} {label}",
        )
    return BuildingAlignment(
        building_id=building.building_id,
        transform=SUPPORTED_TRANSFORM,
        generator=generator,
        source_asset=source.artifact,
        rendering_artifact=rendering.artifact,
        collision_artifact=collision.artifact,
        geometry_json_sha256=geometry_json_sha256(building),
        alignment_sha256=hashlib.sha256(
            canonical_json_bytes(
                alignment_content(
                    building,
                    assets=assets,
                    transform=SUPPORTED_TRANSFORM,
                    generator=generator,
                )
            )
        ).hexdigest(),
    )


def alignment_manifest(
    *,
    reader: BundleReader,
    world: WorldPackage,
    generator: GeneratorIdentity,
) -> AlignmentManifest:
    """Build the manifest binding one world's declared buildings (fail on none)."""
    if not world.buildings:
        raise ValueError("alignment manifest requires at least one building")
    alignments = tuple(
        _signed_alignment(
            building,
            assets=world.assets,
            reader=reader,
            generator=generator,
        )
        for building in sorted(world.buildings, key=lambda item: item.building_id)
    )
    body = {
        "schema_version": ALIGNMENT_SCHEMA_VERSION,
        "world_id": world.world_id,
        "world_schema_version": WORLD_SCHEMA_VERSION,
        "world_digest": world.world_digest,
        "asset_digest": world.asset_digest,
        "alignments": [item.model_dump(mode="json") for item in alignments],
    }
    return AlignmentManifest(
        schema_version=ALIGNMENT_SCHEMA_VERSION,
        world_id=world.world_id,
        world_schema_version=WORLD_SCHEMA_VERSION,
        world_digest=world.world_digest,
        asset_digest=world.asset_digest,
        alignments=alignments,
        manifest_sha256=hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    )


def alignment_matches(
    building: BuildingSpec,
    alignment: BuildingAlignment,
    *,
    assets: tuple[AssetRecord, ...],
    reader: BundleReader,
    generator: GeneratorIdentity,
) -> None:
    """Check one declared building against one alignment record.

    Raises on any field mismatch: building id, transform, generator,
    source/rendering/collision identity, geometry digest, or the
    per-building alignment digest itself.
    """
    if alignment.building_id != building.building_id:
        raise ValueError(
            f"alignment names building {alignment.building_id!r}, package "
            f"declares {building.building_id!r}"
        )
    if alignment.transform != SUPPORTED_TRANSFORM:
        raise ValueError(
            f"alignment transform {alignment.transform!r} is not the "
            f"supported convention {SUPPORTED_TRANSFORM!r}"
        )
    if alignment.generator != generator:
        raise ValueError(
            f"building {building.building_id!r} alignment generator does "
            "not match the declared generator identity"
        )
    source, rendering, collision = _building_assets(building, assets)
    for label, asset in (
        ("source asset", source),
        ("rendering asset", rendering),
        ("collision asset", collision),
    ):
        _verify_asset_bytes(
            reader,
            asset,
            label=f"building {building.building_id!r} {label}",
        )
    if alignment.source_asset != source.artifact:
        raise ValueError(
            f"building {building.building_id!r} source asset does not match "
            "its alignment record"
        )
    if alignment.rendering_artifact != rendering.artifact:
        raise ValueError(
            f"building {building.building_id!r} rendering artifact does not "
            "match its alignment record"
        )
    if alignment.collision_artifact != collision.artifact:
        raise ValueError(
            f"building {building.building_id!r} collision artifact does not "
            "match its alignment record"
        )
    if alignment.geometry_json_sha256 != geometry_json_sha256(building):
        raise ValueError(
            f"building {building.building_id!r} geometry digest does not "
            "match its alignment record"
        )
    body = alignment_content(
        building,
        assets=assets,
        transform=alignment.transform,
        generator=alignment.generator,
    )
    if (
        alignment.alignment_sha256
        != hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    ):
        raise ValueError(
            f"alignment digest for building {building.building_id!r} does "
            "not match its recorded content"
        )


def verify_alignment(
    manifest: AlignmentManifest,
    *,
    reader: BundleReader,
    world: WorldPackage,
    generator: GeneratorIdentity,
) -> None:
    """Fail closed unless the manifest binds this world's declared buildings.

    Raises on: a manifest naming a different `world_id`, missing buildings,
    extra alignments, duplicate alignments, a broken manifest digest, or
    any per-building mismatch listed in `alignment_matches`.
    """
    if manifest.world_id != world.world_id:
        raise ValueError(
            f"alignment manifest binds world {manifest.world_id!r}, not the "
            f"declared world {world.world_id!r}"
        )
    body = {
        "schema_version": manifest.schema_version,
        "world_id": manifest.world_id,
        "world_schema_version": manifest.world_schema_version,
        "world_digest": manifest.world_digest,
        "asset_digest": manifest.asset_digest,
        "alignments": [item.model_dump(mode="json") for item in manifest.alignments],
    }
    if (
        manifest.manifest_sha256
        != hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    ):
        raise ValueError("alignment manifest digest does not match its content")
    if manifest.world_schema_version != WORLD_SCHEMA_VERSION:
        raise ValueError("alignment manifest names a different world schema")
    if (
        manifest.world_digest != world.world_digest
        or manifest.asset_digest != world.asset_digest
    ):
        raise ValueError("alignment manifest does not bind the exact WorldPackage")
    expected_ids = [building.building_id for building in world.buildings]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("building ids must be unique for alignment")
    manifest_by_id = {
        alignment.building_id: alignment for alignment in manifest.alignments
    }
    if len(manifest_by_id) != len(manifest.alignments):
        raise ValueError("alignment manifest contains duplicate buildings")
    if set(manifest_by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(manifest_by_id))
        extra = sorted(set(manifest_by_id) - set(expected_ids))
        raise ValueError(
            "alignment manifest does not cover the declared buildings: "
            f"missing={missing}, extra={extra}"
        )
    for building in world.buildings:
        alignment_matches(
            building,
            manifest_by_id[building.building_id],
            assets=world.assets,
            reader=reader,
            generator=generator,
        )


__all__ = [
    "ALIGNMENT_SCHEMA_VERSION",
    "AlignmentManifest",
    "BuildingAlignment",
    "GeneratorIdentity",
    "SUPPORTED_TRANSFORM",
    "alignment_manifest",
    "alignment_matches",
    "geometry_json_sha256",
    "verify_alignment",
]
