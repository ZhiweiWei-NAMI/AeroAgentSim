from __future__ import annotations

from tests.support import fixture_provider_registry

import copy
import hashlib
import json

import pytest
import yaml
from pydantic import ValidationError

from aero_bench.config.models import FileRef, SuiteSpec
from aero_bench.config.resolver import (
    ResolvedRunSpec,
    resolve_suite,
    validate_resolved_run_bundle,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import as_formal_run, build_bundle, digest, resolve_bundle, runtime


def _canonical_run(raw: dict[str, object]) -> dict[str, object]:
    payload = {key: value for key, value in raw.items() if key != "run_id"}
    raw["run_id"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return raw


def _rebind_resolved_task(raw: dict[str, object]) -> dict[str, object]:
    scenario = raw["scenario"]
    scenario["task"]["task_contract_digest"] = hashlib.sha256(
        canonical_json_bytes(
            {
                "task": raw["task"],
                "agents": raw["agents"],
            }
        )
    ).hexdigest()
    scenario_document = {
        key: value for key, value in scenario.items() if key != "scenario_digest"
    }
    scenario["scenario_digest"] = hashlib.sha256(
        canonical_json_bytes(scenario_document)
    ).hexdigest()
    return _canonical_run(raw)


def _write_task_and_repin_suite(bundle, task: dict[str, object]) -> None:
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")


def test_resolver_applies_matrix_values_and_stabilizes_run_ids(tmp_path) -> None:
    bundle = build_bundle(tmp_path)

    first = resolve_bundle(bundle.suite, executor_kind="docker_reference")
    second = resolve_bundle(bundle.suite, executor_kind="docker_reference")

    assert len(first) == 4
    assert {run.execution_scope for run in first} == {"executor_validation"}
    assert [run.run_id for run in first] == [run.run_id for run in second]
    assert {run.environment.clock.step_ns for run in first} == {
        100_000_000,
        200_000_000,
    }
    assert len({run.run_id for run in first}) == 4
    assert {item.artifact_type for item in first[0].artifact_requirements} == {
        "business.state",
        "camera-frame-data",
        "event.log",
        "inspection.detection",
        "inspection.report",
        "network.delivery",
        "observation.metadata",
        "scene.state-history",
        "sensor-frame",
        "theoretical.bounds",
        "trajectory",
        "truth.dataset",
    }
    assert first[0].verification_outputs[0].artifact_type == "verification.report"


def test_resolver_rejects_digest_change(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    bundle.task.write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_driver_config_cannot_alias_a_private_scenario_payload(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    schema_path = bundle.root / "schemas/driver-config.json"
    schema_path.write_text('{"type":"object"}\n')
    driver_id = "test.model-driver"
    agent["driver"] = {
        "driver_id": driver_id,
        "workload": runtime("7", driver_id),
        "config": {
            "file": {
                "path": "private/truth.json",
                "sha256": digest(bundle.root / "private/truth.json"),
            },
            "schema_file": {
                "path": "schemas/driver-config.json",
                "sha256": digest(schema_path),
            },
        },
        "bridge_port": 17731,
        "artifact_requirements": [
            {
                "artifact_id": "artifact." + name,
                "artifact_type": kind,
                "producer_id": driver_id,
                "visibility": "private",
                "relative_path": name + ".json",
                "max_size_bytes": 1024,
                "source_asset_id": None,
            }
            for name, kind in (
                ("model-manifest", "model.session-manifest"),
                ("model-log", "model.interactions"),
            )
        ],
    }
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False))
    suite = yaml.safe_load(bundle.suite.read_text())
    suite["cases"][0]["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False))
    with pytest.raises(ValueError, match="disjoint from control files"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_matrix_target_must_exist(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    suite_raw = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite_raw["cases"][0]["axes"][0]["target"] = "/environment/clock/missing"
    bundle.suite.write_text(
        yaml.safe_dump(suite_raw, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="matrix target does not exist"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_schema_rejects_unknown_fields_and_placeholder_digests(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    suite_raw = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    unknown = copy.deepcopy(suite_raw)
    unknown["legacy_mode"] = True

    with pytest.raises(ValidationError, match="legacy_mode"):
        SuiteSpec.model_validate(unknown)
    with pytest.raises(ValidationError, match="real digest"):
        FileRef.model_validate({"path": "file.txt", "sha256": "0" * 64})


def test_executor_identity_changes_run_id(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    docker_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    kubernetes_run = resolve_bundle(bundle.suite, executor_kind="kubernetes_cluster")[0]

    assert docker_run.run_id != kubernetes_run.run_id


def test_formal_scope_rejects_mechanical_workload_implementations(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["execution_scope"] = "formal_benchmark"
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ValidationError,
        match="formal_benchmark requires every workload implementation kind to be production",
    ):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_resolver_requires_an_explicit_task_package_resolver(tmp_path) -> None:
    bundle = build_bundle(tmp_path)

    with pytest.raises(ValueError, match="no task package resolver"):
        resolve_suite(
            str(bundle.suite),
            executor_kind="docker_reference",
            task_package_resolvers=(),
            provider_registry=fixture_provider_registry(),
        )


def test_provider_config_is_validated_by_its_pinned_schema(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    config_path = bundle.root / "configs/provider.json"
    config_path.write_text('{"profile":"not-nominal"}\n', encoding="utf-8")
    environment_path = bundle.root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    environment["providers"][0]["config"]["file"]["sha256"] = digest(config_path)
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["environment"]["sha256"] = digest(environment_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="schema validation failed"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_environment_requires_one_private_authoritative_event_log(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    environment_path = bundle.root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    environment["harness_artifact_requirements"] = [
        requirement
        for requirement in environment["harness_artifact_requirements"]
        if requirement["artifact_type"] != "event.log"
    ]
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["environment"]["sha256"] = digest(environment_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="authoritative event.log"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_private_asset_cannot_be_granted_to_an_agent(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    task["assets"][0]["audiences"] = [
        {"role": "agent", "workload_ids": ["reference.agent"]}
    ]
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="private assets"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_runtime_image_and_asset_digest_each_change_run_identity(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    baseline = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]

    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    task["verifier"]["workload"]["runtime"]["image"] = (
        "registry.invalid/aero@sha256:" + "9" * 64
    )
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    image_changed = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    assert image_changed.run_id != baseline.run_id

    truth_path = bundle.root / "private/truth.json"
    truth_path.write_text('{"defects":["d-1"]}\n', encoding="utf-8")
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    truth_asset = next(
        asset for asset in task["assets"] if asset["asset_id"] == "inspection.truth"
    )
    truth_asset["file"]["sha256"] = digest(truth_path)
    package_path = bundle.root / "tasks/inspection-package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package_truth = next(
        asset for asset in package["assets"] if asset["asset_id"] == "inspection.truth"
    )
    package_truth["file"]["sha256"] = digest(truth_path)
    package_path.write_text(
        json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    task["package"]["config"]["file"]["sha256"] = digest(package_path)
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    asset_changed = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]

    assert asset_changed.run_id != image_changed.run_id


def test_resolved_run_rejects_forged_derived_fields_and_identity(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]

    wrong_identity = run.model_dump(mode="json")
    wrong_identity["run_id"] = "a" * 64
    with pytest.raises(ValidationError, match="canonical ResolvedRun payload"):
        ResolvedRunSpec.model_validate(wrong_identity)

    missing_artifact = run.model_dump(mode="json")
    missing_artifact["artifact_requirements"].pop()
    with pytest.raises(ValidationError, match="must be derived"):
        ResolvedRunSpec.model_validate(_canonical_run(missing_artifact))


def test_verifier_isolated_by_content_digest_not_image_name(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    raw = run.model_dump(mode="json")
    harness_digest = raw["environment"]["harness"]["runtime"]["image"].rsplit(
        "@sha256:", maxsplit=1
    )[1]
    raw["task"]["verifier"]["workload"]["runtime"]["image"] = (
        f"another.invalid/verifier@sha256:{harness_digest}"
    )

    with pytest.raises(ValidationError, match="image digest must be distinct"):
        ResolvedRunSpec.model_validate(_rebind_resolved_task(raw))


def test_bundle_evidence_asset_is_exclusively_verifier_private(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    raw = run.model_dump(mode="json")
    truth = next(
        asset
        for asset in raw["task"]["assets"]
        if asset["asset_id"] == "inspection.truth"
    )
    truth["audiences"].append({"role": "provider", "workload_ids": ["observation"]})

    with pytest.raises(ValidationError, match="exclusively visible"):
        ResolvedRunSpec.model_validate(_rebind_resolved_task(raw))


def test_materialization_boundary_recomputes_task_feasibility(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    raw = run.model_dump(mode="json")
    first_bound = raw["feasibility"]["bounds"][0]
    first_bound["value"] = float(first_bound["value"]) + 1.0
    forged = ResolvedRunSpec.model_validate(_canonical_run(raw))

    with pytest.raises(ValueError, match="does not match the pinned task package"):
        validate_resolved_run_bundle(
            forged,
            bundle_root=bundle.root,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=fixture_provider_registry(),
        )


def test_materialization_boundary_replays_the_pinned_source_suite(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    original = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    forged = as_formal_run(original)

    with pytest.raises(ValueError, match="exact output of its pinned source Suite"):
        validate_resolved_run_bundle(
            forged,
            bundle_root=bundle.root,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=fixture_provider_registry(),
        )


def test_inspection_nested_observation_schema_is_pinned_and_validated(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    package_path = bundle.root / "tasks/inspection-package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["observations"][0]["metadata_schema"] = task["instruction"]
    package_path.write_text(
        json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    task["package"]["config"]["file"]["sha256"] = digest(package_path)
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["observations"][0]["schema_file"] = task["instruction"]
    agent_path.write_text(
        yaml.safe_dump(agent, sort_keys=False),
        encoding="utf-8",
    )
    _write_task_and_repin_suite(bundle, task)
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(
        yaml.safe_dump(suite, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="JSON Schema root must be a mapping"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_inspection_observation_contracts_match_agent_grants(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["observations"][0]["provider_id"] = "flight"
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="not granted to its physical provider",
    ):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_inspection_rejects_unimplemented_goal_parameters(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    parameters = [{"name": "window", "value": 3}]
    task["goals"][0]["parameters"] = parameters
    package_path = bundle.root / "tasks/inspection-package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["goals"][0]["parameters"] = parameters
    package_path.write_text(
        json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    task["package"]["config"]["file"]["sha256"] = digest(package_path)
    _write_task_and_repin_suite(bundle, task)

    with pytest.raises(ValueError, match="goal parameters must be empty"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")
