from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError, model_validator

from aero_bench.artifacts.contracts import (
    ArtifactRecord,
    SealManifest,
    seal_manifest,
)
from aero_bench.config.models import ArtifactRequirement, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.verifier.streaming import (
    load_sealed_event_ledger_streaming as load_sealed_event_ledger,
)
from aero_bench.runtime.ledger import verifier_event_segment_from_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.contracts import (
    VerificationReport,
    validate_report_against_run,
)


_REPORT_ARTIFACT_TYPE = "verification.report"


class VerificationOutputError(ValueError):
    """A verification output failed the independent, fail-closed boundary."""


class ValidatedVerificationOutput(StrictModel):
    """The sealed verifier output and its independently validated report."""

    seal: SealManifest
    report: VerificationReport

    @model_validator(mode="after")
    def seal_matches_report(self) -> "ValidatedVerificationOutput":
        report_records = tuple(
            artifact
            for artifact in self.seal.artifacts
            if artifact.artifact_type == _REPORT_ARTIFACT_TYPE
        )
        if len(report_records) != 1 or report_records[0].visibility != "public":
            raise ValueError(
                "validated output seal must contain exactly one public "
                "verification.report artifact"
            )
        if self.seal.run_id != self.report.run_id:
            raise ValueError("verification seal run_id differs from report")
        if self.seal.execution_scope != self.report.execution_scope:
            raise ValueError("verification seal execution_scope differs from report")
        return self


def load_and_validate_verification_output(
    *,
    run: ResolvedRunSpec,
    runtime_seal: SealManifest,
    output_seal: SealManifest,
    output_root: Path,
    runtime_root: Path | None = None,
) -> ValidatedVerificationOutput:
    """Load one sealed verifier report and validate all of its trust bindings.

    The report is read only after the output inventory and the seal bindings have
    been checked.  No verifier is executed in this process and no report is
    synthesized when any part of the contract is missing or inconsistent.
    """

    _validate_seal_bindings(run, runtime_seal, output_seal)
    requirement = _find_report_requirement(run)
    records = _validate_output_inventory(run, output_seal)
    record = records[requirement.artifact_id]
    report_bytes = _read_output_bytes(
        output_root=output_root,
        requirement=requirement,
        record=record,
        output_seal=output_seal,
    )
    payload = _parse_canonical_json(report_bytes)
    try:
        report = VerificationReport.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as error:
        raise VerificationOutputError(
            f"verification report contract is invalid: {error}"
        ) from error

    try:
        validate_report_against_run(report, run)
    except ValueError as error:
        raise VerificationOutputError(
            f"verification report does not match ResolvedRun: {error}"
        ) from error

    _validate_executor_validation_status(report, run)
    _validate_metric_evidence(report, runtime_seal)
    _validate_verifier_event_segment(
        run=run,
        runtime_seal=runtime_seal,
        runtime_root=runtime_root,
        output_seal=output_seal,
        output_root=output_root,
        report=report,
        report_bytes=report_bytes,
        records=records,
    )
    return ValidatedVerificationOutput(seal=output_seal, report=report)


def _validate_seal_bindings(
    run: ResolvedRunSpec,
    runtime_seal: SealManifest,
    output_seal: SealManifest,
) -> None:
    if runtime_seal.run_id != run.run_id:
        raise VerificationOutputError(
            "runtime seal run_id does not match the ResolvedRun"
        )
    if runtime_seal.execution_scope != run.execution_scope:
        raise VerificationOutputError(
            "runtime seal execution_scope does not match the ResolvedRun"
        )
    if output_seal.run_id != runtime_seal.run_id or output_seal.attempt_id != runtime_seal.attempt_id:
        raise VerificationOutputError("output seal run_id differs from runtime seal")
    if output_seal.execution_scope != runtime_seal.execution_scope:
        raise VerificationOutputError(
            "output seal execution_scope differs from runtime seal"
        )
    if output_seal.event_chain_root != runtime_seal.event_chain_root:
        raise VerificationOutputError(
            "output seal event_chain_root differs from runtime seal"
        )


