"""Focused tests for the strict `aero-bench.world/v2` world contracts."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from aero_bench.world.contracts import (
    ArtifactSelector,
    AssetRecord,
    EntitySpec,
    NetworkNodeBinding,
    PrecisionMetadata,
    PublicLayer,
    WorldFrame,
    WorldPackage,
    WorldPackageContent,
    WorldPose,
    asset_digest_value,
    world_digest_value,
    world_package,
)
from tests.world.support import (
    DIGEST_GEOID,
    DIGEST_LICENSE,
    asset_record,
    assets,
    building,
    content_kwargs,
    expected_public_asset,
    semantic_target,
    uav_entity,
    world_frame,
    world_package_raw,
    world_package_valid,
)


def selector(
    *,
    asset_id: str = "asset.one",
    selector_value: str = "bundle/path.bin",
    sha256: str = DIGEST_GEOID,
) -> ArtifactSelector:
    return ArtifactSelector(
        artifact_id=asset_id,
        selector=selector_value,
        sha256=sha256,
    )


def content_payload() -> dict[str, Any]:
    payload = world_package_raw()
    payload.pop("asset_digest")
    payload.pop("world_digest")
    return payload


def test_valid_v2_package_round_trips_with_self_verified_digests() -> None:
    package = world_package_valid()
    assert package.schema_version == "aero-bench.world/v2"
    assert package.frame.origin_height_reference == "wgs84_ellipsoid"
    assert package.expected_public_assets[0].asset_id == "public.report"
    assert WorldPackage.model_validate(package.model_dump(mode="json")) == package


def test_v1_payload_and_removed_fields_are_rejected_without_legacy_parser() -> None:
    payload = world_package_raw()
    payload["schema_version"] = "aero-bench.world/v1"
    with pytest.raises(ValidationError):
        WorldPackage.model_validate(payload)

    old_shape = {
        "schema_version": "aero-bench.world/v1",
        "world_id": "world.city-demo",
        "frame": {
            "datum": "WGS84",
            "semi_major_axis_m": 6_378_137.0,
            "inverse_flattening": 298.257_223_563,
            "transform": "wgs84_geodetic_to_enu_v1",
            "origin": {
                "frame_id": "WGS84",
                "longitude_deg": 116.397,
                "latitude_deg": 39.916,
                "altitude_m": 43.0,
            },
        },
        "base_layers": (),
        "buildings": (),
        "weather": (),
        "entities": (),
        "layers": (),
    }
    with pytest.raises(ValidationError):
        WorldPackageContent.model_validate(old_shape)

    legacy_entity = uav_entity().model_dump(mode="json")
    legacy_entity.pop("provider_id")
    legacy_entity["owner_provider_id"] = "flight"
    legacy_entity["authority_provider_id"] = "flight"
    with pytest.raises(ValidationError):
        EntitySpec.model_validate(legacy_entity)


def test_no_hidden_defaults_extra_fields_and_constructor_omissions() -> None:
    package = world_package_valid()
    raw = package.model_dump(mode="json")
    assert raw["sumo"]["object_bindings"]
    assert "position" not in raw["network"]["node_bindings"][0]

    base_content_payload = content_payload()
    for key in ("assets", "provider_requirements", "roads", "network"):
        missing = dict(base_content_payload)
        missing.pop(key)
        with pytest.raises(ValidationError):
            WorldPackageContent.model_validate(missing)

    missing_nested = dict(base_content_payload)
    missing_nested["sumo"] = dict(missing_nested["sumo"])
    missing_nested["sumo"].pop("object_bindings")
    with pytest.raises(ValidationError):
        WorldPackageContent.model_validate(missing_nested)

    with pytest.raises(ValidationError):
        WorldFrame.model_validate(
            {
                **world_frame().model_dump(mode="json"),
                "extra_field": "forbidden",
            }
        )

    kwargs = content_kwargs()
    del kwargs["assets"]
    with pytest.raises(TypeError):
        world_package(**kwargs)  # type: ignore[arg-type]


def test_selector_rejects_urls_path_escapes_placeholders_and_unknown_fields() -> None:
    for rejected in (
        "https://example.com/world.bin",
        "/abs/path.bin",
        "a/../b",
        "a//b",
        "a\\b",
        " bundle/path.bin ",
    ):
        with pytest.raises(ValidationError):
            selector(selector_value=rejected)

    with pytest.raises(ValidationError):
        selector(sha256="0" * 64)

    with pytest.raises(ValidationError):
        ArtifactSelector.model_validate(
            {
                **selector(sha256=DIGEST_LICENSE).model_dump(mode="json"),
                "url": "https://example.com",
            }
        )


def test_origin_source_frames_precision_and_quaternions_are_strict() -> None:
    with pytest.raises(ValidationError):
        WorldFrame.model_validate(
            {
                **world_frame().model_dump(mode="json"),
                "origin_height_reference": "amsl",
            }
        )

    bad_sumo_config = next(asset for asset in assets() if asset.asset_role == "sumo_config").model_dump(mode="json")
    bad_sumo_config["source_frame"] = "WGS84"
    with pytest.raises(ValidationError, match="sumo_config assets must use"):
        AssetRecord.model_validate(bad_sumo_config)

    with pytest.raises(ValidationError, match="unit length"):
        WorldPose(
            frame_id="ENU",
            east_m=0.0,
            north_m=0.0,
            up_m=0.0,
            qw=0.5,
            qx=0.0,
            qy=0.0,
            qz=0.0,
        )

    with pytest.raises(ValidationError, match="requires absolute_tolerance"):
        PrecisionMetadata(
            quantity="height",
            kind="absolute_tolerance",
            unit="m",
            exact_bytes=1,
            absolute_tolerance=None,
        )


def test_asset_digest_is_order_independent_and_world_digest_is_ordered() -> None:
    package = world_package_valid()
    reordered_payload = content_payload()
    reordered_payload["assets"] = list(reversed(reordered_payload["assets"]))
    reordered = WorldPackageContent.model_validate(reordered_payload)
    assert asset_digest_value(reordered) == package.asset_digest
    assert world_digest_value(reordered) != package.world_digest

    with pytest.raises(ValidationError, match="world_digest"):
        WorldPackage.model_validate(
            {
                **reordered_payload,
                "asset_digest": package.asset_digest,
                "world_digest": package.world_digest,
            }
        )

    asset_tamper = world_package_raw()
    asset_tamper["assets"][0]["byte_size"] += 1
    with pytest.raises(ValidationError, match="asset_digest"):
        WorldPackage.model_validate(asset_tamper)

    placeholder = world_package_raw()
    placeholder["asset_digest"] = "0" * 64
    with pytest.raises(ValidationError, match="placeholder"):
        WorldPackage.model_validate(placeholder)


def test_expected_public_assets_are_runtime_outputs_not_source_assets() -> None:
    payload = content_payload()
    source_asset_ids = {asset["artifact"]["artifact_id"] for asset in payload["assets"]}
    assert "public.report" not in source_asset_ids
    assert payload["expected_public_assets"][0]["media_type"] == "application/json"
    assert payload["expected_public_assets"][0]["units"]
    assert payload["expected_public_assets"][0]["precision"]

    colliding_source_asset = asset_record(
        asset_id="public.report",
        selector="world/public/report.json",
        sha256="13" * 32,
        asset_role="other",
        media_type="application/json",
        visibility="public",
        source_frame="non_spatial",
        byte_size=256,
    ).model_dump(mode="json")
    payload["assets"].append(colliding_source_asset)
    with pytest.raises(
        ValidationError,
        match="expected_public_assets must not reuse source asset manifest IDs",
    ):
        WorldPackageContent.model_validate(payload)


def test_visibility_and_closed_layer_rules_are_enforced() -> None:
    payload = content_payload()
    layer_asset = next(asset for asset in payload["assets"] if asset["artifact"]["artifact_id"] == "layer.buildings")
    layer_asset["visibility"] = "private"
    layer_asset["audiences"] = [
        {"audience_kind": "provider", "audience_id": "flight"}
    ]
    with pytest.raises(
        ValidationError, match="public layers must reference public assets"
    ):
        WorldPackageContent.model_validate(payload)

    assert (
        PublicLayer(
            layer_id="layer.weather",
            kind="weather",
            asset_id="layer.buildings",
            visibility="public",
            default_visible=False,
        ).kind
        == "weather"
    )
    with pytest.raises(ValidationError):
        PublicLayer(
            layer_id="layer.ads",
            kind="advertisements",  # type: ignore[arg-type]
            asset_id="layer.buildings",
            visibility="public",
            default_visible=True,
        )


def test_building_target_region_and_spatial_rules_fail_closed() -> None:
    bad_building = content_payload()
    bad_building["buildings"] = [
        {**building().model_dump(mode="json"), "entity_id": "uav.alpha"}
    ]
    with pytest.raises(ValidationError, match="building entity_id"):
        WorldPackageContent.model_validate(bad_building)

    bad_target = content_payload()
    bad_target["semantic_targets"] = [
        {**semantic_target().model_dump(mode="json"), "parent_entity_id": "uav.alpha"}
    ]
    with pytest.raises(ValidationError, match="static entity"):
        WorldPackageContent.model_validate(bad_target)

    shadow_region = content_payload()
    shadow_region["regions"] = [
        {
            **shadow_region["regions"][0],
            "region_id": "region.shadow",
            "kind": "communications_shadow",
            "communications_shadow_attenuation_db": None,
        }
    ]
    with pytest.raises(ValidationError, match="attenuation_db"):
        WorldPackageContent.model_validate(shadow_region)

    no_fly_region = content_payload()
    no_fly_region["regions"][0]["communications_shadow_attenuation_db"] = 10.0
    with pytest.raises(ValidationError, match="must not declare"):
        WorldPackageContent.model_validate(no_fly_region)

    out_of_bounds_road = content_payload()
    out_of_bounds_road["roads"][0]["centerline_enu_m"][1]["east_m"] = 900.0
    with pytest.raises(ValidationError, match="spatial_extent"):
        WorldPackageContent.model_validate(out_of_bounds_road)


def test_launch_sites_allow_multiple_uavs_without_pose_equality_constraints() -> None:
    payload = content_payload()
    payload["entities"].append(
        uav_entity(entity_id="uav.bravo", east_m=80.0).model_dump(mode="json")
    )
    payload["launch_sites"].append(
        {
            **payload["launch_sites"][0],
            "launch_site_id": "launch.bravo",
            "allowed_uav_entity_ids": ["uav.alpha", "uav.bravo"],
            "pose": {
                **payload["launch_sites"][0]["pose"],
                "east_m": 35.0,
                "north_m": 30.0,
            },
        }
    )
    validated = WorldPackageContent.model_validate(payload)
    assert len(validated.launch_sites) == 2
    assert validated.launch_sites[1].allowed_uav_entity_ids == (
        "uav.alpha",
        "uav.bravo",
    )
    assert validated.launch_sites[1].primary_uav_entity_id == "uav.alpha"

    bad_launch = content_payload()
    bad_launch["launch_sites"][0]["primary_uav_entity_id"] = "static.pad"
    bad_launch["launch_sites"][0]["allowed_uav_entity_ids"] = ["static.pad"]
    with pytest.raises(ValidationError, match="dynamic uav"):
        WorldPackageContent.model_validate(bad_launch)

    bad_primary = content_payload()
    bad_primary["launch_sites"][0]["primary_uav_entity_id"] = "uav.bravo"
    with pytest.raises(ValidationError, match="primary_uav_entity_id"):
        WorldPackageContent.model_validate(bad_primary)


def test_provider_requirements_are_unique_sorted_and_role_consistent() -> None:
    duplicate_provider = content_payload()
    duplicate_provider["provider_requirements"].append(
        duplicate_provider["provider_requirements"][0]
    )
    with pytest.raises(ValidationError, match="provider requirement provider_id"):
        WorldPackageContent.model_validate(duplicate_provider)

    unsorted_provider = content_payload()
    unsorted_provider["provider_requirements"] = list(
        reversed(unsorted_provider["provider_requirements"])
    )
    with pytest.raises(ValidationError, match="sorted by provider_id"):
        WorldPackageContent.model_validate(unsorted_provider)

    duplicate_capability = content_payload()
    duplicate_capability["provider_requirements"][0]["required_capability_ids"] = [
        "gazebo.physics",
        "gazebo.physics",
    ]
    with pytest.raises(ValidationError, match="capability_id"):
        WorldPackageContent.model_validate(duplicate_capability)

    missing_provider = content_payload()
    missing_provider["entities"][0]["provider_id"] = "missing.motion"
    with pytest.raises(ValidationError, match="provider_requirements"):
        WorldPackageContent.model_validate(missing_provider)

    wrong_role = content_payload()
    wrong_role["sensors"][0]["provider_id"] = "flight"
    with pytest.raises(ValidationError, match="provider role sensor"):
        WorldPackageContent.model_validate(wrong_role)

    removed_mission_provider_field = content_payload()
    removed_mission_provider_field["mission_provider_id"] = "flight"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorldPackageContent.model_validate(removed_mission_provider_field)


def test_engine_sumo_and_network_bindings_are_explicit_and_consistent() -> None:
    duplicate_sumo_binding = content_payload()
    duplicate_sumo_binding["engine_frame_bindings"].append(
        {
            **duplicate_sumo_binding["engine_frame_bindings"][1],
            "binding_id": "sumo.net-enu-extra",
        }
    )
    with pytest.raises(
        ValidationError,
        match="enabled SUMO requires exactly one frame binding with source_frame_id 'sumo_net' and target_frame_id 'ENU'",
    ):
        WorldPackageContent.model_validate(duplicate_sumo_binding)

    missing_gazebo_binding = content_payload()
    missing_gazebo_binding["engine_frame_bindings"] = [
        missing_gazebo_binding["engine_frame_bindings"][1]
    ]
    with pytest.raises(ValidationError, match="Gazebo"):
        WorldPackageContent.model_validate(missing_gazebo_binding)

    bad_sumo = content_payload()
    bad_sumo["sumo"]["object_bindings"][0]["entity_id"] = "uav.alpha"
    with pytest.raises(
        ValidationError, match="SUMO object bindings must cover exactly"
    ):
        WorldPackageContent.model_validate(bad_sumo)

    bad_sumo_provider = content_payload()
    bad_sumo_provider["sumo"]["provider_id"] = "flight"
    with pytest.raises(ValidationError, match="provider role traffic"):
        WorldPackageContent.model_validate(bad_sumo_provider)

    mismatched_sumo_binding_owner = content_payload()
    mismatched_sumo_binding_owner["provider_requirements"].append(
        {
            "provider_id": "traffic.alt",
            "roles": ["traffic"],
            "required_capability_ids": ["sumo.traffic.alt"],
        }
    )
    mismatched_sumo_binding_owner["provider_requirements"].sort(
        key=lambda requirement: requirement["provider_id"]
    )
    mismatched_sumo_binding_owner["engine_frame_bindings"][1]["provider_id"] = (
        "traffic.alt"
    )
    with pytest.raises(ValidationError, match="owned by sumo.provider_id"):
        WorldPackageContent.model_validate(mismatched_sumo_binding_owner)

    bad_sensor_provider = content_payload()
    bad_sensor_provider["sensors"][0]["provider_id"] = "missing.sensor"
    with pytest.raises(ValidationError, match="sensor camera.front provider_id"):
        WorldPackageContent.model_validate(bad_sensor_provider)

    bad_network_provider = content_payload()
    bad_network_provider["network"]["provider_id"] = "traffic"
    with pytest.raises(ValidationError, match="provider role wireless_network"):
        WorldPackageContent.model_validate(bad_network_provider)

    dangling_radio_profile = content_payload()
    dangling_radio_profile["network"]["node_bindings"][0]["radio_profile_id"] = (
        "radio.missing"
    )
    with pytest.raises(ValidationError, match="declared radio profiles"):
        WorldPackageContent.model_validate(dangling_radio_profile)

    mismatched_radio_provider = content_payload()
    mismatched_radio_provider["network"]["radio_profiles"][0]["provider_id"] = "traffic"
    with pytest.raises(ValidationError, match="network.provider_id"):
        WorldPackageContent.model_validate(mismatched_radio_provider)

    with pytest.raises(ValidationError):
        NetworkNodeBinding.model_validate(
            {
                "node_id": "node.legacy",
                "entity_id": "uav.alpha",
                "endpoint_id": "endpoint.legacy",
                "radio_profile_id": "radio.wifi-main",
                "position": {"east_m": 0.0, "north_m": 0.0},
            }
        )


def test_vertical_checks_only_compare_enu_up_altitudes() -> None:
    unlike_height = content_payload()
    unlike_height["buildings"][0]["geometry"]["base_altitude_m"] = -5_000.0
    unlike_height["buildings"][0]["geometry"]["top_altitude_m"] = 20_000.0
    unlike_height["regions"][0]["geometry"]["min_altitude_m"] = -5_000.0
    unlike_height["regions"][0]["geometry"]["max_altitude_m"] = 20_000.0
    validated = WorldPackageContent.model_validate(unlike_height)
    assert validated.buildings[0].geometry.height_reference == "amsl"
    assert validated.regions[0].geometry.height_reference == "amsl"

    enu_up_building = content_payload()
    enu_up_building["buildings"][0]["geometry"]["height_reference"] = "enu_up"
    enu_up_building["buildings"][0]["geometry"]["base_altitude_m"] = -100.0
    enu_up_building["buildings"][0]["geometry"]["top_altitude_m"] = 18.0
    with pytest.raises(ValidationError, match="base_altitude_m"):
        WorldPackageContent.model_validate(enu_up_building)

    enu_up_region = content_payload()
    enu_up_region["regions"][0]["geometry"]["height_reference"] = "enu_up"
    enu_up_region["regions"][0]["geometry"]["min_altitude_m"] = 0.0
    enu_up_region["regions"][0]["geometry"]["max_altitude_m"] = 500.0
    with pytest.raises(ValidationError, match="max_altitude_m"):
        WorldPackageContent.model_validate(enu_up_region)


def test_mission_requirements_and_expected_public_assets_are_cross_validated() -> None:
    payload = content_payload()
    payload["mission_requirements"][2]["expected_public_asset_id"] = None
    with pytest.raises(ValidationError, match="upload_or_buffer mission requirements"):
        WorldPackageContent.model_validate(payload)

    cyclic = content_payload()
    cyclic["mission_requirements"][0]["dependencies"] = ["mission.land"]
    with pytest.raises(ValidationError, match="acyclic"):
        WorldPackageContent.model_validate(cyclic)

    bad_expected = content_payload()
    bad_expected["expected_public_assets"] = [
        {
            **expected_public_asset().model_dump(mode="json"),
            "producer_requirement_id": "mission.observe",
        }
    ]
    with pytest.raises(ValidationError, match="upload_or_buffer mission requirement"):
        WorldPackageContent.model_validate(bad_expected)


def test_models_are_frozen() -> None:
    package = world_package_valid()
    with pytest.raises(ValidationError):
        package.world_id = "mutated.world"  # type: ignore[misc]
