from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.providers.world_scene.config import SoftwareIdentity, WorldSceneConfig


def config(**overrides: object) -> WorldSceneConfig:
    values: dict[str, object] = {
        "schema_version": "aero-bench.world-scene/v2",
        "provider_id": "scene",
        "scene": SoftwareIdentity(version="0.3.0-world-scene.2", commit="b" * 40),
    }
    values.update(overrides)
    return WorldSceneConfig.model_validate(values)


def test_world_scene_config_accepts_only_implementation_identity() -> None:
    resolved = config()
    assert resolved.model_dump(mode="json") == {
        "schema_version": "aero-bench.world-scene/v2",
        "provider_id": "scene",
        "scene": {"version": "0.3.0-world-scene.2", "commit": "b" * 40},
    }


def test_world_scene_config_pins_current_schema_and_scene_identity() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        config(schema_version="aero-bench.world-scene/v1")
    with pytest.raises(ValidationError, match="scene.commit"):
        config(scene={"version": "0.3.0-world-scene.2", "commit": "not-a-commit"})


@pytest.mark.parametrize(
    "authority_field",
    (
        "world_schema_version",
        "world_id",
        "world_digest",
        "asset_digest",
        "world_package",
        "mission_provider_id",
        "static_scene",
        "world_scene",
    ),
)
def test_world_scene_config_rejects_static_or_mission_authority(
    authority_field: str,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        config(**{authority_field: "forbidden"})


@pytest.mark.parametrize(
    "deployment_field",
    ("endpoint", "runtime_image", "protocol_version", "command_timeout_ms"),
)
def test_world_scene_config_carries_no_deployment_identity(
    deployment_field: str,
) -> None:
    """Deployment identity lives on the manifest/endpoint, never in the config."""

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        config(**{deployment_field: "forbidden"})