def _find_report_requirement(run: ResolvedRunSpec) -> ArtifactRequirement:
    requirements = run.verification_outputs
    report_requirements = tuple(
        requirement
        for requirement in requirements
        if requirement.artifact_type == _REPORT_ARTIFACT_TYPE
    )
    if len(report_requirements) != 1:
        raise VerificationOutputError(
            "ResolvedRun verification_outputs must contain exactly one "
            "verification.report declaration"
        )
    return report_requirements[0]


def _validate_output_inventory(
    run: ResolvedRunSpec,
    output_seal: SealManifest,
) -> dict[str, ArtifactRecord]:
    requirements = run.verification_outputs
    requirements_by_id = {
        requirement.artifact_id: requirement for requirement in requirements
    }
    if len(requirements_by_id) != len(requirements):
        raise VerificationOutputError(
            "ResolvedRun verification output declarations repeat an artifact_id"
        )
    records_by_id = {record.artifact_id: record for record in output_seal.artifacts}
    if len(records_by_id) != len(output_seal.artifacts):
        raise VerificationOutputError("output seal repeats an artifact_id")
    if set(records_by_id) != set(requirements_by_id):
        raise VerificationOutputError(
            "output seal inventory artifact_id set does not exactly match "
            "verification output declarations"
        )
    for artifact_id, requirement in requirements_by_id.items():
        record = records_by_id[artifact_id]
        for field in (
            "artifact_id",
            "artifact_type",
            "producer_id",
            "visibility",
            "relative_path",
        ):
            if getattr(record, field) != getattr(requirement, field):
                raise VerificationOutputError(
                    f"output seal record {field} disagrees with verification output "
                    f"declaration for {artifact_id}"
                )
        if record.size_bytes > requirement.max_size_bytes:
            raise VerificationOutputError(
                f"output seal artifact exceeds declared max_size_bytes: {artifact_id}"
            )
    report_requirements = tuple(
        requirement
        for requirement in requirements
        if requirement.artifact_type == _REPORT_ARTIFACT_TYPE
    )
    if len(report_requirements) != 1:
        raise VerificationOutputError(
            "ResolvedRun verification_outputs must contain exactly one "
            "verification.report declaration"
        )
    if report_requirements[0].visibility != "public":
        raise VerificationOutputError(
            "verification.report declaration must have public visibility"
        )
    return records_by_id


def _read_output_bytes(
    *,
    output_root: Path,
    requirement: ArtifactRequirement,
    record: ArtifactRecord,
    output_seal: SealManifest,
) -> bytes:
    root = _normalized_root(output_root)
    relative_path = _normalized_relative_path(
        requirement.relative_path, "verification output relative_path"
    )
    _validate_output_root_against_seal(root, output_seal)
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    if candidate.is_symlink():
        raise VerificationOutputError("verification report path is a symbolic link")
    try:
        resolved_candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise VerificationOutputError(
            "verification report path is unavailable"
        ) from error
    if resolved_candidate != candidate:
        raise VerificationOutputError(
            "verification report path contains a symbolic link"
        )
    if not resolved_candidate.is_relative_to(root):
        raise VerificationOutputError("verification report path escapes output root")
    try:
        descriptor = resolved_candidate.open("rb")
    except OSError as error:
        raise VerificationOutputError("verification report cannot be opened") from error
    with descriptor:
        try:
            before = os.fstat(descriptor.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise VerificationOutputError(
                    "verification report is not a regular file"
                )
            if before.st_size != record.size_bytes:
                raise VerificationOutputError(
                    "verification report size does not match output seal"
                )
            if before.st_size > requirement.max_size_bytes:
                raise VerificationOutputError(
                    "verification report exceeds declared max_size_bytes"
                )
            payload = descriptor.read()
            after = os.fstat(descriptor.fileno())
        except OSError as error:
            raise VerificationOutputError(
                "verification report could not be read"
            ) from error
    if after.st_size != before.st_size:
        raise VerificationOutputError("verification report changed while reading")
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != record.sha256:
        raise VerificationOutputError(
            "verification report digest does not match output seal"
        )
    return payload


def _normalized_root(output_root: Path) -> Path:
    if not isinstance(output_root, Path):
        raise VerificationOutputError("output_root must be a pathlib.Path")
    if not output_root.is_absolute():
        raise VerificationOutputError("output_root must be an absolute normalized path")
    if os.fspath(output_root) != os.path.normpath(os.fspath(output_root)):
        raise VerificationOutputError("output_root must be an absolute normalized path")
    try:
        resolved_root = output_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise VerificationOutputError("output_root is unavailable") from error
    if resolved_root != output_root:
        raise VerificationOutputError("output_root contains a symbolic link")
    if not resolved_root.is_dir():
        raise VerificationOutputError("output_root is not a directory")
    return resolved_root


def _normalized_relative_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value.strip() != value
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or str(path) != value
    ):
        raise VerificationOutputError(f"{label} is not normalized and relative")
    return value


