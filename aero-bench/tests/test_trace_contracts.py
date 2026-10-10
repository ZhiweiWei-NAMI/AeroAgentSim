"""Strict Public Trace v3 contract tests."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from aero_bench.trace import PublicReplayFile, PublicReplayManifest, PublicTrace
from aero_bench.trace.contracts import ArtifactReference, PublicMetricResult
from tests.test_trace_projector import _project

RUN_ID = hashlib.sha256(b"trace-contract-run").hexdigest()
SCENARIO_DIGEST = hashlib.sha256(b"trace-contract-scenario").hexdigest()
CHAIN_ROOT = hashlib.sha256(b"trace-contract-chain").hexdigest()
TRACE_DIGEST = hashlib.sha256(b"trace-contract-trace").hexdigest()
ASSET_DIGEST = hashlib.sha256(b"trace-contract-asset").hexdigest()
OTHER_DIGEST = hashlib.sha256(b"trace-contract-other").hexdigest()


def _reference(**overrides: object) -> dict[str, object]:
    reference: dict[str, object] = {
        "artifact_id": "artifact.sensor-frame",
        "selector": "frames/frame.1",
        "digest": ASSET_DIGEST,
        "visibility": "public",
    }
    reference.update(overrides)
    return reference


def test_accepts_only_frozen_strict_v3_documents(tmp_path: Path) -> None:
    trace = _project(tmp_path)
    assert PublicTrace.model_validate(trace.model_dump(mode="json")) == trace

    with pytest.raises(ValidationError):
        trace.phase = "verified"  # type: ignore[misc]

    legacy = trace.model_dump(mode="json")
    legacy["schema_version"] = "aero-bench.public-trace/v2"
    with pytest.raises(ValidationError, match="public-trace/v3"):
        PublicTrace.model_validate(legacy)

    unknown = trace.model_dump(mode="json")
    unknown["entities"] = []
    with pytest.raises(ValidationError, match="entities"):
        PublicTrace.model_validate(unknown)

    private = trace.model_dump(mode="json")
    private["hidden_truth"] = {}
    with pytest.raises(ValidationError, match="hidden_truth"):
        PublicTrace.model_validate(private)


def test_artifact_references_are_public_normalized_and_real() -> None:
    reference = ArtifactReference.model_validate(_reference())
    assert reference.visibility == "public"

    for selector in (
        "https://cdn.example/frame",
        "../escape",
        "frames\\frame.1",
        "frames/\x01frame.1",
    ):
        with pytest.raises(ValidationError, match="selector"):
            ArtifactReference.model_validate(_reference(selector=selector))

    with pytest.raises(ValidationError, match="placeholder"):
        ArtifactReference.model_validate(_reference(digest="0" * 64))
    with pytest.raises(ValidationError, match="visibility"):
        ArtifactReference.model_validate(_reference(visibility="private"))
    omitted = _reference()
    del omitted["visibility"]
    with pytest.raises(ValidationError, match="visibility"):
        ArtifactReference.model_validate(omitted)


def test_replay_manifest_is_content_addressed_sorted_and_trace_bound() -> None:
    asset = PublicReplayFile(
        relative_path=f"assets/{ASSET_DIGEST}",
        sha256=ASSET_DIGEST,
        size_bytes=7,
    )
    trace = PublicReplayFile(
        relative_path="public-trace.json",
        sha256=TRACE_DIGEST,
        size_bytes=11,
    )
    manifest = PublicReplayManifest(
        schema_version="aero-bench.public-replay-manifest/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        event_chain_root=CHAIN_ROOT,
        trace_sha256=TRACE_DIGEST,
        files=(asset, trace),
    )
    assert manifest.files == (asset, trace)

    with pytest.raises(ValidationError, match="sorted and unique"):
        PublicReplayManifest(
            schema_version="aero-bench.public-replay-manifest/v1",
            run_id=RUN_ID,
            scenario_digest=SCENARIO_DIGEST,
            event_chain_root=CHAIN_ROOT,
            trace_sha256=TRACE_DIGEST,
            files=(trace, asset),
        )
    with pytest.raises(ValidationError, match="does not bind its trace"):
        PublicReplayManifest(
            schema_version="aero-bench.public-replay-manifest/v1",
            run_id=RUN_ID,
            scenario_digest=SCENARIO_DIGEST,
            event_chain_root=CHAIN_ROOT,
            trace_sha256=OTHER_DIGEST,
            files=(asset, trace),
        )
    with pytest.raises(ValidationError, match="must equal its SHA-256"):
        PublicReplayFile(
            relative_path=f"artifacts/{ASSET_DIGEST}",
            sha256=OTHER_DIGEST,
            size_bytes=1,
        )


def test_public_causality_must_be_present_backward_and_run_bound(
    tmp_path: Path,
) -> None:
    trace = _project(tmp_path)

    self_causal = trace.model_dump(mode="json")
    event = self_causal["events"][0]
    event["parent_event_id"] = event["event_id"]
    event["causal_event_ids"] = [event["event_id"]]
    with pytest.raises(ValidationError, match="must point backward"):
        PublicTrace.model_validate(self_causal)

    missing = trace.model_dump(mode="json")
    event = missing["events"][0]
    event["parent_event_id"] = "event.0000000000000000"
    event["causal_event_ids"] = ["event.0000000000000000"]
    with pytest.raises(ValidationError, match="absent from public projection"):
        PublicTrace.model_validate(missing)

    other_run = trace.model_dump(mode="json")
    other_run["events"][0]["run_id"] = OTHER_DIGEST
    with pytest.raises(ValidationError, match="belongs to another run"):
        PublicTrace.model_validate(other_run)


def test_specialized_sensor_projection_must_bind_runtime_evidence(
    tmp_path: Path,
) -> None:
    trace = _project(tmp_path)
    document = trace.model_dump(mode="json")
    document["sensor_frames"][0]["artifact"]["digest"] = OTHER_DIGEST

    with pytest.raises(ValidationError, match="absent from runtime artifacts"):
        PublicTrace.model_validate(document)


def test_public_scenario_frame_assets_must_remain_offline_and_public(
    tmp_path: Path,
) -> None:
    trace = _project(tmp_path)
    document = trace.model_dump(mode="json")
    geoid_id = document["scenario"]["frame_authority"][
        "geoid_correction_asset_id"
    ]
    document["scenario"]["assets"] = [
        asset
        for asset in document["scenario"]["assets"]
        if asset["asset_id"] != geoid_id
    ]

    with pytest.raises(ValidationError, match="geoid asset is unavailable"):
        PublicTrace.model_validate(document)


def test_public_verification_metrics_are_finite_bounded_and_evidenced() -> None:
    evidence = (ArtifactReference.model_validate(_reference()),)
    metric = PublicMetricResult(
        metric_id="inspection.valid_observation_rate",
        value=1.0,
        unit="ratio",
        evidence=evidence,
    )
    assert metric.value == 1.0

    with pytest.raises(ValidationError, match="finite"):
        PublicMetricResult(
            metric_id="inspection.valid_observation_rate",
            value=math.nan,
            unit="ratio",
            evidence=evidence,
        )
    with pytest.raises(ValidationError, match="unit"):
        PublicMetricResult(
            metric_id="inspection.valid_observation_rate",
            value=1.0,
            unit="https://example.com/unit",
            evidence=evidence,
        )
    with pytest.raises(ValidationError, match="evidence"):
        PublicMetricResult(
            metric_id="inspection.valid_observation_rate",
            value=1.0,
            unit="ratio",
            evidence=(),
        )
