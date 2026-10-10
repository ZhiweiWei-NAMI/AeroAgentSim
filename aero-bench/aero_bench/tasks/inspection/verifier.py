from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from aero_bench.artifacts.contracts import (
    ArtifactRecord,
    EvidenceReference,
    SealManifest,
)
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.runtime.ledger import EventLedger
from aero_bench.tasks.inspection.bounds import calculate_theoretical_bounds
from aero_bench.tasks.inspection.business import replay_business_history
from aero_bench.tasks.inspection.contracts import (
    EvidenceBinding,
    InspectionBoundResolution,
    InspectionEvidenceBundle,
    InspectionGoal,
    InspectionProviderConfigBinding,
    InspectionTaskPackage,
    InspectionTheoreticalBounds,
    WorkOrderStatus,
)
from aero_bench.tasks.inspection.integration import (
    resolve_inspection_business_endpoint,
    resolve_inspection_run_context,
)
from aero_bench.tasks.inspection.observation import evaluate_trigger
from aero_bench.tasks.inspection.payloads import evidence_payload_digest
from aero_bench.tasks.inspection.sealed_evidence import (
    _LoadedInspectionEvidence,
    _load_verified_sealed_inspection_evidence,
)
from aero_bench.verifier.contracts import (
    GoalResult,
    MetricResult,
    VerificationReport,
    validate_report_against_run,
)


_METRIC_EVIDENCE: dict[str, str] = {
    "work_order.completion_rate": "authoritative_state",
    "inspection.valid_observation_rate": "artifact",
    "inspection.upload_rate": "artifact",
    "inspection.arrival_rate": "artifact",
    "inspection.success_rate": "artifact",
    "inspection.detection.f1": "artifact",
    "inspection.theoretical.success_upper_bound": "artifact",
    "inspection.theoretical.detection_f1_upper_bound": "artifact",
}


def _time_ns(time: object) -> int:
    return getattr(time, "sim_time_ns")


def _validate_time_series(
    records: tuple[object, ...], name: str, *, time_field: str = "time"
) -> None:
    previous: object | None = None
    for record in records:
        current_time = getattr(record, time_field, None)
        if current_time is None:
            raise ValueError(f"{name} record has no simulation time")
        if previous is not None:
            previous_time = getattr(previous, time_field)
            if current_time.tick < previous_time.tick:
                raise ValueError(f"{name} tick moved backwards")
            if current_time.sim_time_ns < previous_time.sim_time_ns:
                raise ValueError(f"{name} simulation time moved backwards")
        previous = record


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == "ge":
        return value >= threshold
    if operator == "gt":
        return value > threshold
    if operator == "le":
        return value <= threshold
    if operator == "lt":
        return value < threshold
    if operator == "eq":
        return value == threshold
    raise ValueError(f"unsupported inspection goal operator: {operator}")


class InspectionVerificationReport(StrictModel):
    schema_version: Literal["aero-bench.inspection-verification/v1"] = (
        "aero-bench.inspection-verification/v1"
    )
    run_id: Sha256
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    status: Literal["passed", "failed", "invalid"]
    goals: tuple[GoalResult, ...]
    metrics: tuple[MetricResult, ...]
    theoretical_bounds: InspectionTheoreticalBounds
    coverage_complete: bool
    invalid_reasons: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def status_matches_results(self) -> "InspectionVerificationReport":
        all_goals_passed = bool(self.goals) and all(goal.passed for goal in self.goals)
        if self.status in {"passed", "failed"} and self.execution_scope != (
            "formal_benchmark"
        ):
            raise ValueError(
                "inspection pass/fail requires formal_benchmark execution_scope"
            )
        if self.status == "passed" and (
            not self.coverage_complete or not all_goals_passed or self.invalid_reasons
        ):
            raise ValueError(
                "passed inspection verification requires complete, valid, passing goals"
            )
        if self.status == "failed" and (
            not self.coverage_complete or all_goals_passed or self.invalid_reasons
        ):
            raise ValueError(
                "failed inspection verification requires complete coverage and a failed goal"
            )
        if self.status == "invalid" and (
            self.coverage_complete
            or self.goals
            or self.metrics
            or not self.invalid_reasons
        ):
            raise ValueError(
                "invalid inspection verification must contain only invalid reasons"
            )
        return self


def core_verification_report(
    report: InspectionVerificationReport,
    *,
    run: ResolvedRunSpec,
) -> VerificationReport:
    """Project the domain report onto the executor's public report contract."""

    report = InspectionVerificationReport.model_validate(report.model_dump(mode="json"))
    run = ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
    if report.run_id != run.run_id or report.execution_scope != run.execution_scope:
        raise ValueError("Inspection verification report belongs to another ResolvedRun")

    if report.status == "invalid":
        failure_class = report.invalid_reasons[0]
        goals = tuple(
            GoalResult(
                goal_id=goal.goal_id,
                passed=False,
                metrics=(),
                failure_class=failure_class,
            )
            for goal in run.task.goals
        )
    else:
        by_goal_id = {goal.goal_id: goal for goal in report.goals}
        if len(by_goal_id) != len(report.goals):
            raise ValueError("Inspection verification report repeats a goal")
        try:
            goals = tuple(by_goal_id[goal.goal_id] for goal in run.task.goals)
        except KeyError as error:
            raise ValueError(
                "Inspection verification report omits a ResolvedRun goal"
            ) from error

    core = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status=report.status,
        goals=goals,
        coverage_complete=report.coverage_complete,
    )
    validate_report_against_run(core, run)
    return core


