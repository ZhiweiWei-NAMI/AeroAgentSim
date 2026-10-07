from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path, PurePosixPath

from aero_bench.artifacts.contracts import SealManifest, seal_manifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import NamedValue
from aero_bench.executor.contracts import VerifierWorkloadContract
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runtime.events import RunEventAudience
from aero_bench.verifier.streaming import (
    load_sealed_event_ledger_streaming as load_sealed_event_ledger,
)
from aero_bench.runtime.ledger import (
    VerifierEventLedger,
    verifier_event_segment_jsonl_bytes,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.formal_v2_contracts import (
    InspectionFormalVerifierConfigV3,
)
from aero_bench.tasks.inspection.formal_v2_sealed import verify_formal_v2_sealed
from aero_bench.tasks.inspection.integration import resolve_inspection_run_context
from aero_bench.tasks.inspection.verifier import (
    InspectionVerifier,
    core_verification_report,
)


class InspectionVerifierWorkloadError(RuntimeError):
    pass


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise InspectionVerifierWorkloadError(f"required environment is unavailable: {name}")
    return value


def _normalized_root(name: str) -> Path:
    raw = _required_environment(name)
    path = Path(raw)
    if not path.is_absolute() or os.fspath(path) != os.path.normpath(os.fspath(path)):
        raise InspectionVerifierWorkloadError(f"{name} must be an absolute normalized path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InspectionVerifierWorkloadError(f"{name} is unavailable") from error
    if resolved != path or not resolved.is_dir():
        raise InspectionVerifierWorkloadError(f"{name} must be a real directory")
    return resolved


def _canonical_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = parse_json_object(raw)
        expected = canonical_json_bytes(value) + b"\n"
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise InspectionVerifierWorkloadError(f"{label} is invalid") from error
    if raw != expected:
        raise InspectionVerifierWorkloadError(
            f"{label} must be canonical JSON with one trailing newline"
        )
    return value


def _load_contract() -> VerifierWorkloadContract:
    contract_path = Path(_required_environment("AERO_BENCH_CONTRACT"))
    if not contract_path.is_absolute():
        raise InspectionVerifierWorkloadError("AERO_BENCH_CONTRACT must be absolute")
    try:
        contract = VerifierWorkloadContract.model_validate(
            _canonical_object(contract_path, label="VerifierWorkloadContract")
        )
    except (TypeError, ValueError) as error:
        raise InspectionVerifierWorkloadError(
            "VerifierWorkloadContract validation failed"
        ) from error
    if (
        _required_environment("AERO_BENCH_ROLE") != "verifier"
        or _required_environment("AERO_BENCH_RUN_ID") != contract.run_id
        or _required_environment("AERO_BENCH_WORKLOAD_ID") != contract.workload_id
        or int(_required_environment("AERO_BENCH_SEED")) != contract.seed
    ):
        raise InspectionVerifierWorkloadError(
            "executor environment differs from VerifierWorkloadContract"
        )
    return contract


def _load_seal(root: Path, contract: VerifierWorkloadContract) -> SealManifest:
    manifest_path = root / "seal-manifest.json"
    try:
        seal = SealManifest.model_validate(
            _canonical_object(manifest_path, label="runtime seal manifest")
        )
        verified = seal_manifest(
            root=root,
            run_id=contract.run_id,
            attempt_id=seal.attempt_id,
            execution_scope=contract.run.execution_scope,
            event_chain_root=seal.event_chain_root,
            artifacts=seal.artifacts,
            manifest_name="seal-manifest.json",
        )
    except (OSError, TypeError, ValueError) as error:
        raise InspectionVerifierWorkloadError("runtime seal validation failed") from error
    if verified != seal:
        raise InspectionVerifierWorkloadError("runtime seal identity changed")
    if tuple(seal.artifacts) != tuple(
        sorted(
            seal.artifacts,
            key=lambda artifact: (
                artifact.producer_id,
                artifact.artifact_type,
                artifact.artifact_id,
            ),
        )
    ):
        raise InspectionVerifierWorkloadError("runtime seal artifact order is invalid")
    return seal


def _output_requirements(contract: VerifierWorkloadContract):
    report_requirements = tuple(
        requirement
        for requirement in contract.verifier.output_artifacts
        if requirement.artifact_type == "verification.report"
    )
    segment_requirements = tuple(
        requirement
        for requirement in contract.verifier.output_artifacts
        if requirement.artifact_type == "verification.event-segment"
    )
    if (
        len(report_requirements) != 1
        or len(segment_requirements) > 1
        or len(contract.verifier.output_artifacts)
        != len(report_requirements) + len(segment_requirements)
    ):
        raise InspectionVerifierWorkloadError(
            "Inspection Verifier outputs must be one report and at most one event segment"
        )
    report = report_requirements[0]
    if (
        report.producer_id != contract.workload_id
        or report.visibility != "public"
        or report.source_asset_id is not None
    ):
        raise InspectionVerifierWorkloadError(
            "verification.report output declaration is invalid"
        )
    segment = segment_requirements[0] if segment_requirements else None
    if segment is not None and (
        segment.producer_id != contract.workload_id
        or segment.visibility != "public"
        or segment.source_asset_id is not None
    ):
        raise InspectionVerifierWorkloadError(
            "verification.event-segment output declaration is invalid"
        )
    return report, segment


def _write_output(
    root: Path,
    *,
    relative_path: str,
    max_size_bytes: int,
    payload: bytes,
) -> None:
    relative = PurePosixPath(relative_path)
    destination = root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.resolve(strict=True) != destination.parent:
        raise InspectionVerifierWorkloadError(
            "verification output parent contains a symbolic link"
        )
    if len(payload) > max_size_bytes:
        raise InspectionVerifierWorkloadError("verification output exceeds its size bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise InspectionVerifierWorkloadError(
            "verification output could not be written"
        ) from error


def _verifier_event_segment(
    *,
    contract: VerifierWorkloadContract,
    seal: SealManifest,
    seal_root: Path,
    report: object,
    report_payload: bytes,
) -> bytes:
    runtime_ledger = load_sealed_event_ledger(
        run=contract.run,
        seal=seal,
        seal_root=seal_root,
    )
    segment = VerifierEventLedger(runtime_records=runtime_ledger.records)
    terminal_time = runtime_ledger.records[-1].event.time
    report_digest = hashlib.sha256(report_payload).hexdigest()
    goals = getattr(report, "goals", None)
    if not isinstance(goals, tuple) or not goals:
        raise InspectionVerifierWorkloadError(
            "verification report contains no evidence goals"
        )
    for goal in goals:
        goal_payload = goal.model_dump(mode="json")
        segment.append_event(
            source=contract.workload_id,
            workload_id=contract.workload_id,
            event_type=f"verifier.evidence.{goal.goal_id}",
            time=terminal_time,
            payload=(
                NamedValue(name="goal_id", value=goal.goal_id),
                NamedValue(name="passed", value=goal.passed),
                NamedValue(name="failure_class", value=goal.failure_class),
                NamedValue(
                    name="goal_result_json",
                    value=canonical_json_bytes(goal_payload).decode("utf-8"),
                ),
                NamedValue(name="report_sha256", value=report_digest),
                NamedValue(name="report_status", value=report.status),
            ),
            payload_schema_id="verifier.evidence.v1",
            interaction_type="verifier.evidence.v1",
            correlation_id=f"verification.{goal.goal_id}",
            visibility=(RunEventAudience(scope="public", audience_id=None),),
        )
    return verifier_event_segment_jsonl_bytes(
        runtime_records=runtime_ledger.records,
        verifier_records=segment.records,
    )


def run() -> None:
    contract = _load_contract()
    bundle_root = _normalized_root("AERO_BENCH_BUNDLE_DIR")
    seal_root = _normalized_root("AERO_BENCH_SEAL_DIR")
    artifact_root = _normalized_root("AERO_BENCH_ARTIFACT_DIR")
    seal = _load_seal(seal_root, contract)

    reader = BundleReader(bundle_root)
    package, resolved_bounds, feasibility = resolve_inspection_run_context(
        reader=reader,
        task=contract.run.task,
        environment=contract.run.environment,
        agents=contract.run.agents,
        scenario=contract.run.scenario,
    )
    if feasibility != contract.feasibility:
        raise InspectionVerifierWorkloadError(
            "Inspection feasibility differs from VerifierWorkloadContract"
        )
    verifier_config_document = reader.validate_schema_bound_file(
        contract.run.task.verifier.config
    )
    verifier_schema_version = (
        verifier_config_document.get("schema_version")
        if isinstance(verifier_config_document, dict)
        else None
    )
    if verifier_schema_version == "aero-bench.inspection-verifier/v3":
        try:
            formal_config = InspectionFormalVerifierConfigV3.model_validate(
                verifier_config_document
            )
        except (TypeError, ValueError) as error:
            raise InspectionVerifierWorkloadError(
                "Formal Inspection verifier configuration is invalid"
            ) from error
        report = verify_formal_v2_sealed(
            package=package,
            resolved_bounds=resolved_bounds,
            config=formal_config,
            bundle_root=bundle_root,
            run=contract.run,
            seal=seal,
            sealed_root=seal_root,
        )
    else:
        domain_report = InspectionVerifier(
            package,
            resolved_bounds=resolved_bounds,
        ).verify_sealed(
            bundle_root=bundle_root,
            run=contract.run,
            seal=seal,
            sealed_root=seal_root,
        )
        report = core_verification_report(domain_report, run=contract.run)
    report_requirement, segment_requirement = _output_requirements(contract)
    if any(artifact_root.iterdir()):
        raise InspectionVerifierWorkloadError("Verifier output root is not empty")
    payload = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
    _write_output(
        artifact_root,
        relative_path=report_requirement.relative_path,
        max_size_bytes=report_requirement.max_size_bytes,
        payload=payload,
    )
    if segment_requirement is not None:
        segment_payload = _verifier_event_segment(
            contract=contract,
            seal=seal,
            seal_root=seal_root,
            report=report,
            report_payload=payload,
        )
        _write_output(
            artifact_root,
            relative_path=segment_requirement.relative_path,
            max_size_bytes=segment_requirement.max_size_bytes,
            payload=segment_payload,
        )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["selfcheck"]:
        from selfcheck import main as selfcheck_main

        return selfcheck_main()
    if arguments != ["verify"]:
        print("usage: inspection-verifier {verify|selfcheck}", file=sys.stderr)
        return 2
    try:
        run()
    except Exception as error:
        print(f"inspection-verifier: FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
