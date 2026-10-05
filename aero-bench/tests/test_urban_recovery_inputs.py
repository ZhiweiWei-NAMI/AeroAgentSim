"""Policy input closure tests; fixture bundles are not runnable demonstrations."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import AgentSpec, TaskSpec
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.integration import (
    UrbanRecoveryResolutionError, _validate_participant_inputs,
)
from aero_bench.tasks.urban_recovery_demo.provenance import URBAN_PROVENANCE_ASSETS
from tools.build_urban_recovery_demo import _demo_package, _schemas, _task_environment, write_json
from tests.test_urban_recovery_participant import _participant


def _inputs(root):
    source = root / "unit-only-source.json"
    source.write_bytes(b'{"unit_only":true}\n')
    package = _demo_package(root, source)
    schemas = _schemas(root)
    for _, relative in URBAN_PROVENANCE_ASSETS:
        write_json(root, relative, {"unit_only": True})
    configs = {key: write_json(root, f"configs/{key}.json", {"unit_only": True}) for key in ("px4", "ns3", "sumo")}
    image_names = ("groundstation.rule", "uav.policy.01", "uav.policy.02", "flight", "network", "traffic", "harness", "verifier")
    images = {key: f"registry.test/{key}@sha256:{index:064x}" for index, key in enumerate(image_names, start=1)}
    task_path, _, agents = _task_environment(
        root=root, package=package, schemas=schemas, configs=configs,
        images=images, revision="a" * 40,
    )
    task = TaskSpec.model_validate(yaml.safe_load(task_path.read_text()))
    return package, task, tuple(AgentSpec.model_validate(yaml.safe_load(path.read_text())) for path in agents)


def test_builder_declares_digest_bound_role_scoped_policy_inputs(tmp_path):
    package, task, agents = _inputs(tmp_path)
    _validate_participant_inputs(reader=BundleReader(tmp_path), task=task, package=package, agents=agents)
    for agent in agents:
        asset_id = f"asset.participant.{agent.agent_id}"
        assert agent.workload.runtime.command == ("run", "--config-asset", asset_id)
        asset = next(item for item in task.assets if item.asset_id == asset_id)
        payload = (tmp_path / asset.file.path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == asset.file.sha256
        assert asset.classification == "public"
        assert {item.role: item.workload_ids for item in asset.audiences} == {
            "agent": (agent.agent_id,), "verifier": (package.verifier_id,),
        }


def test_builder_declares_provenance_as_private_task_inputs(tmp_path):
    package, task, _ = _inputs(tmp_path)
    assets = {asset.asset_id: asset for asset in task.assets}
    for asset_id, relative in URBAN_PROVENANCE_ASSETS:
        asset = assets[asset_id]
        assert asset.file.path == relative
        assert hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest() == asset.file.sha256
        assert asset.classification == "private"
        assert [(audience.role, audience.workload_ids) for audience in asset.audiences] == [
            ("verifier", (package.verifier_id,)),
        ]


def test_tool_schemas_reject_cross_vehicle_and_cross_endpoint_commands(tmp_path):
    package, _, agents = _inputs(tmp_path)
    reader = BundleReader(tmp_path)
    roles = {item.agent_id: item for item in package.roles}
    defaults = {
        "latitude_deg": 31.2304, "longitude_deg": 121.4737, "altitude_amsl_m": 65.0,
        "altitude_m": 45.0, "yaw_deg": 0.0, "work_order_id": "urban.test", "message_id": "message.test",
        "payload_base64": "eA==", "payload_sha256": hashlib.sha256(b"x").hexdigest(),
        "traffic_class": "best_effort", "priority": 0, "reliability": "best_effort",
    }
    for agent in agents:
        role = roles[agent.agent_id]
        for grant in agent.tools:
            schema = reader.validate_schema(grant.request_schema)
            values = {
                **defaults, "vehicle_id": role.vehicle_id, "source": role.endpoint_id,
                "destination": "endpoint.uav.01" if role.role == "groundstation" else "endpoint.groundstation",
            }
            request = {key: values[key] for key in schema["properties"]}
            validator = Draft202012Validator(schema)
            validator.validate(request)
            fields = ("source", "destination") if grant.tool_id == "network.send" else ("vehicle_id",)
            for field in fields:
                with pytest.raises(ValidationError):
                    validator.validate({**request, field: "foreign.owner"})


def test_resolver_rejects_a_rehashed_schema_that_drops_vehicle_ownership(tmp_path):
    package, task, agents = _inputs(tmp_path)
    agent = agents[1]
    grant = next(item for item in agent.tools if item.tool_id == "flight.arm")
    schema_path = tmp_path / grant.request_schema.path
    schema = json.loads(schema_path.read_bytes())
    del schema["properties"]["vehicle_id"]["const"]
    payload = canonical_json_bytes(schema) + b"\n"
    schema_path.write_bytes(payload)
    document = agent.model_dump(mode="json")
    next(item for item in document["tools"] if item["tool_id"] == "flight.arm")["request_schema"]["sha256"] = hashlib.sha256(payload).hexdigest()
    agents = (agents[0], AgentSpec.model_validate(document), agents[2])
    with pytest.raises(UrbanRecoveryResolutionError, match="owned UAV"):
        _validate_participant_inputs(reader=BundleReader(tmp_path), task=task, package=package, agents=agents)


def test_declared_implementation_versions_match_the_container_build_labels(tmp_path):
    import re

    _, task, _ = _inputs(tmp_path)
    environment = yaml.safe_load((tmp_path / "environment/environment.yaml").read_text())
    root = Path(__file__).resolve().parents[1]
    workloads = {
        "harness": environment["harness"]["implementation"],
        "urban-recovery-verifier": task.verifier.workload.implementation.model_dump(mode="json"),
    }
    adapter_dirs = {"px4.gazebo": "px4-gazebo", "ns3.rpc": "ns3", "sumo.traci": "sumo"}
    for provider in environment["providers"]:
        workloads[adapter_dirs[provider["adapter"]]] = provider["workload"]["implementation"]
    for directory, identity in workloads.items():
        dockerfile = (root / "containers" / directory / "Dockerfile").read_text()
        assert re.search(r'io.aero-bench.component="([^"]+)"', dockerfile).group(1) == identity["component_id"]
        assert re.search(r'org.opencontainers.image.version="([^"]+)"', dockerfile).group(1) == identity["version"]


def test_builder_binds_verifier_schema_and_both_declared_outputs(tmp_path):
    package, task, _ = _inputs(tmp_path)
    reader = BundleReader(tmp_path)
    config = reader.validate_schema_bound_file(task.verifier.config)
    assert config["physics_step_ns"] == 4_000_000
    assert config["required_channels"] == list(package.required_channels)
    outputs = {item.artifact_type: item for item in task.verifier.output_artifacts}
    assert set(outputs) == {"verification.report", "verification.event-segment"}
    assert outputs["verification.event-segment"].relative_path == "verifier/events.jsonl"
    assert all(item.producer_id == package.verifier_id and item.visibility == "public" for item in outputs.values())


def test_mutated_policy_bytes_cannot_keep_the_old_run_input_digest(tmp_path):
    package, task, agents = _inputs(tmp_path)
    path = tmp_path / task.assets[0].file.path
    document = json.loads(path.read_bytes())
    document["maximum_retries"] = 0
    path.write_bytes(canonical_json_bytes(document) + b"\n")
    with pytest.raises(UrbanRecoveryResolutionError, match="config bytes"):
        _validate_participant_inputs(reader=BundleReader(tmp_path), task=task, package=package, agents=agents)


@pytest.mark.parametrize("change", [
    {"maximum_retries": 0}, {"origin_longitude_deg": 2.35},
    {"vehicle_agent_ids": {"uav.01": "uav.policy.02", "uav.02": "uav.policy.01"}},
])
def test_rehashed_config_still_must_bind_package_policy_and_identities(tmp_path, change):
    package, task, agents = _inputs(tmp_path)
    asset = task.assets[0]
    document = json.loads((tmp_path / asset.file.path).read_bytes())
    payload = canonical_json_bytes({**document, **change}) + b"\n"
    (tmp_path / asset.file.path).write_bytes(payload)
    asset_document = asset.model_dump(mode="json")
    asset_document["file"]["sha256"] = hashlib.sha256(payload).hexdigest()
    task_document = task.model_dump(mode="json")
    task_document["assets"][0] = asset_document
    task = TaskSpec.model_validate(task_document)
    with pytest.raises(UrbanRecoveryResolutionError, match="differs"):
        _validate_participant_inputs(reader=BundleReader(tmp_path), task=task, package=package, agents=agents)


def test_agent_cannot_mount_another_participants_config(tmp_path):
    package, task, agents = _inputs(tmp_path)
    document = task.model_dump(mode="json")
    document["assets"][0]["audiences"][0]["workload_ids"].append("uav.policy.01")
    with pytest.raises(UrbanRecoveryResolutionError, match="audience"):
        _validate_participant_inputs(
            reader=BundleReader(tmp_path), task=TaskSpec.model_validate(document), package=package, agents=agents,
        )


def _entrypoint():
    path = Path(__file__).resolve().parents[1] / "containers/urban-recovery-participant/entrypoint.py"
    spec = importlib.util.spec_from_file_location("urban_participant_entrypoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_entrypoint_reads_only_authorized_digest_checked_inputs(monkeypatch):
    entrypoint = _entrypoint()
    context = Mock(agent_id="groundstation.rule")
    context.asset_bytes.return_value = canonical_json_bytes(_participant().config.model_dump(mode="json")) + b"\n"
    monkeypatch.setattr(entrypoint.AgentContext, "from_environment", lambda: context)
    participant = Mock()
    constructor = Mock(return_value=participant)
    monkeypatch.setattr(entrypoint, "UrbanParticipant", constructor)
    assert entrypoint.main(["run", "--config-asset", "asset.participant.groundstation.rule", "--timeout-seconds", "19"]) == 0
    context.instruction_bytes.assert_called_once()
    context.asset_bytes.assert_called_once_with("asset.participant.groundstation.rule")
    participant.run.assert_called_once_with(timeout_seconds=19)


def test_entrypoint_cannot_open_arbitrary_config_paths(monkeypatch, capsys):
    entrypoint = _entrypoint()
    context = Mock(agent_id="uav.policy.01")
    monkeypatch.setattr(entrypoint.AgentContext, "from_environment", lambda: context)
    assert entrypoint.main(["run", "--config-asset", "../../verifier/truth.json"]) == 2
    context.asset_bytes.assert_not_called()
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED"
