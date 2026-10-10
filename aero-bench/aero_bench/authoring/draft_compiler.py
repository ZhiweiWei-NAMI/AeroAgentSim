"""Compile current drafts against an explicit, byte-verified native template."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path

import yaml

from aero_bench.authoring.compilation_contracts import (
    CityCompilationResult, CityCompileRequest, CompilationBlocker, CompiledRunIdentity,
)
from aero_bench.authoring.native_registry import (
    NativeSceneRegistry,
    VerifiedSceneRegistration,
)
from aero_bench.authoring.traffic_events import lower_traffic_events
from aero_bench.authoring.order_events import lower_order_events, rewrite_order_schedule
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import EnvironmentSpec, FileRef, SuiteSpec, TaskSpec
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.providers.rpc import parse_json_object
from aero_bench.providers.sumo.config import SumoConfig
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.contracts import WorldPackage


COMPILER_ID = "aero-bench.city-draft-compiler/v2"
_FIXED_FIELDS = (
    "fleet", "traffic", "facilities", "airspace", "algorithms",
    "actionRules", "labelRules", "orders", "orderGeneration",
    "performanceProfiles", "authoredLandscape",
)
_RESERVED_ASSET_ID = "asset.city-authoring-snapshot"
_SNAPSHOT_PATH = "authoring/workspace-snapshot.json"
# An installed adapter is not a Provider declared by this run.
_EVENT_CAPABILITY_BLOCKERS = {
    "weather.changed": (
        "No declared Weather Provider applies scheduled wind or precipitation to "
        "native physics; px4.gazebo has no weather-change command."
    ),
    "airspace.activated": (
        "No declared Airspace Provider owns scheduled activation or enforcement; "
        "Gazebo region-transition observations do not implement activation."
    ),
    "charger.outage": (
        "No declared Provider owns physical charger state or scheduled outages; "
        "logistics facility observations do not implement charger actuation."
    ),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reference(root: Path, path: Path) -> FileRef:
    return FileRef(path=path.relative_to(root).as_posix(), sha256=_sha(path.read_bytes()))


def _changed_fields(actual: object, expected: object, path: str) -> list[str]:
    """Report the exact changed inputs, including changes to list membership."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        fields = []
        for key in sorted(actual.keys() | expected.keys()):
            field = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key not in actual or key not in expected:
                fields.append(field)
            else:
                fields.extend(_changed_fields(actual[key], expected[key], field))
        return fields
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return [path]
        return [field for index, (value, reference) in enumerate(zip(actual, expected, strict=True))
                for field in _changed_fields(value, reference, f"{path}/{index}")]
    return [] if actual == expected else [path]