def _validate_output_root_against_seal(
    root: Path,
    output_seal: SealManifest,
) -> None:
    try:
        verified = seal_manifest(
            root=root,
            run_id=output_seal.run_id,
            attempt_id=output_seal.attempt_id,
            execution_scope=output_seal.execution_scope,
            event_chain_root=output_seal.event_chain_root,
            artifacts=output_seal.artifacts,
            manifest_name="verification-manifest.json",
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise VerificationOutputError(
            f"output_root does not match output seal: {error}"
        ) from error
    if verified != output_seal:
        raise VerificationOutputError("output_root does not match output seal exactly")


def _parse_canonical_json(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise VerificationOutputError(
            "verification report is not valid UTF-8"
        ) from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise VerificationOutputError(
            f"verification report is not strict JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise VerificationOutputError("verification report JSON must be an object")
    try:
        canonical = canonical_json_bytes(payload) + b"\n"
    except (TypeError, ValueError, UnicodeError) as error:
        raise VerificationOutputError(
            f"verification report cannot be canonicalized: {error}"
        ) from error
    if raw != canonical:
        raise VerificationOutputError(
            "verification report must be canonical JSON with one trailing newline"
        )
    return payload


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number is not allowed")
    return parsed


def _validate_executor_validation_status(
    report: VerificationReport,
    run: ResolvedRunSpec,
) -> None:
    if run.execution_scope == "executor_validation" and report.status != "invalid":
        raise VerificationOutputError(
            "executor_validation reports must have status=invalid"
        )


def _validate_metric_evidence(
    report: VerificationReport,
    runtime_seal: SealManifest,
) -> None:
    runtime_artifact_ids = {artifact.artifact_id for artifact in runtime_seal.artifacts}
    for goal in report.goals:
        for metric in goal.metrics:
            for evidence in metric.evidence:
                if evidence.artifact_id not in runtime_artifact_ids:
                    raise VerificationOutputError(
                        "verification metric evidence names an artifact absent from "
                        f"the runtime seal: {evidence.artifact_id}"
                    )
                if evidence.selector is not None:
                    _validate_selector(evidence.selector)


def _validate_verifier_event_segment(
    *,
    run: ResolvedRunSpec,
    runtime_seal: SealManifest,
    runtime_root: Path | None,
    output_seal: SealManifest,
    output_root: Path,
    report: VerificationReport,
    report_bytes: bytes,
    records: dict[str, ArtifactRecord],
) -> None:
    requirements = tuple(
        requirement
        for requirement in run.verification_outputs
        if requirement.artifact_type == "verification.event-segment"
    )
    if len(requirements) > 1:
        raise VerificationOutputError(
            "ResolvedRun repeats verification.event-segment output"
        )
    if not requirements:
        return
    if runtime_root is None:
        raise VerificationOutputError(
            "runtime_root is required to validate the verifier event segment"
        )
    requirement = requirements[0]
    if requirement.visibility != "public":
        raise VerificationOutputError(
            "verification.event-segment declaration must be public"
        )
    record = records[requirement.artifact_id]
    segment_bytes = _read_output_bytes(
        output_root=output_root,
        requirement=requirement,
        record=record,
        output_seal=output_seal,
    )
    try:
        runtime_ledger = load_sealed_event_ledger(
            run=run,
            seal=runtime_seal,
            seal_root=runtime_root,
        )
        segment = verifier_event_segment_from_jsonl_bytes(
            runtime_records=runtime_ledger.records,
            content=segment_bytes,
        )
    except (TypeError, ValueError) as error:
        raise VerificationOutputError(
            "verification.event-segment does not continue the sealed runtime ledger"
        ) from error

    expected_goals = {goal.goal_id: goal for goal in report.goals}
    actual_goal_events: dict[str, object] = {}
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    expected_source = run.task.verifier.verifier_id
    terminal_time = runtime_ledger.records[-1].event.time
    for record in segment.records:
        event = record.event
        interaction = event.interaction
        if event.source != expected_source or event.workload_id != expected_source:
            raise VerificationOutputError(
                "verifier event segment source differs from ResolvedRun"
            )
        if event.time != terminal_time:
            raise VerificationOutputError(
                "verifier evidence must bind the terminal runtime simulation time"
            )
        if interaction is None:
            raise VerificationOutputError(
                "verifier event segment contains a non-interaction event"
            )
        if interaction.interaction_type in {
            "process.stdout.v1",
            "process.stderr.v1",
        }:
            continue
        if interaction.interaction_type != "verifier.evidence.v1":
            raise VerificationOutputError(
                "verifier event segment contains an unsupported interaction"
            )
        if tuple((item.scope, item.audience_id) for item in event.visibility) != (
            ("public", None),
        ):
            raise VerificationOutputError("verifier evidence must be public")
        payload = {item.name: item.value for item in event.payload}
        expected_fields = {
            "failure_class",
            "goal_id",
            "goal_result_json",
            "passed",
            "report_sha256",
            "report_status",
        }
        goal_id = payload.get("goal_id")
        if (
            set(payload) != expected_fields
            or not isinstance(goal_id, str)
            or goal_id not in expected_goals
            or goal_id in actual_goal_events
            or payload["report_sha256"] != report_digest
            or payload["report_status"] != report.status
            or event.correlation_id != f"verification.{goal_id}"
            or event.event_type != f"verifier.evidence.{goal_id}"
        ):
            raise VerificationOutputError(
                "verifier evidence interaction identity is invalid"
            )
        goal = expected_goals[goal_id]
        if (
            payload["passed"] != goal.passed
            or payload["failure_class"] != goal.failure_class
            or payload["goal_result_json"]
            != canonical_json_bytes(goal.model_dump(mode="json")).decode("utf-8")
        ):
            raise VerificationOutputError(
                "verifier evidence interaction differs from verification report"
            )
        actual_goal_events[goal_id] = event
    if set(actual_goal_events) != set(expected_goals):
        raise VerificationOutputError(
            "verifier event segment does not cover every verification goal"
        )


def _validate_selector(selector: str) -> None:
    if not selector or selector.strip() != selector:
        raise VerificationOutputError(
            "verification evidence selector must be non-empty and normalized"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in selector):
        raise VerificationOutputError(
            "verification evidence selector contains a control character"
        )
    if "\\" in selector or "%" in selector or "://" in selector:
        raise VerificationOutputError(
            "verification evidence selector cannot contain URL or escape syntax"
        )
    try:
        parsed_url = urlsplit(selector)
    except ValueError as error:
        raise VerificationOutputError(
            "verification evidence selector is not a valid normalized reference"
        ) from error
    if parsed_url.scheme or parsed_url.netloc:
        raise VerificationOutputError("verification evidence selector cannot be an URL")
    if selector.startswith("/"):
        _validate_json_pointer_selector(selector)
        return
    path = PurePosixPath(selector)
    if (
        path.is_absolute()
        or path == PurePosixPath(".")
        or str(path) != selector
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise VerificationOutputError(
            "verification evidence selector must be normalized and relative"
        )


def _validate_json_pointer_selector(selector: str) -> None:
    if selector != "/" and "//" in selector:
        raise VerificationOutputError(
            "verification evidence JSON pointer selector is not normalized"
        )
    for token in selector[1:].split("/"):
        if token in {".", ".."}:
            raise VerificationOutputError(
                "verification evidence selector cannot escape its artifact"
            )
        index = 0
        while index < len(token):
            if token[index] != "~":
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise VerificationOutputError(
                    "verification evidence JSON pointer selector has invalid escape"
                )
            index += 2


__all__ = [
    "ValidatedVerificationOutput",
    "VerificationOutputError",
    "load_and_validate_verification_output",
]
