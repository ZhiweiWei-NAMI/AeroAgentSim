"""Strict no-launch and registration tests; fixtures are not formal proof."""

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from aero_bench.authoring.compilation_contracts import CityCompileRequest
from aero_bench.authoring.draft_compiler import (
    CityDraftCompiler,
    load_published_compilation,
)
from aero_bench.authoring.logistics_arrivals_registration import (
    publish_arrivals_registration,
)
from aero_bench.authoring.native_registry import NativeSceneRegistry
from aero_bench.config.models import CaseSpec
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.integration import LOGISTICS_RUNTIME_IMPLEMENTED
from aero_bench.tasks.logistics_arrivals.bundle import build_arrivals_bundle
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.contracts import PublicScenario
from aero_bench.trace.projector import project_public_scenario
from aero_bench.world.resolved import ResolvedScenario, scenario_assets_for_workload
from aero_bench.world.workload_scenario import validate_workload_scenario

ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "validation/platform-plan-20261001/W3-BACKEND"


@pytest.fixture(scope="module")
def profile(tmp_path_factory):
    root = tmp_path_factory.mktemp("arrivals")
    bundle = build_arrivals_bundle(
        source_bundle=LANE / "traffic-input-v4",
        image_lock=LANE / "logistics-images-v1/images-lock.json",
        output=root / "bundle",
    )
    runs = resolve_suite(
        str(bundle / "suite.yaml"),
        executor_kind="docker_reference",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    manifest = publish_arrivals_registration(
        suite=bundle / "suite.yaml",
        output=root / "registration",
        registration_id="logistics.arrivals.test",
    )
    registry = NativeSceneRegistry.from_manifest(manifest)
    return bundle, runs[0], registry, registry.get("logistics.arrivals.test")


def test_business_only_run_resolves_without_placeholder_uav(profile):
    _, run, registry, scene = profile
    assert run.launch_site_id is None and run.scenario.selected_launch_site_id is None
    assert run.feasibility.feasible
    assert run.task.package.package_id == "logistics.arrivals.v1"
    assert len(run.environment.providers) == 1
    assert run.environment.providers[0].adapter == "logistics.business"
    assert {
        item.artifact_type for item in run.environment.harness_artifact_requirements
    } == {"event.log", "scene.state-history"}
    assert not run.scenario.launch_sites and not run.scenario.sensors
    assert all(
        item.state == "static" and not item.selected_launch_override
        for item in run.scenario.entities
    )
    assert not LOGISTICS_RUNTIME_IMPLEMENTED
    assert registry.catalog().registrations[0].editable_execution_fields == (
        "/seed",
        "/events",
    )
    assert scene.run == run
    assert PublicScenario.model_validate_json(
        scene.public_scene_bytes()
    ) == project_public_scenario(run)


def test_arrival_metric_cites_private_authority_and_sealed_public_clock(profile):
    from aero_bench.artifacts import EvidenceReference
    from aero_bench.tasks.logistics_arrivals.verifier import arrival_metric_evidence

    _, run, _, _ = profile
    assert arrival_metric_evidence(
        run=run, business_artifact_id="artifact.logistics"
    ) == (
        EvidenceReference(
            artifact_id="artifact.logistics", selector="scheduled_evidence"
        ),
        EvidenceReference(artifact_id="artifact.scene-states", selector="ticks/1-8"),
    )


@pytest.mark.parametrize("tamper", ["missing", "private", "duplicate"])
def test_arrival_metric_rejects_missing_or_private_public_support(profile, tamper):
    from aero_bench.tasks.logistics_arrivals.verifier import arrival_metric_evidence

    _, run, _, _ = profile
    requirements = list(run.artifact_requirements)
    history = next(
        item for item in requirements if item.artifact_type == "scene.state-history"
    )
    requirements.remove(history)
    if tamper == "private":
        requirements.append(history.model_copy(update={"visibility": "private"}))
    elif tamper == "duplicate":
        requirements.extend((history, history))
    invalid = run.model_copy(update={"artifact_requirements": tuple(requirements)})
    with pytest.raises(ValueError, match="exactly one public scene state history"):
        arrival_metric_evidence(run=invalid, business_artifact_id="artifact.logistics")


def public_history_fixture(run):
    from aero_bench.runtime.contracts import (
        SimulationTime,
        StageBarrier,
        stage_barrier_digest_value,
    )
    from aero_bench.runtime.scene_state import SceneStateAssembler

    assembler = SceneStateAssembler(run.scenario)
    states, stages, commits = [], [], {}
    previous = None
    for tick in range(1, run.environment.clock.max_steps + 1):
        at = SimulationTime(tick=tick, sim_time_ns=tick * run.environment.clock.step_ns)
        fields = {
            "schema_version": "aero-bench.stage-barrier/v1",
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "at": at,
            "stage": "motion",
            "input_scene_state_digest": None,
            "predecessor_barriers": (),
            "provider_ids": (),
            "receipts": (),
            "receipt_digests": (),
        }
        unsigned = StageBarrier.model_construct(**fields, barrier_digest="0" * 64)
        barrier = StageBarrier(
            **fields, barrier_digest=stage_barrier_digest_value(unsigned)
        )
        state = assembler.assemble(
            run_id=run.run_id,
            at=at,
            barrier=barrier,
            contributions=(),
            previous_scene_state=previous,
        )
        states.append(state)
        stages.append(
            {
                "target": at.model_dump(mode="json"),
                "scene_state_digest": state.scene_state_digest,
                "motion_barrier_digest": barrier.barrier_digest,
            }
        )
        commits[tick] = state.scene_state_digest
        previous = state
    return tuple(states), stages, commits


def test_public_clock_support_binds_real_typed_states_to_business_inputs(profile):
    from aero_bench.tasks.logistics_arrivals.verifier import (
        validate_arrival_public_history,
    )

    _, run, _, _ = profile
    states, stages, commits = public_history_fixture(run)
    validate_arrival_public_history(
        run=run,
        states=states,
        stage_inputs=stages,
        committed_digests=commits,
    )


@pytest.mark.parametrize(
    "tamper", ["missing", "target", "scene", "motion", "commit", "run"]
)
def test_public_clock_support_rejects_disconnected_evidence(profile, tamper):
    from aero_bench.tasks.logistics_arrivals.verifier import (
        validate_arrival_public_history,
    )

    _, run, _, _ = profile
    states, stages, commits = public_history_fixture(run)
    if tamper == "missing":
        states = states[:-1]
    elif tamper == "target":
        stages[0]["target"]["tick"] += 1
    elif tamper == "scene":
        stages[0]["scene_state_digest"] = "f" * 64
    elif tamper == "motion":
        stages[0]["motion_barrier_digest"] = "f" * 64
    elif tamper == "commit":
        commits[1] = "f" * 64
    else:
        run = run.model_copy(update={"run_id": "f" * 64})
    with pytest.raises(ValueError, match="public .*history"):
        validate_arrival_public_history(
            run=run,
            states=states,
            stage_inputs=stages,
            committed_digests=commits,
        )


@pytest.mark.parametrize(
    "role,workload",
    [
        ("harness", "harness"),
        ("provider", "logistics.business"),
        ("agent", "participant.agent"),
        ("verifier", "logistics.arrivals.verifier"),
    ],
)
def test_no_launch_contract_is_valid_at_each_real_workload_boundary(
    profile, role, workload
):
    _, run, _, _ = profile
    value = validate_workload_scenario(
        run.scenario.model_dump(mode="json"),
        expected_seed=run.seed,
        expected_digest=run.scenario.scenario_digest,
        role=role,
        workload_id=workload,
        projected_assets=[
            item.model_dump(mode="json")
            for item in scenario_assets_for_workload(
                run.scenario, role=role, workload_id=workload
            )
        ],
    )
    assert value.scenario["selected_launch_site_id"] is None


@pytest.mark.parametrize("selections", [[], [None, "launch.alpha"], [None, None]])
def test_case_rejects_missing_or_mixed_no_launch_selection(profile, selections):
    bundle, _, _, _ = profile
    import yaml

    case = yaml.safe_load((bundle / "suite.yaml").read_text())["cases"][0]
    case["launch_site_ids"] = selections
    with pytest.raises(ValueError):
        CaseSpec.model_validate(case)


@pytest.mark.parametrize(
    "tamper", ["package", "stage", "capability", "dynamic", "override"]
)
def test_resolved_and_workload_no_launch_contract_rejects_scope_drift(profile, tamper):
    _, run, _, _ = profile
    raw = run.scenario.model_dump(mode="json")
    if tamper == "package":
        raw["task"]["package_id"] = "inspection.task.v1"
    elif tamper == "stage":
        raw["providers"][0]["runtime_stage"] = "motion"
    elif tamper == "capability":
        raw["providers"][0]["capability_ids"].remove(
            "logistics.orders.scheduled-arrivals"
        )
    elif tamper == "dynamic":
        raw["entities"][0].update(
            state="dynamic",
            kind="ugv",
            authority_kind="sumo_traffic",
            owner_kind="provider",
            owner_id="logistics.business",
            source_provider_id="logistics.business",
        )
    else:
        raw["entities"][0]["selected_launch_override"] = True
    body = deepcopy(raw)
    del body["scenario_digest"]
    raw["scenario_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    with pytest.raises(ValueError):
        ResolvedScenario.model_validate(raw)
    with pytest.raises(ValueError):
        validate_workload_scenario(
            raw,
            expected_seed=run.seed,
            expected_digest=raw["scenario_digest"],
            role="harness",
            workload_id="harness",
            projected_assets=[
                item.model_dump(mode="json")
                for item in scenario_assets_for_workload(
                    run.scenario, role="harness", workload_id="harness"
                )
            ],
        )


@pytest.mark.parametrize("field", ["selected_launch_override", "state"])
def test_public_no_launch_projection_rejects_motion_claims(profile, field):
    _, run, _, _ = profile
    raw = project_public_scenario(run).model_dump(mode="json")
    raw["entities"][0][field] = (
        True if field == "selected_launch_override" else "dynamic"
    )
    with pytest.raises(ValueError, match="static and non-physical"):
        PublicScenario.model_validate(raw)


def request(scene, events=None):
    draft = scene.definition.reference_draft.snapshot()
    if events is not None:
        draft["events"] = events
    return CityCompileRequest(
        schema_version="aero-bench.city-compile-request/v1",
        registration_id=scene.definition.registration_id,
        registration_sha256=scene.registration_sha256,
        draft=draft,
    )


def test_editor_schedule_changes_the_formal_run_id_and_pinned_config(profile, tmp_path):
    _, original, registry, scene = profile
    compiler = CityDraftCompiler(tmp_path / "compiled", registry)
    base = compiler.compile(request(scene))
    events = scene.definition.reference_draft.snapshot()["events"]
    events[0]["atS"] = 1.0
    changed = compiler.compile(request(scene, events))
    assert base.status == changed.status == "compiled"
    assert base.runs[0].run_id != changed.runs[0].run_id
    assert changed.runs[0].run_id != original.run_id
    _, _, runs = load_published_compilation(
        compiler.output_root, changed.compilation_id
    )
    run = runs[0]
    assert (
        run.environment.providers[0].config.file.sha256
        != original.environment.providers[0].config.file.sha256
    )
    assert (
        run.task.package.config.file.sha256 != original.task.package.config.file.sha256
    )
    assert run.scenario.scenario_digest != original.scenario.scenario_digest
    assert run.launch_site_id is None
    assert all(
        asset.file.path
        not in {
            run.environment.providers[0].config.file.path,
            run.environment.providers[0].config.schema_file.path,
        }
        for asset in run.task.assets
    )


def test_empty_editor_schedule_stays_a_compiler_blocker(profile, tmp_path):
    _, _, registry, scene = profile
    result = CityDraftCompiler(tmp_path / "compiled", registry).compile(
        request(scene, [])
    )
    assert result.status == "blocked"
    assert any(item.code == "event.order_schedule_missing" for item in result.blockers)


def test_no_launch_run_cannot_be_relabelled_as_inspection(profile):
    _, run, _, _ = profile
    raw = run.model_dump(mode="json")
    raw["task"]["package"]["package_id"] = "inspection.task.v1"
    with pytest.raises(ValueError):
        ResolvedRunSpec.model_validate(raw)


def test_harness_revalidation_does_not_read_verifier_private_configuration(profile):
    from aero_bench.config.loader import BundleReader
    from aero_bench.config.resolver import validate_runtime_run_bundle
    from aero_bench.executor.planning import build_execution_plan

    bundle, run, _, _ = profile
    registry = builtin_provider_registry()
    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=bundle,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=registry,
    )
    harness = next(item for item in plan.runtime_workloads if item.role == "harness")
    assert run.task.verifier.config.file.path not in {
        item.source.path for item in harness.bundle_inputs
    }
    verifier = {
        run.task.verifier.config.file.path,
        run.task.verifier.config.schema_file.path,
    }

    class HarnessReader(BundleReader):
        def resolve_file(self, reference):
            if reference.path in verifier:
                raise AssertionError(
                    "Harness attempted to read Verifier-private configuration"
                )
            return super().resolve_file(reference)

    from aero_bench.tasks.logistics_arrivals.integration import (
        LogisticsArrivalsTaskPackageResolver,
    )

    kwargs = dict(
        reader=HarnessReader(bundle),
        task=run.task,
        environment=run.environment,
        agents=run.agents,
        scenario=run.scenario,
    )
    resolver = LogisticsArrivalsTaskPackageResolver()
    assert resolver.resolve_runtime(**kwargs) == run.feasibility
    with pytest.raises(AssertionError, match="Verifier-private"):
        resolver.resolve(**kwargs)
    assert validate_runtime_run_bundle(
        run,
        bundle_root=bundle,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=registry,
    )
