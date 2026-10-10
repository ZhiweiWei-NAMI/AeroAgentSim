from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.tasks.urban_recovery_demo.contracts import DemoTaskPackage
from aero_bench.tasks.urban_recovery_demo.integration import (
    URBAN_REQUIRED_TOOLS, UrbanRecoveryResolutionError, _validate_agent_grants,
)
from tools.build_urban_recovery_demo import _demo_package, _images_lock, _runtime_source_revision, build


def test_urban_package_requires_an_explicit_indexed_replay_declaration(tmp_path) -> None:
    source = tmp_path / "test-only-osm.json"
    source.write_bytes(b'{"test_only":true}\n')
    package = _demo_package(tmp_path, source)
    assert package.replay_mode == "indexed"
    document = package.model_dump(mode="json")
    document.pop("replay_mode")
    with pytest.raises(ValidationError, match="replay_mode"):
        DemoTaskPackage.model_validate(document)
    document["replay_mode"] = "embedded"
    with pytest.raises(ValidationError, match="indexed"):
        DemoTaskPackage.model_validate(document)


def test_builder_never_overwrites_existing_evidence_even_with_a_legacy_force_flag(tmp_path) -> None:
    output = tmp_path / "sealed-release"
    output.mkdir()
    authority = output / "seal.json"
    authority.write_bytes(b"preserve existing bytes")
    with pytest.raises(ValueError, match="cannot be replaced"):
        build(Namespace(output=str(output), force=True))
    assert authority.read_bytes() == b"preserve existing bytes"


def test_builder_cannot_invent_a_placeholder_image_lock() -> None:
    with pytest.raises(ValueError, match="image lock is required"):
        _images_lock(None)


@pytest.mark.parametrize("image", ["registry.test/agent:latest", "registry.test/agent@sha256:" + "0" * 64])
def test_image_lock_rejects_unpinned_or_placeholder_images_before_creating_output(tmp_path, image):
    import json

    lock = tmp_path / "images.json"
    images = {name: "registry.test/component@sha256:" + "a" * 64 for name in ("groundstation.rule", "uav.policy.01", "uav.policy.02", "flight", "network", "traffic", "harness", "verifier")}
    images["uav.policy.01"] = image
    lock.write_text(json.dumps({"images": images}))
    output = tmp_path / "release"
    with pytest.raises(ValueError):
        build(Namespace(output=str(output), images_lock=str(lock), mission_mode="recovery"))
    assert not output.exists()


def test_builder_never_labels_dirty_source_as_the_old_head_revision(monkeypatch):
    monkeypatch.setattr("tools.build_urban_recovery_demo.subprocess.check_output", lambda *_args, **_kwargs: " M aero_bench/agent/runtime.py\n")
    with pytest.raises(ValueError, match="dirty source tree"):
        _runtime_source_revision(None)
    assert _runtime_source_revision("a" * 64) == "a" * 64


@pytest.mark.parametrize("revision", ["0" * 40, "x" * 40, "A" * 64, "abcd"])
def test_builder_rejects_unusable_source_revision(revision):
    with pytest.raises(ValueError, match="git/content revision"):
        _runtime_source_revision(revision)


def test_image_lock_requires_separate_agent_component_identities(tmp_path):
    import json

    names = ("groundstation.rule", "uav.policy.01", "uav.policy.02", "flight", "network", "traffic", "harness", "verifier")
    images = {name: f"registry.test/component@sha256:{index:064x}" for index, name in enumerate(names, start=1)}
    lock = tmp_path / "images.json"
    lock.write_text(json.dumps({"images": images}))
    assert _images_lock(lock) == images
    images["uav.policy.02"] = images["uav.policy.01"]
    lock.write_text(json.dumps({"images": images}))
    with pytest.raises(ValueError, match="distinct image digests"):
        _images_lock(lock)
    images["agent"] = images.pop("groundstation.rule")
    with pytest.raises(ValueError, match="eight workload"):
        lock.write_text(json.dumps({"images": images}))
        _images_lock(lock)


def test_builder_refuses_a_dangling_output_symlink_without_following_it(tmp_path):
    output = tmp_path / "release-link"
    target = tmp_path / "uncreated-target"
    output.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="cannot be replaced"):
        build(Namespace(output=str(output)))
    assert output.is_symlink()
    assert not target.exists()


@pytest.mark.parametrize("role_index", [0, 1, 2])
@pytest.mark.parametrize("mutation", ["extra_observation", "wrong_provider", "extra_tool", "missing_tool", "artifact"])
def test_urban_resolver_rejects_every_grant_outside_the_exact_role(tmp_path, role_index, mutation) -> None:
    source = tmp_path / "test-only-osm.json"
    source.write_bytes(b'{"test_only":true}\n')
    package = _demo_package(tmp_path, source)
    agents = []
    for role in package.roles:
        tools = URBAN_REQUIRED_TOOLS if role.role == "uav" else {"network.send"}
        observations = {role.mailbox_observation_id: "network"}
        if role.role == "uav":
            observations.update({role.telemetry_observation_id: "flight", role.safety_observation_id: "flight"})
        agents.append(SimpleNamespace(
            agent_id=role.agent_id,
            tools=[SimpleNamespace(tool_id=tool, provider_id="network" if tool == "network.send" else "flight") for tool in sorted(tools)],
            observations=[SimpleNamespace(observation_id=key, provider_id=value) for key, value in observations.items()],
            artifact_requirements=[],
        ))
    environment = SimpleNamespace(providers=[SimpleNamespace(provider_id=name) for name in ("flight", "network", "traffic")])
    _validate_agent_grants(package=package, agents=tuple(agents), environment=environment)
    agent = agents[role_index]
    if mutation == "extra_observation":
        agent.observations.append(SimpleNamespace(observation_id="private.truth", provider_id="flight"))
    elif mutation == "wrong_provider":
        agent.observations[0].provider_id = "traffic"
    elif mutation == "extra_tool":
        agent.tools.append(SimpleNamespace(tool_id="private.read", provider_id="flight"))
    elif mutation == "missing_tool":
        agent.tools.pop()
    else:
        agent.artifact_requirements.append(SimpleNamespace(artifact_id="private.truth"))
    with pytest.raises(UrbanRecoveryResolutionError, match="grants"):
        _validate_agent_grants(package=package, agents=tuple(agents), environment=environment)