class InspectionVerifier:
    """Independent inspection evaluator over sealed, typed evidence only."""

    def __init__(
        self,
        package: InspectionTaskPackage,
        *,
        resolved_bounds: InspectionBoundResolution,
    ) -> None:
        if not isinstance(package, InspectionTaskPackage) or not isinstance(
            resolved_bounds, InspectionBoundResolution
        ):
            raise TypeError(
                "InspectionVerifier requires typed package and resolved bounds contracts"
            )
        try:
            package = InspectionTaskPackage.model_validate(
                package.model_dump(mode="json")
            )
            resolved_bounds = InspectionBoundResolution.model_validate(
                resolved_bounds.model_dump(mode="json")
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Inspection verifier configuration is invalid") from error
        if package.bounds != resolved_bounds.bounds:
            raise ValueError(
                "resolved inspection bounds disagree with the task package"
            )
        self.package = package
        self._resolved_bounds = resolved_bounds

    def verify(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        sealed_root: Path,
    ) -> InspectionVerificationReport:
        """Disabled legacy entry point; verification must begin with sealed loading."""

        del evidence, sealed_root
        raise RuntimeError("InspectionVerifier.verify is disabled; use verify_sealed")

    def _validate_loaded_evidence(
        self,
        loaded: _LoadedInspectionEvidence,
        *,
        business_endpoint_id: str | None = None,
        allow_task_local_business_principal: bool = False,
    ) -> InspectionEvidenceBundle:
        if type(loaded) is not _LoadedInspectionEvidence:
            raise TypeError(
                "InspectionVerifier requires loader-issued sealed evidence"
            )
        # Only the strict sealed loader can issue this capability. Its nested
        # frozen models have already been validated; dumping the complete ledger
        # and SceneState history duplicates both graphs without adding a check.
        # Re-run bundle validators and all authority/binding checks below while
        # retaining those validated nested instances.
        evidence = InspectionEvidenceBundle.model_validate(
            {
                name: getattr(loaded.evidence, name)
                for name in InspectionEvidenceBundle.model_fields
            }
        )
        self._validate_seal_and_bindings(evidence)
        self._validate_records(
            evidence,
            business_endpoint_id=business_endpoint_id,
            allow_task_local_business_principal=(
                allow_task_local_business_principal
            ),
        )
        return evidence

    def _verify_loaded(
        self,
        loaded: _LoadedInspectionEvidence,
        *,
        business_endpoint_id: str | None = None,
        allow_task_local_business_principal: bool = False,
    ) -> InspectionVerificationReport:
        bounds = calculate_theoretical_bounds(
            self._resolved_bounds.bounds,
            network_delivery_required=self.package.network_delivery_required,
        )
        identity_evidence = loaded.evidence
        try:
            evidence = self._validate_loaded_evidence(
                loaded,
                business_endpoint_id=business_endpoint_id,
                allow_task_local_business_principal=(
                    allow_task_local_business_principal
                ),
            )
            metrics = self._compute_metrics(evidence, bounds)
            goals = tuple(
                self._evaluate_goal(goal, metrics, bounds)
                for goal in self.package.goals
            )
            passed = all(goal.passed for goal in goals)
            return InspectionVerificationReport(
                run_id=evidence.run_id,
                execution_scope=evidence.seal.execution_scope,
                status="passed" if passed else "failed",
                goals=goals,
                metrics=tuple(metrics),
                theoretical_bounds=bounds,
                coverage_complete=True,
            )
        except (TypeError, UnicodeError, ValueError) as error:
            return InspectionVerificationReport(
                run_id=identity_evidence.run_id,
                execution_scope=identity_evidence.seal.execution_scope,
                status="invalid",
                goals=(),
                metrics=(),
                theoretical_bounds=bounds,
                coverage_complete=False,
                invalid_reasons=(self._error_code(str(error)),),
            )

    def load_validated_sealed_evidence(
        self,
        *,
        bundle_root: Path,
        run: ResolvedRunSpec,
        seal: SealManifest,
        sealed_root: Path,
    ) -> InspectionEvidenceBundle:
        """Load and fully validate the exact sealed Inspection evidence inventory."""

        validated_run = ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
        pinned_package, resolved_bounds, _ = resolve_inspection_run_context(
            reader=BundleReader(bundle_root),
            task=validated_run.task,
            environment=validated_run.environment,
            agents=validated_run.agents,
            scenario=validated_run.scenario,
        )
        if pinned_package != self.package:
            raise ValueError(
                "pinned Inspection package disagrees with verifier configuration"
            )
        if resolved_bounds != self._resolved_bounds:
            raise ValueError(
                "bundle-resolved Inspection bounds disagree with verifier configuration"
            )
        self._validate_resolved_run_context(validated_run)
        business_endpoint_id, task_local_business = (
            resolve_inspection_business_endpoint(
                package=pinned_package,
                task=validated_run.task,
                environment=validated_run.environment,
                agents=validated_run.agents,
            )
        )
        expected_logical_endpoint_ids = (
            (business_endpoint_id,) if task_local_business else ()
        )
        if (
            validated_run.scenario.task.logical_endpoint_ids
            != expected_logical_endpoint_ids
        ):
            raise ValueError(
                "Inspection verifier Business ownership disagrees with ResolvedScenario"
            )
        loaded = _load_verified_sealed_inspection_evidence(
            package=self.package,
            bundle_root=bundle_root,
            run=validated_run,
            seal=seal,
            sealed_root=sealed_root,
        )
        return self._validate_loaded_evidence(
            loaded,
            business_endpoint_id=business_endpoint_id,
            allow_task_local_business_principal=task_local_business,
        )

    def verify_sealed(
        self,
        *,
        bundle_root: Path,
        run: ResolvedRunSpec,
        seal: SealManifest,
        sealed_root: Path,
    ) -> InspectionVerificationReport:
        """Load the exact sealed inventory before verifying its typed evidence."""

        bounds = calculate_theoretical_bounds(
            self._resolved_bounds.bounds,
            network_delivery_required=self.package.network_delivery_required,
        )
        try:
            validated_run = ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
            pinned_package, resolved_bounds, _ = resolve_inspection_run_context(
                reader=BundleReader(bundle_root),
                task=validated_run.task,
                environment=validated_run.environment,
                agents=validated_run.agents,
                scenario=validated_run.scenario,
            )
            if pinned_package != self.package:
                raise ValueError(
                    "pinned Inspection package disagrees with verifier configuration"
                )
            if resolved_bounds != self._resolved_bounds:
                raise ValueError(
                    "bundle-resolved Inspection bounds disagree with verifier configuration"
                )
            self._validate_resolved_run_context(validated_run)
            expected_business_endpoint_id, expected_task_local_business = (
                resolve_inspection_business_endpoint(
                    package=pinned_package,
                    task=validated_run.task,
                    environment=validated_run.environment,
                    agents=validated_run.agents,
                )
            )
            task_local_business = bool(
                validated_run.scenario.task.logical_endpoint_ids
            )
            expected_logical_endpoint_ids = (
                (expected_business_endpoint_id,)
                if expected_task_local_business
                else ()
            )
            if (
                validated_run.scenario.task.logical_endpoint_ids
                != expected_logical_endpoint_ids
            ):
                raise ValueError(
                    "Inspection verifier Business ownership disagrees with "
                    "ResolvedScenario"
                )
            business_endpoint_id = (
                validated_run.scenario.task.logical_endpoint_ids[0]
                if task_local_business
                else expected_business_endpoint_id
            )
            evidence = _load_verified_sealed_inspection_evidence(
                package=self.package,
                bundle_root=bundle_root,
                run=validated_run,
                seal=seal,
                sealed_root=sealed_root,
            )
        except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as error:
            return self._invalid_sealed_report(run=run, bounds=bounds, error=error)
        return self._verify_loaded(
            evidence,
            business_endpoint_id=business_endpoint_id,
            allow_task_local_business_principal=task_local_business,
        )

    @staticmethod
    def _invalid_sealed_report(
        *,
        run: object,
        bounds: InspectionTheoreticalBounds,
        error: Exception,
    ) -> InspectionVerificationReport:
        run_id = getattr(run, "run_id", None)
        execution_scope = getattr(run, "execution_scope", None)
        if (
            not isinstance(run_id, str)
            or len(run_id) != 64
            or run_id == "0" * 64
            or any(character not in "0123456789abcdef" for character in run_id)
            or execution_scope not in {"executor_validation", "formal_benchmark"}
        ):
            raise ValueError(
                "cannot issue an Inspection report without the source run identity "
                "and execution scope"
            ) from error
        return InspectionVerificationReport(
            run_id=run_id,
            execution_scope=execution_scope,
            status="invalid",
            goals=(),
            metrics=(),
            theoretical_bounds=bounds,
            coverage_complete=False,
            invalid_reasons=(InspectionVerifier._error_code(str(error)),),
        )

    def _validate_resolved_run_context(self, run: ResolvedRunSpec) -> None:
        if (
            run.task.task_id != self.package.task_id
            or run.task.verifier.verifier_id != self.package.verifier_id
        ):
            raise ValueError(
                "Inspection verifier package does not match the resolved run"
            )
        providers = {
            provider.provider_id: provider for provider in run.environment.providers
        }
        required_provider_ids = {
            source.provider_id for source in self.package.bound_sources
        } | {
            observation.trigger.provider_id for observation in self.package.observations
        }
        if not required_provider_ids <= set(providers):
            raise ValueError(
                "Inspection bound providers are absent from the resolved run"
            )
        expected_provider_configs = tuple(
            InspectionProviderConfigBinding(
                provider_id=provider_id,
                config_digest=providers[provider_id].config.file.sha256,
            )
            for provider_id in sorted(required_provider_ids)
        )
        if self._resolved_bounds.provider_configs != expected_provider_configs:
            raise ValueError(
                "resolved Inspection bounds are not bound to the run environment"
            )

    @staticmethod
    def _error_code(message: str) -> str:
        if "missing required sealed artifact" in message:
            return "evidence.missing_artifact"
        if "digest" in message and "seal" in message:
            return "evidence.invalid_seal"
        if "private" in message:
            return "evidence.visibility"
        if "source artifact" in message:
            return "evidence.unsealed_record"
        if "truth" in message:
            return "evidence.truth_contract"
        return "evidence.invalid"

    def _validate_seal_and_bindings(
        self,
        evidence: InspectionEvidenceBundle,
    ) -> None:
        if evidence.seal.run_id != evidence.run_id:
            raise ValueError("seal belongs to another run")
        if evidence.business_state.event_chain_root != evidence.seal.event_chain_root:
            raise ValueError("event chain root disagrees with seal")
        if evidence.seal.event_chain_root == "0" * 64:
            raise ValueError("event chain root cannot be a placeholder")
        sealed_by_id = {
            artifact.artifact_id: artifact for artifact in evidence.seal.artifacts
        }
        declared = {
            (
                item.artifact_id,
                item.artifact_type,
                item.producer_id,
                item.visibility,
                item.evidence_kind,
            )
            for item in self.package.artifact_requirements
        }
        declared_artifact_ids = {
            item.artifact_id for item in self.package.artifact_requirements
        }
        if not declared_artifact_ids <= set(sealed_by_id):
            raise ValueError(
                "seal is missing a declared Inspection evidence artifact"
            )
        binding_ids = {binding.artifact_id for binding in evidence.bindings}
        if (
            len(evidence.bindings) != len(declared_artifact_ids)
            or binding_ids != declared_artifact_ids
        ):
            raise ValueError(
                "evidence bindings must contain exactly the declared artifacts"
            )
        for binding in evidence.bindings:
            if (
                binding.artifact_id,
                binding.artifact_type,
                binding.producer_id,
                binding.visibility,
                binding.evidence_kind,
            ) not in declared:
                raise ValueError(
                    f"source artifact is not declared: {binding.artifact_id}"
                )
            sealed = sealed_by_id.get(binding.artifact_id)
            if sealed is None:
                raise ValueError(
                    f"source artifact is not sealed: {binding.artifact_id}"
                )
            if (
                sealed.artifact_type != binding.artifact_type
                or sealed.producer_id != binding.producer_id
                or sealed.visibility != binding.visibility
            ):
                raise ValueError(
                    f"source artifact binding disagrees with seal: {binding.artifact_id}"
                )
        for requirement in self.package.artifact_requirements:
            if not any(
                binding.artifact_id == requirement.artifact_id
                and binding.artifact_type == requirement.artifact_type
                and binding.producer_id == requirement.producer_id
                and binding.visibility == requirement.visibility
                and binding.evidence_kind == requirement.evidence_kind
                for binding in evidence.bindings
            ):
                raise ValueError(
                    f"missing required sealed artifact: {requirement.artifact_type}"
                )
        if not evidence.bindings:
            raise ValueError("source artifact bindings are empty")
        self._validate_payload_digests(evidence, sealed_by_id=sealed_by_id)

    def _validate_payload_digests(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        sealed_by_id: dict[str, ArtifactRecord],
    ) -> None:
        return

    @staticmethod
    def _record_source(
        source_artifact_id: str,
        expected_kind: str,
        bindings: dict[str, EvidenceBinding],
    ) -> EvidenceBinding:
        binding = bindings.get(source_artifact_id)
        if binding is None:
            raise ValueError(f"source artifact is not bound: {source_artifact_id}")
        if binding.evidence_kind != expected_kind:
            raise ValueError("source artifact is bound to another evidence kind")
        return binding

    def _validate_records(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        business_endpoint_id: str | None = None,
        allow_task_local_business_principal: bool = False,
    ) -> None:
        bindings = {binding.artifact_id: binding for binding in evidence.bindings}
        self._record_source(
            evidence.event_ledger.source_artifact_id,
            "event_log",
            bindings,
        )
        self._record_source(
            evidence.business_state.source_artifact_id,
            "business_state",
            bindings,
        )
        self._record_source(
            evidence.theoretical_bounds.source_artifact_id,
            "theoretical_bounds",
            bindings,
        )
        if evidence.theoretical_bounds.bounds != calculate_theoretical_bounds(
            self._resolved_bounds.bounds,
            network_delivery_required=self.package.network_delivery_required,
        ):
            raise ValueError("sealed theoretical bounds disagree with task bounds")
        if (
            evidence.theoretical_bounds.provider_configs
            != self._resolved_bounds.provider_configs
        ):
            raise ValueError(
                "sealed theoretical bounds name different provider configurations"
            )
        if not evidence.business_state.history:
            raise ValueError("business history is empty")
        if evidence.event_ledger.records:
            EventLedger.from_records(evidence.event_ledger.records)
        replayed_state = replay_business_history(
            self.package,
            run_id=evidence.run_id,
            history=evidence.business_state.history,
            allow_task_local_business_principal=(
                allow_task_local_business_principal
            ),
        )
        if (
            evidence.business_state.history[-1].record_hash
            != evidence.business_state.business_history_root
        ):
            raise ValueError("business history root does not terminate its local chain")
        if replayed_state != evidence.business_state.state:
            raise ValueError("business state does not match replayed history")
        if evidence.event_ledger.records:
            self._validate_business_ledger_bindings(
                evidence,
                business_source_id=business_endpoint_id,
            )
        for record in evidence.trajectory:
            self._record_source(record.source_artifact_id, "trajectory", bindings)
        for record in evidence.deliveries:
            self._record_source(record.source_artifact_id, "delivery", bindings)
        for record in evidence.observations:
            self._record_source(record.source_artifact_id, "observation", bindings)
        for record in evidence.sensor_frames:
            self._record_source(record.source_artifact_id, "sensor_frame", bindings)
        for record in evidence.camera_frames:
            source = self._record_source(
                record.source_artifact_id, "camera_frame_data", bindings
            )
            if source.visibility != "public":
                raise ValueError("camera frame bytes must come from a public artifact")
        if evidence.model_session is not None:
            if evidence.model_interactions is None:
                raise ValueError("model session evidence is incomplete")
            session_source = self._record_source(
                evidence.model_session.source_artifact_id,
                "model_session_manifest",
                bindings,
            )
            interactions_source = self._record_source(
                evidence.model_interactions.source_artifact_id,
                "model_interactions",
                bindings,
            )
            if (
                session_source.visibility != "private"
                or interactions_source.visibility != "private"
                or session_source.producer_id != interactions_source.producer_id
            ):
                raise ValueError(
                    "model session evidence must come from one private Driver"
                )
        truth_requirement = next(
            item
            for item in self.package.artifact_requirements
            if item.evidence_kind == "truth"
        )
        if truth_requirement.producer_id != "bundle":
            raise ValueError("truth must bind an immutable bundle source asset")
        for record in evidence.truth:
            source = self._record_source(record.source_artifact_id, "truth", bindings)
            if source.visibility != "private":
                raise ValueError(
                    "hidden truth must come from a private source artifact"
                )
        for record in evidence.detections:
            source = self._record_source(
                record.source_artifact_id, "detection", bindings
            )
            if source.visibility != "public":
                raise ValueError(
                    "agent detections must come from a public source artifact"
                )
        for record in evidence.report:
            source = self._record_source(record.source_artifact_id, "report", bindings)
            if source.visibility != "public":
                raise ValueError(
                    "inspection reports must come from a public source artifact"
                )
        _validate_time_series(evidence.trajectory, "trajectory")
        _validate_time_series(evidence.deliveries, "delivery", time_field="sent_at")
        _validate_time_series(
            tuple(
                item
                for item in evidence.deliveries
                if item.delivered_at is not None
            ),
            "delivery completion",
            time_field="delivered_at",
        )
        _validate_time_series(evidence.observations, "observation")

        report_requirement = next(
            item
            for item in self.package.artifact_requirements
            if item.evidence_kind == "report"
        )
        report_source = self._record_source(
            report_requirement.artifact_id,
            "report",
            bindings,
        )
        report_artifact = next(
            item
            for item in evidence.seal.artifacts
            if item.artifact_id == report_source.artifact_id
        )
        if (
            report_artifact.size_bytes
            < self._resolved_bounds.bounds.link.minimum_payload_bytes
        ):
            raise ValueError(
                "sealed report is smaller than the minimum payload used by the link bound"
            )
        for work_order in evidence.business_state.state.work_orders:
            if (
                work_order.report_payload_digest is not None
                and work_order.report_payload_digest != report_artifact.sha256
            ):
                raise ValueError(
                    "report payload digest is not the sealed report digest"
                )

        states = {
            item.work_order_id: item
            for item in evidence.business_state.state.work_orders
        }
        target_ids = {item.target_id for item in self.package.work_orders}
        if any(record.target_id not in target_ids for record in evidence.truth):
            raise ValueError("truth names an undeclared target")
        sensor_frames_by_id = {
            frame.frame_id: frame for frame in evidence.sensor_frames
        }
        for record in evidence.detections:
            state = states.get(record.work_order_id)
            if state is None or state.target_id != record.target_id:
                raise ValueError("detection does not match the task contract")
            if record.observation_id != state.required_observation_id:
                raise ValueError("detection observation does not match the work order")
            frame = sensor_frames_by_id.get(record.frame_id)
            if (
                frame is None
                or frame.observation_id != record.observation_id
                or frame.image_sha256 != record.image_sha256
            ):
                raise ValueError(
                    "detection does not bind the exact captured RGB frame"
                )
        self._validate_report_payload(
            evidence,
            states=states,
            report_artifact=report_artifact,
        )

        if len(evidence.truth) != self._resolved_bounds.bounds.total_defects:
            raise ValueError("truth record count disagrees with bounds")
        visible_count = sum(
            1 for defect in evidence.truth if defect.geometrically_visible
        )
        if visible_count != self._resolved_bounds.bounds.geometrically_visible_defects:
            raise ValueError("visible truth count disagrees with bounds")
        truth_ids = [defect.defect_id for defect in evidence.truth]
        if len(truth_ids) != len(set(truth_ids)):
            raise ValueError("truth defect ids must be unique")
        detection_keys = [
            (detection.work_order_id, detection.defect_id)
            for detection in evidence.detections
        ]
        if len(detection_keys) != len(set(detection_keys)):
            raise ValueError("duplicate defect detections are not valid evidence")

    def _validate_report_payload(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        states: dict[str, object],
        report_artifact: ArtifactRecord,
    ) -> None:
        detection_requirement = next(
            item
            for item in self.package.artifact_requirements
            if item.evidence_kind == "detection"
        )
        detection_artifact = next(
            item
            for item in evidence.seal.artifacts
            if item.artifact_id == detection_requirement.artifact_id
        )
        detection_digest = evidence_payload_digest(evidence, "detection")
        if detection_artifact.sha256 != detection_digest:
            raise ValueError("detection payload disagrees with sealed artifact")

        observations_by_key: dict[tuple[str, str], list[object]] = {}
        for observation in evidence.observations:
            observations_by_key.setdefault(
                (observation.work_order_id, observation.observation_id), []
            ).append(observation)

        submitted_work_orders = {
            work_order_id
            for work_order_id, state in states.items()
            if getattr(state, "report_payload_digest") is not None
        }
        reported_work_orders = {report.work_order_id for report in evidence.report}
        if reported_work_orders != submitted_work_orders:
            raise ValueError(
                "report evidence does not cover every submitted work order"
            )

        reported_detection_keys: set[tuple[str, str, str, str, str, str]] = set()
        for report in evidence.report:
            state = states.get(report.work_order_id)
            if state is None:
                raise ValueError("report names an unknown work order")
            if (
                getattr(state, "observation_id") != report.observation_id
                or getattr(state, "report_payload_digest") != report_artifact.sha256
            ):
                raise ValueError("report does not match the submitted work order")
            observations = tuple(
                observation
                for observation in observations_by_key.get(
                    (report.work_order_id, report.observation_id), []
                )
                if report.observation_payload_digest
                == getattr(observation, "payload_digest")
            )
            if not observations:
                raise ValueError(
                    "report must bind captured observation bytes"
                )
            report_observation_frames = {
                (observation.frame_id, observation.image_sha256)
                for observation in observations
            }
            if any(
                (detection.frame_id, detection.image_sha256)
                not in report_observation_frames
                for detection in report.detections
            ):
                raise ValueError(
                    "report detection does not bind its exact observation frame"
                )
            if report.detection_payload_digest != detection_artifact.sha256:
                raise ValueError(
                    "report detection digest does not match detection evidence"
                )
            report_detections = {
                (
                    detection.defect_id,
                    detection.target_id,
                    detection.frame_id,
                    detection.image_sha256,
                )
                for detection in report.detections
            }
            evidence_detections = {
                (
                    detection.defect_id,
                    detection.target_id,
                    detection.frame_id,
                    detection.image_sha256,
                )
                for detection in evidence.detections
                if detection.work_order_id == report.work_order_id
                and detection.observation_id == report.observation_id
            }
            if report_detections != evidence_detections:
                raise ValueError("report detections do not match detection evidence")
            reported_detection_keys.update(
                (
                    report.work_order_id,
                    report.observation_id,
                    detection.defect_id,
                    detection.target_id,
                    detection.frame_id,
                    detection.image_sha256,
                )
                for detection in report.detections
            )

        evidence_detection_keys = {
            (
                detection.work_order_id,
                detection.observation_id,
                detection.defect_id,
                detection.target_id,
                detection.frame_id,
                detection.image_sha256,
            )
            for detection in evidence.detections
        }
        if reported_detection_keys != evidence_detection_keys:
            raise ValueError("detection evidence is not bound by a report")

    def _validate_business_ledger_bindings(
        self,
        evidence: InspectionEvidenceBundle,
        *,
        business_source_id: str | None = None,
    ) -> None:
        history = evidence.business_state.history
        ledger_records = evidence.event_ledger.records
        bindings = evidence.business_ledger_bindings
        if {item.business_history_sequence for item in bindings} != set(
            range(len(history))
        ):
            raise ValueError(
                "business ledger bindings do not cover the complete business history"
            )

        business_sources = (
            {business_source_id}
            if business_source_id is not None
            else {
                actor.actor_id
                for actor in self.package.actors
                if actor.role == "business"
            }
        )
        mapped_ledger_sequences: set[int] = set()
        for binding in bindings:
            if binding.ledger_sequence >= len(ledger_records):
                raise ValueError("business ledger binding is outside event.log")
            history_record = history[binding.business_history_sequence]
            ledger_record = ledger_records[binding.ledger_sequence]
            if (
                binding.business_history_record_hash != history_record.record_hash
                or binding.command_id != history_record.command.command.command_id
            ):
                raise ValueError(
                    "business ledger binding disagrees with business history"
                )
            if binding.ledger_event_hash != ledger_record.event_hash:
                raise ValueError("business ledger binding event hash is invalid")
            expected_payload = {
                "schema_id": "aero-bench.business-history/v1",
                "business_history_sequence": history_record.sequence,
                "business_history_record_hash": history_record.record_hash,
                "command_id": history_record.command.command.command_id,
            }
            actual_payload = {
                item.name: item.value for item in ledger_record.event.payload
            }
            if (
                ledger_record.event.source not in business_sources
                or ledger_record.event.event_type != "business.transition"
                or ledger_record.event.time != history_record.transition.time
                or actual_payload != expected_payload
            ):
                raise ValueError(
                    "business history is not uniquely recorded by the global ledger"
                )
            mapped_ledger_sequences.add(ledger_record.sequence)

        authoritative_business_sequences = {
            record.sequence
            for record in ledger_records
            if record.event.source in business_sources
            and record.event.event_type == "business.transition"
        }
        if mapped_ledger_sequences != authoritative_business_sequences:
            raise ValueError(
                "global ledger contains an unmapped business transition event"
            )

    def _compute_metrics(
        self,
        evidence: InspectionEvidenceBundle,
        bounds: InspectionTheoreticalBounds,
    ) -> tuple[MetricResult, ...]:
        states = {
            item.work_order_id: item
            for item in evidence.business_state.state.work_orders
        }
        expected_states = {item.work_order_id for item in self.package.work_orders}
        if set(states) != expected_states:
            raise ValueError("business state does not cover every work order")

        valid_observation_keys: set[tuple[str, str]] = set()
        valid_observation_frames: set[tuple[str, str, str, str]] = set()
        observation_refs: set[str] = set()
        sensor_frame_refs = {
            record.source_artifact_id for record in evidence.sensor_frames
        }
        contracts = {item.observation_id: item for item in self.package.observations}
        config_digests = {
            item.provider_id: item.config_digest
            for item in self._resolved_bounds.provider_configs
        }
        for record in evidence.observations:
            observation_refs.add(record.source_artifact_id)
            state = states.get(record.work_order_id)
            if state is None:
                raise ValueError("observation names an unknown work order")
            contract = contracts.get(record.observation_id)
            if contract is None or contract.target_id != record.target_id:
                raise ValueError("observation does not match the task contract")
            if (
                record.view.source_provider_id != contract.trigger.provider_id
                or record.view.provider_config_digest
                != config_digests.get(contract.trigger.provider_id)
            ):
                raise ValueError(
                    "observation view is not bound to the resolved Provider config"
                )
            if record.view.time != record.time:
                raise ValueError(
                    "observation metadata time disagrees with view context"
                )
            if record.view.target_id != record.target_id:
                raise ValueError("observation view target disagrees with metadata")
            if record.view.camera_id != record.camera_id:
                raise ValueError("observation view camera disagrees with metadata")
            if evaluate_trigger(contract.trigger, record.view).eligible:
                valid_observation_keys.add(
                    (record.work_order_id, record.observation_id)
                )
                valid_observation_frames.add(
                    (
                        record.work_order_id,
                        record.observation_id,
                        record.frame_id,
                        record.image_sha256,
                    )
                )

        arrived_work_orders: set[str] = set()
        trajectory_refs: set[str] = set()
        for record in evidence.trajectory:
            trajectory_refs.add(record.source_artifact_id)
            state = states.get(record.work_order_id)
            if state is None or record.target_id != state.target_id:
                raise ValueError("trajectory does not match the task contract")
            if (
                record.distance_to_target_m <= state.arrival_tolerance_m
                and _time_ns(record.time) <= self._mission_deadline_ns()
            ):
                arrived_work_orders.add(record.work_order_id)

        delivered_work_orders: set[str] = set()
        delivery_refs: set[str] = set()
        for record in evidence.deliveries:
            delivery_refs.add(record.source_artifact_id)
            state = states.get(record.work_order_id)
            if state is None:
                raise ValueError("delivery names an unknown work order")
            if state.report_payload_digest != record.payload_digest:
                continue
            if (
                record.delivered_at is not None
                and _time_ns(record.delivered_at) <= state.upload_deadline_ns
            ):
                delivered_work_orders.add(record.work_order_id)

        total = len(self.package.work_orders)
        completed = sum(
            1
            for work_order_id in expected_states
            if states[work_order_id].status is WorkOrderStatus.COMPLETED
        )
        valid_observation_work_orders = {
            work_order_id
            for work_order_id, state in states.items()
            if (work_order_id, state.required_observation_id) in valid_observation_keys
        }
        valid_observations = len(valid_observation_work_orders & expected_states)
        delivered = len(delivered_work_orders & expected_states)
        for work_order_id in expected_states:
            required_observation_id = states[work_order_id].required_observation_id
            if not any(
                (item.work_order_id, item.observation_id)
                == (work_order_id, required_observation_id)
                for item in evidence.observations
            ):
                raise ValueError("missing required observation evidence for work order")
            if not any(
                item.work_order_id == work_order_id for item in evidence.trajectory
            ):
                raise ValueError("missing trajectory evidence for work order")
            if not any(
                item.work_order_id == work_order_id for item in evidence.deliveries
            ):
                raise ValueError("missing delivery evidence for work order")
        detection_f1 = self._detection_f1(evidence, valid_observation_frames)
        if detection_f1 > bounds.detection_f1_upper_bound + 1e-12:
            raise ValueError("observed detection F1 exceeds theoretical bound")
        arrived = len(arrived_work_orders & expected_states)
        completed_work_orders = {
            work_order_id
            for work_order_id in expected_states
            if states[work_order_id].status is WorkOrderStatus.COMPLETED
        }
        successful = len(
            completed_work_orders
            & arrived_work_orders
            & valid_observation_work_orders
            & delivered_work_orders
        )

        detection_refs = {item.source_artifact_id for item in evidence.detections}
        public_ids = {
            artifact.artifact_id
            for artifact in evidence.seal.artifacts
            if artifact.visibility == "public"
        }
        selector_by_kind = {
            "business_state": "business",
            "delivery": "deliveries",
            "detection": "detections",
            "report": "reports",
            "scene_state_history": "ticks",
            "sensor_frame": "frames",
            "camera_frame_data": "frames",
            "theoretical_bounds": "bounds",
            "trajectory": "trajectory",
        }
        public_selectors = {
            binding.artifact_id: selector_by_kind[binding.evidence_kind]
            for binding in evidence.bindings
            if binding.artifact_id in public_ids
            and binding.evidence_kind in selector_by_kind
        }
        if set(public_selectors) != public_ids:
            raise ValueError("public Inspection evidence has no declared selector")
        refs = self._public_refs(
            {binding.artifact_id for binding in evidence.bindings},
            public_ids,
            public_selectors,
        )
        business_refs = self._public_refs(
            {evidence.business_state.source_artifact_id},
            public_ids,
            public_selectors,
        )
        theoretical_refs = self._public_refs(
            {evidence.theoretical_bounds.source_artifact_id},
            public_ids,
            public_selectors,
        )
        truth_refs = self._public_refs(
            {record.source_artifact_id for record in evidence.truth},
            public_ids,
            public_selectors,
        )
        metrics = (
            MetricResult(
                metric_id="work_order.completion_rate",
                value=completed / total,
                unit="ratio",
                evidence=business_refs,
            ),
            MetricResult(
                metric_id="inspection.valid_observation_rate",
                value=valid_observations / total,
                unit="ratio",
                evidence=self._public_refs(
                    observation_refs | sensor_frame_refs,
                    public_ids,
                    public_selectors,
                ),
            ),
            MetricResult(
                metric_id="inspection.upload_rate",
                value=delivered / total,
                unit="ratio",
                evidence=self._public_refs(
                    delivery_refs,
                    public_ids,
                    public_selectors,
                ),
            ),
            MetricResult(
                metric_id="inspection.arrival_rate",
                value=arrived / total,
                unit="ratio",
                evidence=self._public_refs(
                    trajectory_refs,
                    public_ids,
                    public_selectors,
                ),
            ),
            MetricResult(
                metric_id="inspection.success_rate",
                value=successful / total,
                unit="ratio",
                evidence=refs,
            ),
            MetricResult(
                metric_id="inspection.detection.f1",
                value=detection_f1,
                unit="ratio",
                evidence=self._public_refs(
                    {
                        *detection_refs,
                        *{reference.artifact_id for reference in truth_refs},
                    },
                    public_ids,
                    public_selectors,
                ),
            ),
            MetricResult(
                metric_id="inspection.theoretical.success_upper_bound",
                value=bounds.success_upper_bound,
                unit="ratio",
                evidence=theoretical_refs,
            ),
            MetricResult(
                metric_id="inspection.theoretical.detection_f1_upper_bound",
                value=bounds.detection_f1_upper_bound,
                unit="ratio",
                evidence=theoretical_refs,
            ),
        )

        for metric in metrics:
            upper_bound = self._metric_upper_bound(metric.metric_id, bounds)
            if metric.value > upper_bound + 1e-12:
                raise ValueError(
                    f"metric exceeds theoretical upper bound: {metric.metric_id}"
                )
        return metrics

    @staticmethod
    def _public_refs(
        artifact_ids: set[str],
        public_ids: set[str],
        public_selectors: dict[str, str],
    ) -> tuple[EvidenceReference, ...]:
        return tuple(
            EvidenceReference(
                artifact_id=artifact_id,
                selector=public_selectors[artifact_id],
            )
            for artifact_id in sorted(artifact_ids & public_ids)
        )

    def _mission_deadline_ns(self) -> int:
        return math.ceil(
            self._resolved_bounds.bounds.mission.deadline_s * 1_000_000_000
        )

    @staticmethod
    def _detection_f1(
        evidence: InspectionEvidenceBundle,
        valid_observation_frames: set[tuple[str, str, str, str]],
    ) -> float:
        truth = {(item.defect_id, item.target_id) for item in evidence.truth}
        detections = {
            (item.defect_id, item.target_id)
            for item in evidence.detections
            if (
                item.work_order_id,
                item.observation_id,
                item.frame_id,
                item.image_sha256,
            )
            in valid_observation_frames
        }
        true_positive = len(truth & detections)
        false_positive = len(detections - truth)
        false_negative = len(truth - detections)
        denominator = 2 * true_positive + false_positive + false_negative
        return 2 * true_positive / denominator if denominator else 0.0

    @staticmethod
    def _evaluate_goal(
        goal: InspectionGoal,
        metrics: tuple[MetricResult, ...],
        bounds: InspectionTheoreticalBounds,
    ) -> GoalResult:
        metric = next(
            (item for item in metrics if item.metric_id == goal.metric_id), None
        )
        if metric is None:
            raise ValueError(f"unsupported inspection metric: {goal.metric_id}")
        expected_evidence = _METRIC_EVIDENCE.get(goal.metric_id)
        if expected_evidence is None:
            raise ValueError(f"unsupported inspection metric: {goal.metric_id}")
        if goal.evidence != expected_evidence:
            raise ValueError("goal evidence category does not match metric evidence")
        upper_bound = InspectionVerifier._metric_upper_bound(goal.metric_id, bounds)
        impossible_goal = (
            goal.metric_id == "inspection.success_rate"
            and bounds.success_upper_bound == 0.0
        )
        threshold_exceeds_bound = (
            (goal.operator == "ge" and upper_bound < goal.threshold)
            or (goal.operator == "gt" and upper_bound <= goal.threshold)
            or (goal.operator == "eq" and upper_bound < goal.threshold)
        )
        passed = (
            not impossible_goal
            and not threshold_exceeds_bound
            and _compare(metric.value, goal.operator, goal.threshold)
        )
        return GoalResult(
            goal_id=goal.goal_id,
            passed=passed,
            metrics=(metric,),
            failure_class=None if passed else "threshold_not_met",
        )

    @staticmethod
    def _metric_upper_bound(
        metric_id: str,
        bounds: InspectionTheoreticalBounds,
    ) -> float:
        failed = set(bounds.failed_conditions)
        if metric_id in {
            "work_order.completion_rate",
            "inspection.success_rate",
        }:
            return bounds.success_upper_bound
        if metric_id == "inspection.arrival_rate":
            return 0.0 if "mission_deadline" in failed else 1.0
        if metric_id == "inspection.upload_rate":
            return 0.0 if "upload_deadline" in failed else 1.0
        if metric_id == "inspection.valid_observation_rate":
            return 0.0 if "imaging_resolution" in failed else 1.0
        if metric_id in {
            "inspection.detection.f1",
            "inspection.theoretical.detection_f1_upper_bound",
        }:
            return bounds.detection_f1_upper_bound
        if metric_id == "inspection.theoretical.success_upper_bound":
            return bounds.success_upper_bound
        raise ValueError(f"unsupported inspection metric: {metric_id}")


__all__ = [
    "InspectionVerificationReport",
    "InspectionVerifier",
    "core_verification_report",
]