class CityDraftCompiler:
    """Own fresh compilation artifacts; neither start runs nor mint verdicts."""

    def __init__(self, output_root: Path, registry: NativeSceneRegistry | None = None):
        self.output_root = output_root.resolve()
        self.output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.registry = registry if registry is not None else NativeSceneRegistry()
        self._lock = threading.RLock()
        self._results: dict[str, tuple[CityCompilationResult, str]] = {}

    def compile(self, request: CityCompileRequest) -> CityCompilationResult:
        if not isinstance(request, CityCompileRequest):
            raise TypeError("compiler requires a strict CityCompileRequest")
        request_raw = canonical_json_bytes(request.model_dump(mode="json", exclude_unset=True))
        request = CityCompileRequest.model_validate(parse_json_object(request_raw))
        compilation_id = _sha(canonical_json_bytes({
            "compiler": COMPILER_ID, "request_sha256": _sha(request_raw),
        }))
        with self._lock:
            scene = self.registry.get(request.registration_id)
            # Even an idempotent submission must detect changed template bytes.
            files = scene.read_files() if scene is not None else None
            prior = self._results.get(compilation_id)
            if prior is not None:
                if prior[1] != _sha(request_raw):
                    raise ValueError("compilation identity conflict")
                self.result(compilation_id)
                return prior[0]
            blockers = self._blockers(request, scene)
            staging = Path(tempfile.mkdtemp(prefix=".compilation-", dir=self.output_root))
            (staging / "request.json").write_bytes(request_raw)
            draft_sha = _sha(canonical_json_bytes(request.draft.snapshot()))
            suite_ref = None
            identities = ()
            if not blockers:
                if scene is None or files is None:
                    raise RuntimeError("accepted compiler request has no native registration")
                bundle = staging / "bundle"
                bundle.mkdir()
                for relative, raw in files.items():
                    destination = bundle / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                suite_ref, runs = self._lower(request, scene, bundle)
                failed = [(run, condition) for run in runs
                          for condition in run.feasibility.failed_conditions]
                if failed:
                    blockers = tuple(CompilationBlocker(
                        code="feasibility.failed", field="/draft",
                        message=f"{run.case_id}: {condition}",
                    ) for run, condition in failed)
                    suite_ref = None
                else:
                    identities = tuple(CompiledRunIdentity(
                        run_id=run.run_id, scenario_digest=run.scenario.scenario_digest,
                        world_id=run.scenario.world_id, world_digest=scene.world.world_digest,
                        executor_kind=run.executor_kind, feasible=run.feasibility.feasible,
                    ) for run in runs)
            result = CityCompilationResult(
                schema_version="aero-bench.city-compilation-result/v1",
                compilation_id=compilation_id, draft_sha256=draft_sha,
                registration_id=request.registration_id,
                registration_sha256=request.registration_sha256,
                status="blocked" if blockers else "compiled", blockers=blockers,
                suite=suite_ref, runs=identities,
            )
            result_raw = canonical_json_bytes(result.model_dump(mode="json"))
            (staging / "result.json").write_bytes(result_raw)
            (staging / "result.sha256").write_text(_sha(result_raw) + "\n", encoding="ascii")
            final = self.output_root / compilation_id
            if final.exists():
                raise ValueError("compilation destination already exists outside this compiler session")
            staging.rename(final)
            # Keep all compiler output read-only. Control execution uses a fresh
            # sibling output root, never this immutable input directory.
            for path in final.rglob("*"):
                os.chmod(path, 0o500 if path.is_dir() else 0o400)
            os.chmod(final, 0o500)
            self._results[compilation_id] = (result, _sha(request_raw))
            return result

    def _blockers(
        self,
        request: CityCompileRequest,
        scene: VerifiedSceneRegistration | None,
    ) -> tuple[CompilationBlocker, ...]:
        blockers = []
        for index, craft in enumerate(request.draft.fleet):
            if craft.assetId == "model:quadcopter-40-preview":
                blockers.append(CompilationBlocker(
                    code="fleet.native_airframe_unavailable", field=f"/draft/fleet/{index}/assetId",
                    message="This visual-only quadcopter has no registered native airframe implementation.",
                ))
        if scene is None:
            blockers.append(CompilationBlocker(
                code="scene.unregistered", field="/registration_id",
                message="No accepted native scene registration exists for this selection.",
            ))
        else:
            if request.registration_sha256 != scene.registration_sha256:
                blockers.append(CompilationBlocker(
                    code="scene.identity_changed", field="/registration_sha256",
                    message="The selected registration identity differs from its published pin.",
                ))
            if request.draft.scenePath != scene.definition.scene_path:
                blockers.append(CompilationBlocker(
                    code="scene.presentation_mismatch", field="/draft/scenePath",
                    message="The draft does not select this registration's exact presentation.",
                ))
            actual = request.draft.snapshot()
            expected = scene.definition.reference_draft.snapshot()
            for index, event in enumerate(request.draft.events):
                if event.type in {"traffic.restricted", "order.created"}:
                    continue
                blockers.append(CompilationBlocker(
                    code="event.provider_capability_unavailable",
                    field=f"/draft/events/{index}/type",
                    message=_EVENT_CAPABILITY_BLOCKERS[event.type],
                ))
            _, event_blockers = lower_traffic_events(
                request.draft.events, run=scene.run,
                root=scene.root / Path(scene.definition.suite.path).parent,
            )
            blockers.extend(event_blockers)
            _, order_blockers = lower_order_events(
                request.draft.events, run=scene.run,
                root=scene.root / Path(scene.definition.suite.path).parent,
            )
            blockers.extend(order_blockers)
            if scene.run.task.package.package_id == "logistics.arrivals.v1" and not any(event.type == "order.created" for event in request.draft.events):
                blockers.append(CompilationBlocker(code="event.order_schedule_missing", field="/draft/events", message="The arrivals profile requires a nonempty scheduled order.created event set."))
            for name in _FIXED_FIELDS:
                for field in _changed_fields(actual[name], expected[name], f"/draft/{name}"):
                    blockers.append(CompilationBlocker(
                        code="input.native_lowering_unavailable", field=field,
                        message="This native profile has no implementation for the changed execution input.",
                    ))
            if request.draft.deployment.imageRef and (
                request.draft.deployment.imageRef != scene.run.agents[0].workload.runtime.image
            ):
                blockers.append(CompilationBlocker(
                    code="agent.image_unregistered", field="/draft/deployment/imageRef",
                    message="The requested Agent image has no registered implementation binding.",
                ))
        if not request.draft.deployment.imageRef:
            blockers.append(CompilationBlocker(
                code="agent.image_missing", field="/draft/deployment/imageRef",
                message="Execution requires an explicitly registered digest-pinned Agent image.",
            ))
        if request.draft.deployment.executor == "kubernetes_cluster":
            blockers.append(CompilationBlocker(
                code="executor.prerequisites_unavailable", field="/draft/deployment/executor",
                message="This service has no Kubernetes registration with verified cluster prerequisites.",
            ))
        if (scene is None or any(item.adapter == "ns3.rpc" for item in scene.run.environment.providers)) and not 1 <= request.draft.seed <= 0xFFFFFFFF:
            blockers.append(CompilationBlocker(
                code="seed.provider_range", field="/draft/seed",
                message="The registered native ns-3 Provider requires a seed in [1, 2^32-1].",
            ))
        return tuple(blockers)

    def _lower(
        self,
        request: CityCompileRequest,
        scene: VerifiedSceneRegistration,
        bundle: Path,
    ):
        reader = BundleReader(bundle)
        suite_path = reader.resolve_file(scene.definition.suite)
        suite_root = suite_path.parent
        suite_reader = BundleReader(suite_root)
        suite = suite_reader.load_yaml(
            _reference(suite_root, suite_path), SuiteSpec,
        )
        case = suite.cases[0]
        task = suite_reader.load_yaml(case.task, TaskSpec)
        restrictions, event_blockers = lower_traffic_events(
            request.draft.events, run=scene.run, root=suite_root,
        )
        if event_blockers:
            raise ValueError("accepted traffic event failed lowering")
        environment_ref = case.environment
        scheduled_orders, order_blockers = lower_order_events(request.draft.events, run=scene.run, root=suite_root)
        if order_blockers:
            raise ValueError("accepted order creation event failed lowering")
        if scheduled_orders:
            task, environment_ref = rewrite_order_schedule(
                suite_root=suite_root, task=task, environment_ref=case.environment, schedule=scheduled_orders,
            )
        if restrictions:
            environment = suite_reader.load_yaml(case.environment, EnvironmentSpec)
            env_raw = environment.model_dump(mode="json")
            provider = next(item for item in environment.providers if item.adapter == "sumo.traci")
            config = SumoConfig.model_validate(suite_reader.validate_schema_bound_file(provider.config))
            if config.restrictions:
                raise ValueError("native template already contains a scheduled SUMO restriction")
            config = SumoConfig.model_validate({**config.model_dump(mode="json"),
                "restrictions": [item.model_dump(mode="json") for item in restrictions]})
            config_path = suite_root / provider.config.file.path
            schema_path = suite_root / provider.config.schema_file.path
            config_path.write_bytes(canonical_json_bytes(config.model_dump(mode="json")))
            schema_path.write_bytes(canonical_json_bytes(SumoConfig.model_json_schema()))
            slot = next(item for item in env_raw["providers"] if item["provider_id"] == provider.provider_id)
            slot["config"] = {"file": _reference(suite_root, config_path).model_dump(mode="json"),
                              "schema_file": _reference(suite_root, schema_path).model_dump(mode="json")}
            environment = EnvironmentSpec.model_validate(env_raw)
            env_path = suite_root / case.environment.path
            env_path.write_text(yaml.safe_dump(environment.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
            environment_ref = _reference(suite_root, env_path)
        if _RESERVED_ASSET_ID in {asset.asset_id for asset in task.assets} or (
            suite_root / _SNAPSHOT_PATH
        ).exists():
            raise ValueError("native template collides with the compiler's snapshot asset")
        snapshot = {
            "schema_version": "aero-bench.city-authoring-snapshot/v1",
            "compiler_id": COMPILER_ID,
            "registration_id": scene.definition.registration_id,
            "registration_sha256": scene.registration_sha256,
            "draft": request.draft.snapshot(),
            "semantics": {
                "environment": "visual_authoring_only_not_provider_weather",
                "stateKeyframes": "authored_preview_only_not_provider_state",
                "fleet_energy": "declared_authoring_quantities_not_measured_energy",
                "algorithms": "explicit_external_inspection_policy_not_city_planning",
                "orders": "authored_demand_only_not_created_assigned_or_dispatched",
                "orderGeneration": "authoring_configuration_only_not_order_creation",
                "performanceProfiles": "declared_estimates_not_measured_or_mesh_inferred",
                "authoredLandscape": "visual_authoring_only_not_surveyed_or_provider_state",
            },
        }
        snapshot_path = suite_root / _SNAPSHOT_PATH
        if restrictions:
            snapshot["semantics"]["events"] = "pinned_sumo_restrictions_applied_at_provider_barriers"
        if scheduled_orders:
            snapshot["semantics"]["events"] = "pinned_logistics_arrivals_created_at_business_barriers_non_physical"
            snapshot["semantics"]["algorithms"] = "explicit_business_clock_no_physical_planning"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(canonical_json_bytes(snapshot))
        task_raw = task.model_dump(mode="json")
        task_raw["assets"].append({
            "asset_id": _RESERVED_ASSET_ID,
            "file": _reference(suite_root, snapshot_path).model_dump(mode="json"),
            "classification": "private",
            "audiences": [{"role": "verifier", "workload_ids": [task.verifier.verifier_id]}],
        })
        if restrictions:
            # The verifier gets these exact private inputs, never the Agent.
            for kind, asset_id in (("network", scene.run.scenario.sumo.network_asset_id),
                                   ("routes", scene.run.scenario.sumo.routes_asset_id)):
                asset = next(item for item in scene.run.scenario.assets if item.asset_id == asset_id)
                verifier_asset_id = f"asset.sumo-restriction-{kind}"
                if verifier_asset_id in {item["asset_id"] for item in task_raw["assets"]}:
                    raise ValueError("native template collides with restriction verification inputs")
                verifier_path = suite_root / f"authoring/sumo-restriction-{kind}.xml"
                if verifier_path.exists():
                    raise ValueError("native template collides with restriction verification files")
                verifier_path.write_bytes(suite_reader.resolve_file(asset.file).read_bytes())
                task_raw["assets"].append({
                    "asset_id": verifier_asset_id,
                    "file": _reference(suite_root, verifier_path).model_dump(mode="json"),
                    "classification": "private",
                    "audiences": [{"role": "verifier", "workload_ids": [task.verifier.verifier_id]}],
                })
        task = TaskSpec.model_validate(task_raw)
        task_path = suite_root / case.task.path
        task_path.write_text(yaml.safe_dump(task.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
        suite_raw = suite.model_dump(mode="json")
        suite_raw["cases"][0]["task"] = _reference(suite_root, task_path).model_dump(mode="json")
        suite_raw["cases"][0]["environment"] = environment_ref.model_dump(mode="json")
        suite_raw["cases"][0]["seeds"] = [request.draft.seed]
        suite = SuiteSpec.model_validate(suite_raw)
        suite_path.write_text(yaml.safe_dump(suite.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
        runs = resolve_suite(
            str(suite_path), executor_kind=request.draft.deployment.executor,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=builtin_provider_registry(),
        )
        (bundle / "compiled-resolved-runs.json").write_bytes(canonical_json_bytes(
            [run.model_dump(mode="json") for run in runs]
        ))
        return _reference(bundle, suite_path), runs

    def result(self, compilation_id: str) -> CityCompilationResult:
        with self._lock:
            entry = self._results.get(compilation_id)
            if entry is None:
                raise KeyError("unknown compilation")
            result = entry[0]
            root = self.output_root / compilation_id
            if _sha((root / "request.json").read_bytes()) != entry[1]:
                raise ValueError("published compilation request identity changed")
            raw = (root / "result.json").read_bytes()
            expected = _sha(canonical_json_bytes(result.model_dump(mode="json")))
            if _sha(raw) != expected or (root / "result.sha256").read_text().strip() != expected:
                raise ValueError("published compilation result identity changed")
            parsed = CityCompilationResult.model_validate(parse_json_object(raw))
            if parsed != result:
                raise ValueError("published compilation result changed")
            return result

    def bundle(self, compilation_id: str) -> tuple[Path, FileRef]:
        result = self.result(compilation_id)
        if result.status != "compiled" or result.suite is None:
            raise ValueError("blocked compilation has no executable bundle")
        root = self.output_root / compilation_id / "bundle"
        BundleReader(root).resolve_file(result.suite)
        return root, result.suite


def load_published_compilation(
    output_root: Path, compilation_id: str,
) -> tuple[CityCompilationResult, Path, tuple[ResolvedRunSpec, ...]]:
    """Read and re-resolve immutable publication before handing it to Control."""
    if re.fullmatch(r"[0-9a-f]{64}", compilation_id) is None:
        raise ValueError("compilation ID must be a SHA-256 identity")
    output_root = output_root.resolve(strict=True)
    root = output_root / compilation_id
    if root.is_symlink() or not root.is_dir():
        raise ValueError("compilation publication is not a regular directory")
    request_raw = (root / "request.json").read_bytes()
    request = CityCompileRequest.model_validate(parse_json_object(request_raw))
    identity = _sha(canonical_json_bytes({"compiler": COMPILER_ID, "request_sha256": _sha(request_raw)}))
    if identity != compilation_id:
        raise ValueError("compilation request does not reproduce the published ID")
    result_raw = (root / "result.json").read_bytes()
    if _sha(result_raw) != (root / "result.sha256").read_text(encoding="ascii").strip():
        raise ValueError("compilation result publication digest changed")
    result = CityCompilationResult.model_validate(parse_json_object(result_raw))
    if (result.compilation_id, result.draft_sha256, result.registration_id, result.registration_sha256) != (
        compilation_id, _sha(canonical_json_bytes(request.draft.snapshot())),
        request.registration_id, request.registration_sha256,
    ):
        raise ValueError("compilation result does not bind its exact request")
    if result.status != "compiled" or result.suite is None:
        raise ValueError("blocked compilation has no executable bundle")
    bundle = root / "bundle"
    reader = BundleReader(bundle)
    suite_path = reader.resolve_file(result.suite)
    suite_reader = BundleReader(suite_path.parent)
    runs = resolve_suite(
        str(suite_path), executor_kind=request.draft.deployment.executor,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=builtin_provider_registry(),
    )
    expected = tuple(CompiledRunIdentity(
        run_id=run.run_id, scenario_digest=run.scenario.scenario_digest,
        world_id=run.scenario.world_id,
        world_digest=WorldPackage.model_validate(
            suite_reader.load_document(run.scenario.source_world_package)
        ).world_digest,
        executor_kind=run.executor_kind, feasible=run.feasibility.feasible,
    ) for run in runs)
    if expected != result.runs or not all(run.feasibility.feasible for run in runs):
        raise ValueError("immutable compilation resolved identities or feasibility changed")
    recorded = parse_json_object(
        b'{"runs":' + (bundle / "compiled-resolved-runs.json").read_bytes() + b'}'
    )["runs"]
    if recorded != [run.model_dump(mode="json") for run in runs]:
        raise ValueError("recorded ResolvedRun differs from the independently resolved bundle")
    return result, bundle, runs
