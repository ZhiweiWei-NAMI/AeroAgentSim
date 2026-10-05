"""WorldPackage v2 building alignment and byte-binding tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.alignment import (
    ALIGNMENT_SCHEMA_VERSION,
    AlignmentManifest,
    BuildingAlignment,
    GeneratorIdentity,
    alignment_content,
    alignment_manifest,
    geometry_json_sha256,
    verify_alignment,
)
from aero_bench.world.contracts import ArtifactSelector, AssetRecord, WorldPackage
from tests.world.support import materialize_world_package


GENERATOR = GeneratorIdentity(
    generator_id="mesh.pipeline",
    version="2.0.0",
    source_revision="a" * 40,
)


def _manifest_body(
    manifest: AlignmentManifest,
    alignments: tuple[BuildingAlignment, ...],
) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "world_id": manifest.world_id,
        "world_schema_version": manifest.world_schema_version,
        "world_digest": manifest.world_digest,
        "asset_digest": manifest.asset_digest,
        "alignments": [alignment.model_dump(mode="json") for alignment in alignments],
    }


def _resign(
    manifest: AlignmentManifest,
    alignments: tuple[BuildingAlignment, ...],
) -> AlignmentManifest:
    body = _manifest_body(manifest, alignments)
    return AlignmentManifest.model_validate(
        {
            **body,
            "manifest_sha256": hashlib.sha256(
                canonical_json_bytes(body)
            ).hexdigest(),
        }
    )


def _replace_asset(
    assets: tuple[AssetRecord, ...],
    asset_id: str,
    replacement: AssetRecord,
) -> tuple[AssetRecord, ...]:
    return tuple(
        replacement if item.artifact.artifact_id == asset_id else item
        for item in assets
    )


def _world_bundle(tmp_path: Path):
    return materialize_world_package(tmp_path / "bundle")


def test_alignment_resolves_v2_building_ids_through_exact_asset_manifest(
    tmp_path: Path,
) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    building = world.buildings[0]
    alignment = manifest.alignments[0]
    assets = {asset.artifact.artifact_id: asset for asset in world.assets}

    assert manifest.schema_version == ALIGNMENT_SCHEMA_VERSION
    assert manifest.world_schema_version == "aero-bench.world/v2"
    assert manifest.world_digest == world.world_digest
    assert manifest.asset_digest == world.asset_digest
    assert alignment.source_asset == assets[building.source_asset_id].artifact
    assert alignment.rendering_artifact == assets[building.render_asset_id].artifact
    assert alignment.collision_artifact == assets[building.collision_asset_id].artifact
    assert alignment.geometry_json_sha256 == geometry_json_sha256(building)
    verify_alignment(manifest, reader=reader, world=world, generator=GENERATOR)


def test_render_and_collision_are_independent_pinned_outputs(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path)
    alignment = alignment_manifest(
        reader=reader,
        world=world,
        generator=GENERATOR,
    ).alignments[0]
    assert alignment.source_asset.sha256 != alignment.rendering_artifact.sha256
    assert alignment.source_asset.sha256 != alignment.collision_artifact.sha256
    assert alignment.rendering_artifact.sha256 != alignment.collision_artifact.sha256


def test_alignment_content_binds_resolved_assets_geometry_and_generator(
    tmp_path: Path,
) -> None:
    _, _, world = _world_bundle(tmp_path)
    building = world.buildings[0]
    content = alignment_content(
        building,
        assets=world.assets,
        transform="wgs84_geodetic_to_enu_v1",
        generator=GENERATOR,
    )
    assets = {asset.artifact.artifact_id: asset for asset in world.assets}
    assert content["source_asset"] == assets[
        building.source_asset_id
    ].artifact.model_dump(mode="json")
    assert content["rendering_artifact"] == assets[
        building.render_asset_id
    ].artifact.model_dump(mode="json")
    assert content["collision_artifact"] == assets[
        building.collision_asset_id
    ].artifact.model_dump(mode="json")
    assert content["geometry"] == building.geometry.model_dump(mode="json")
    assert content["generator"] == GENERATOR.model_dump(mode="json")


@pytest.mark.parametrize(
    "asset_id",
    ("building.source", "building.render", "building.collision"),
)
def test_alignment_verifies_source_render_and_collision_bytes(
    tmp_path: Path,
    asset_id: str,
) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    asset = next(item for item in world.assets if item.artifact.artifact_id == asset_id)
    path = reader.root / asset.artifact.selector
    path.write_bytes(path.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_alignment(manifest, reader=reader, world=world, generator=GENERATOR)


def test_alignment_verifies_declared_asset_byte_size(tmp_path: Path) -> None:
    def mutate(kwargs: dict[str, Any], _root: Path) -> None:
        asset = next(
            item
            for item in kwargs["assets"]
            if item.artifact.artifact_id == "building.source"
        )
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "building.source",
            asset.model_copy(update={"byte_size": asset.byte_size + 1}),
        )

    reader, _, world = materialize_world_package(
        tmp_path / "bundle",
        mutate_kwargs=mutate,
    )
    with pytest.raises(ValueError, match="byte_size mismatch"):
        alignment_manifest(reader=reader, world=world, generator=GENERATOR)


def test_manifest_rejects_another_exact_world_package(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path / "first")
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)

    def mutate(kwargs: dict[str, Any], root: Path) -> None:
        asset = next(
            item
            for item in kwargs["assets"]
            if item.artifact.artifact_id == "building.source"
        )
        data = b"another exact source asset\n"
        destination = root / asset.artifact.selector
        destination.write_bytes(data)
        replacement = asset.model_copy(
            update={
                "artifact": ArtifactSelector(
                    artifact_id=asset.artifact.artifact_id,
                    selector=asset.artifact.selector,
                    sha256=hashlib.sha256(data).hexdigest(),
                ),
                "byte_size": len(data),
            }
        )
        kwargs["assets"] = _replace_asset(
            kwargs["assets"],
            "building.source",
            replacement,
        )

    other_reader, _, other_world = materialize_world_package(
        tmp_path / "second" / "bundle",
        mutate_kwargs=mutate,
    )
    with pytest.raises(ValueError, match="exact WorldPackage"):
        verify_alignment(
            manifest,
            reader=other_reader,
            world=other_world,
            generator=GENERATOR,
        )


def test_corrupted_alignment_and_manifest_digests_fail_closed(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    alignment = manifest.alignments[0]
    corrupt_alignment = alignment.model_copy(update={"alignment_sha256": "c" * 64})
    resigned = _resign(manifest, (corrupt_alignment,))
    with pytest.raises(ValueError, match="alignment digest"):
        verify_alignment(resigned, reader=reader, world=world, generator=GENERATOR)

    corrupt_manifest = manifest.model_copy(update={"manifest_sha256": "d" * 64})
    with pytest.raises(ValueError, match="manifest digest"):
        verify_alignment(
            corrupt_manifest,
            reader=reader,
            world=world,
            generator=GENERATOR,
        )


def test_geometry_and_generator_mismatches_fail_closed(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    alignment = manifest.alignments[0]
    wrong_geometry = alignment.model_copy(update={"geometry_json_sha256": "e" * 64})
    with pytest.raises(ValueError, match="geometry digest"):
        verify_alignment(
            _resign(manifest, (wrong_geometry,)),
            reader=reader,
            world=world,
            generator=GENERATOR,
        )

    other_generator = GeneratorIdentity(
        generator_id="mesh.pipeline",
        version="9.9.9",
        source_revision="b" * 40,
    )
    with pytest.raises(ValueError, match="generator"):
        verify_alignment(
            manifest,
            reader=reader,
            world=world,
            generator=other_generator,
        )


def test_duplicate_and_missing_alignment_coverage_fail_closed(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    alignment = manifest.alignments[0]
    with pytest.raises(ValueError, match="duplicate"):
        verify_alignment(
            _resign(manifest, (alignment, alignment)),
            reader=reader,
            world=world,
            generator=GENERATOR,
        )

    extra = alignment.model_copy(update={"building_id": "building.extra"})
    with pytest.raises(ValueError, match="missing|extra"):
        verify_alignment(
            _resign(manifest, (extra,)),
            reader=reader,
            world=world,
            generator=GENERATOR,
        )


def test_v1_alignment_and_missing_world_binding_are_rejected(tmp_path: Path) -> None:
    reader, _, world = _world_bundle(tmp_path)
    manifest = alignment_manifest(reader=reader, world=world, generator=GENERATOR)
    payload = manifest.model_dump(mode="json")
    payload["schema_version"] = "aero-bench.world-alignment/v1"
    with pytest.raises(ValidationError):
        AlignmentManifest.model_validate(payload)

    payload = manifest.model_dump(mode="json")
    payload.pop("world_digest")
    with pytest.raises(ValidationError):
        AlignmentManifest.model_validate(payload)


def test_alignment_requires_at_least_one_building(tmp_path: Path) -> None:
    def mutate(kwargs: dict[str, Any], _root: Path) -> None:
        kwargs["buildings"] = ()

    reader, _, world = materialize_world_package(
        tmp_path / "bundle",
        mutate_kwargs=mutate,
    )
    assert isinstance(world, WorldPackage)
    with pytest.raises(ValueError, match="at least one building"):
        alignment_manifest(reader=reader, world=world, generator=GENERATOR)


def test_generator_identity_rejects_placeholders() -> None:
    with pytest.raises(ValidationError):
        GeneratorIdentity(
            generator_id="mesh.pipeline",
            version="1.0.0",
            source_revision="0" * 40,
        )
