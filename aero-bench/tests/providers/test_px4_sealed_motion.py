"""Synthetic native-stream contract tests, not formal Gazebo execution evidence."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord
from aero_bench.config.models import NamedValue
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.px4_gazebo.sealed_motion import validate_px4_snapshot_stream
from aero_bench.runtime.contracts import (
    ProviderEvent,
    SceneState,
    StageBarrier,
    StageReceipt,
    scene_state_digest_value,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
)
from aero_bench.runtime.sealed_motion import SealedMotionFrame
from aero_bench.serialization import canonical_json_bytes
from tests.tasks.test_logistics_runtime_bindings import _px4_config_document
from tests.tasks.test_logistics_runtime_hook import _closed_stage, build_fixture


def _rehashed(model, changes, digest_field, digest_function):
    values = {
        **{name: getattr(model, name) for name in type(model).model_fields},
        **changes,
        digest_field: "0" * 64,
    }
    unsigned = type(model).model_construct(**values)
    values[digest_field] = digest_function(unsigned)
    return type(model).model_validate(values)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    fixture = build_fixture(tmp_path_factory.mktemp("native-source-check"))
    run = fixture.resolved_run
    config = Px4GazeboConfig.model_validate(_px4_config_document(("uav.alpha",)))
    _, scene, event, _ = _closed_stage(fixture)
    payload = {item.name: item.value for item in event.payload}
    vehicle = {
        key: value
        for key, value in payload.items()
        if key not in {"contacts_json", "evidence_path", "evidence_sha256"}
    }
    vehicle["contacts"] = json.loads(payload["contacts_json"])
    snapshot = {"sim_time_ns": scene.at.sim_time_ns, "vehicles": [vehicle]}
    metadata = {
        "schema_version": "aero-bench.px4-evidence/v1",
        "provider_id": "flight",
        "run_id": run.run_id,
        "config_digest": "a" * 64,
        "runtime_image": "registry.invalid/px4@sha256:" + "b" * 64,
        "artifact_id": "px4.trajectory",
        "artifact_type": "trajectory",
        **{
            f"{name}_{part}": getattr(getattr(config, name), part)
            for name in ("px4", "gazebo", "mavsdk")
            for part in ("version", "commit")
        },
    }

    def record(operation, tick, state):
        return {
            **metadata,
            "operation": operation,
            "tick": tick,
            "sim_time_ns": state["sim_time_ns"],
            "snapshot": state,
            "snapshot_sha256": hashlib.sha256(canonical_json_bytes(state)).hexdigest(),
        }

    initial = {"sim_time_ns": 0, "vehicles": [{**vehicle, "simulation_time_ns": 0}]}
    documents = [record("reset", 0, initial), record("step_stage", 1, snapshot)]
    prefix = b"".join(canonical_json_bytes(item) + b"\n" for item in documents)
    # A later snapshot changes the final artifact hash. The step event must
    # retain its historical prefix hash, not be rewritten to that final hash.
    documents.append(record("snapshot", 1, snapshot))
    raw = b"".join(canonical_json_bytes(item) + b"\n" for item in documents)
    artifact = ArtifactRecord(
        artifact_id="px4.trajectory",
        artifact_type="trajectory",
        producer_id="flight",
        visibility="private",
        relative_path="trajectory/evidences.jsonl",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    payload["evidence_sha256"] = hashlib.sha256(prefix).hexdigest()
    event = ProviderEvent(
        **{
            **event.model_dump(mode="json"),
            "payload": tuple(
                NamedValue(name=key, value=value) for key, value in payload.items()
            ),
        }
    )
    receipt = _rehashed(
        scene.stage_barrier.receipts[0],
        {"state_digest": documents[1]["snapshot_sha256"]},
        "receipt_digest",
        stage_receipt_digest_value,
    )
    assert isinstance(receipt, StageReceipt)
    # Keep nested objects typed while calculating their canonical digests.
    barrier_values = {
        **{
            name: getattr(scene.stage_barrier, name)
            for name in StageBarrier.model_fields
        },
        "receipts": (receipt,),
        "receipt_digests": (receipt.receipt_digest,),
        "barrier_digest": "0" * 64,
    }
    barrier_values["barrier_digest"] = stage_barrier_digest_value(
        StageBarrier.model_construct(**barrier_values)
    )
    barrier = StageBarrier.model_validate(barrier_values)
    scene_values = {
        **{name: getattr(scene, name) for name in SceneState.model_fields},
        "stage_barrier": barrier,
        "scene_state_digest": "0" * 64,
    }
    scene_values["scene_state_digest"] = scene_state_digest_value(
        SceneState.model_construct(**scene_values)
    )
    scene = SceneState.model_validate(scene_values)
    frame = SealedMotionFrame(scene, (barrier,), (event,), (1,), 5)
    return dict(
        raw=raw,
        artifact=artifact,
        run_id=run.run_id,
        config=config,
        config_digest=metadata["config_digest"],
        runtime_image=metadata["runtime_image"],
        step_ns=fixture.environment.clock.step_ns,
        frames=(frame,),
    )


def _changed_bytes(source, documents):
    raw = b"".join(canonical_json_bytes(item) + b"\n" for item in documents)
    return {
        **source,
        "raw": raw,
        "artifact": source["artifact"].model_copy(
            update={"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        ),
    }


def test_native_snapshot_prefix_and_software_are_bound(source):
    records = validate_px4_snapshot_stream(**source)
    assert tuple(item.operation for item in records) == (
        "reset",
        "step_stage",
        "snapshot",
    )
    payload = {item.name: item.value for item in source["frames"][0].events[0].payload}
    assert payload["evidence_sha256"] != source["artifact"].sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "c" * 64),
        ("provider_id", "other"),
        ("config_digest", "d" * 64),
        ("runtime_image", "registry.invalid/other@sha256:" + "c" * 64),
        ("px4_version", "other"),
        ("px4_commit", "d" * 40),
        ("gazebo_version", "other"),
        ("gazebo_commit", "d" * 40),
        ("mavsdk_version", "other"),
        ("mavsdk_commit", "d" * 40),
        ("artifact_id", "another.trajectory"),
    ],
)
def test_rehashed_source_with_another_authority_is_rejected(source, field, value):
    documents = [json.loads(line) for line in source["raw"].splitlines()]
    documents[1][field] = value
    with pytest.raises(ValueError, match="identity differs"):
        validate_px4_snapshot_stream(**_changed_bytes(source, documents))


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_sha256", "d" * 64),
        ("evidence_path", "another/source.jsonl"),
        ("armed", False),
        ("battery_percent", 1.0),
        ("collision_contact", True),
    ],
)
def test_source_event_cannot_rewrite_snapshot_facts(source, field, value):
    frame = source["frames"][0]
    event = frame.events[0]
    payload = tuple(
        NamedValue(name=item.name, value=value if item.name == field else item.value)
        for item in event.payload
    )
    event = event.model_copy(update={"payload": payload})
    with pytest.raises(ValueError, match="facts or trajectory prefix"):
        validate_px4_snapshot_stream(
            **{**source, "frames": (replace(frame, events=(event,)),)}
        )


def test_changed_sealed_artifact_bytes_are_rejected(source):
    with pytest.raises(ValueError, match="hash or size"):
        validate_px4_snapshot_stream(**{**source, "raw": source["raw"] + b"\n"})


@pytest.mark.parametrize(
    "change", ["reset", "gap", "omit_vehicle", "duplicate_vehicle", "digest"]
)
def test_incomplete_or_inconsistent_native_snapshot_is_rejected(source, change):
    documents = [json.loads(line) for line in source["raw"].splitlines()]
    if change == "reset":
        documents[0]["operation"] = "snapshot"
    elif change == "gap":
        documents[1]["tick"] = 2
    elif change == "omit_vehicle":
        documents[1]["snapshot"]["vehicles"] = []
    elif change == "duplicate_vehicle":
        documents[1]["snapshot"]["vehicles"] *= 2
    else:
        documents[1]["snapshot_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        validate_px4_snapshot_stream(**_changed_bytes(source, documents))


def test_extra_closed_source_event_is_rejected(source):
    frame = source["frames"][0]
    with pytest.raises(ValueError, match="omit or repeat"):
        validate_px4_snapshot_stream(
            **{**source, "frames": (replace(frame, events=frame.events * 2),)}
        )


def test_array_format_and_noncanonical_framing_are_rejected(source):
    documents = [json.loads(line) for line in source["raw"].splitlines()]
    for raw in (
        canonical_json_bytes(documents),
        source["raw"].rstrip(b"\n"),
        b'{"run_id":"a","run_id":"b"}\n',
    ):
        changed = {
            **source,
            "raw": raw,
            "artifact": source["artifact"].model_copy(
                update={
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
            ),
        }
        with pytest.raises(ValueError):
            validate_px4_snapshot_stream(**changed)
