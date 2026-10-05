import ast
import copy
import hashlib
import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from aeroagentsim.integration import (
    ContractError,
    MappingArtifactReader,
    ReadOnlyReplay,
    canonical_bytes,
    enu_to_render,
    normalize_frame,
    relative_seconds,
    render_to_enu,
)
from aeroagentsim.integration.contracts import ns
from .conftest import replay_from


def changed_bundle(bundle, mutate):
    changed = copy.deepcopy(bundle)
    mutate(changed)
    return changed


def refresh_hashes(bundle):
    for entry in bundle["manifest"]["index"]:
        entry["sha256"] = hashlib.sha256(canonical_bytes(bundle["artifacts"][entry["artifact_id"]])).hexdigest()


def test_fixture_has_aerial_ground_lifetime_gap_and_revision_cases(replay, bundle):
    assert len(replay.frames) == 8
    assert {entity.kind for entity in replay.frames[0].entities} == {"aerial", "ground", "static"}
    assert "vehicle.transient" not in {entity.entity_id for entity in replay.frames[1].entities}
    assert "vehicle.transient" in {entity.entity_id for entity in replay.frames[2].entities}
    assert "vehicle.transient" not in {entity.entity_id for entity in replay.frames[5].entities}
    assert replay.frames[3].gaps_before == replay.gaps
    assert len(bundle["semantic_cases"]) == 3
    assert replay.provenance["is_actual_bench_data"] is False


def test_exact_previous_bounds_and_gap_are_explicit(replay):
    origin = ns(replay.engine_origin_ns)
    assert replay.seek(str(origin - 1)).status == "before_start"
    assert replay.seek(str(origin + 21_000_000_000), "previous").status == "after_end"
    assert replay.seek(str(origin + 2_000_000_000)).status == "not_indexed"
    previous = replay.seek(str(origin + 2_000_000_000), "previous")
    assert previous.status == "previous"
    assert previous.frame is replay.frames[1]
    assert previous.requested_time_ns != previous.frame.sim_time_ns
    assert replay.seek(replay.end_ns).frame is replay.frames[-1]
    for mode in ("exact", "previous"):
        result = replay.seek(str(origin + 5_500_000_000), mode)
        assert result.status == "gap"
        assert result.frame is None
        assert result.gap.reason == "authored_unavailable_interval"
    with pytest.raises(ContractError):
        replay.seek(replay.start_ns, "nearest")


@pytest.mark.parametrize("value", [0, 1.0, True, "01", "-1", "1.0", "1e9", "+1", " 1", None])
def test_ns_never_accepts_lossy_or_noncanonical_input(value):
    with pytest.raises(ContractError):
        ns(value)


def test_ns_subtraction_before_float_and_render_roundtrip(replay):
    origin = replay.engine_origin_ns
    assert ns(origin) > 2**53
    assert relative_seconds(str(ns(origin) + 1), origin) == 1e-9
    assert replay.frames[2].relative_seconds == 4.0
    for point in ((1.25, -3.5, 7.0), (0.0, 0.0, 0.0), (-10.0, 2.0, -5.0)):
        assert render_to_enu(enu_to_render(point)) == point
    assert enu_to_render((1.0, 2.0, 3.0)) == (1.0, 3.0, -2.0)


def test_source_and_normalized_frames_are_immutable(bundle):
    original = copy.deepcopy(bundle)
    artifacts = {key: canonical_bytes(value) for key, value in bundle["artifacts"].items()}
    reader = MappingArtifactReader(artifacts)
    replay = ReadOnlyReplay(canonical_bytes(bundle["manifest"]), reader)
    artifacts["frame_0"] = b"changed externally"
    for frame in reversed(replay.frames):
        assert replay.seek(frame.sim_time_ns).frame == frame
    assert bundle == original
    assert reader.read("frame_0") == canonical_bytes(original["artifacts"]["frame_0"])
    with pytest.raises(FrozenInstanceError):
        replay.frames[0].sim_time_ns = "0"
    with pytest.raises(TypeError):
        replay.frames[0].provenance["source"] = "changed"
    with pytest.raises(TypeError):
        replay.frames[0].entities[0].provenance["provider_id"] = "changed"


@pytest.mark.parametrize("field", ["attachment_id", "run_id", "run_epoch", "manifest_revision"])
def test_cross_namespace_frame_rejected(bundle, field):
    changed = changed_bundle(bundle, lambda value: value["artifacts"]["frame_0"]["run"].update({field: "other"}))
    refresh_hashes(changed)
    with pytest.raises(ContractError, match="another attachment"):
        replay_from(changed)


@pytest.mark.parametrize("artifact_id", ["../private", "/tmp/secret", "https://host/data", "file:///x", "with.dot"])
def test_manifest_cannot_request_paths_or_urls(bundle, artifact_id):
    bundle["manifest"]["index"][0]["artifact_id"] = artifact_id

    class NeverReader:
        def read(self, key):
            pytest.fail("reader must not be called for invalid artifact ID")

    with pytest.raises(ContractError, match="opaque"):
        ReadOnlyReplay(canonical_bytes(bundle["manifest"]), NeverReader())


def test_integrity_and_index_identity_rejected(bundle):
    bundle["artifacts"]["frame_0"]["entities"][0]["position_enu_m"][0] += 1
    with pytest.raises(ContractError, match="hash mismatch"):
        replay_from(bundle)
    refresh_hashes(bundle)
    bundle["manifest"]["index"][0]["stage_evidence_key"] = "wrong-stage"
    with pytest.raises(ContractError, match="index identity"):
        replay_from(bundle)


def test_origin_ordering_and_complete_entity_lifetime_rejected(bundle):
    changed = copy.deepcopy(bundle)
    changed["artifacts"]["frame_1"]["engine_origin_ns"] = "0"
    refresh_hashes(changed)
    with pytest.raises(ContractError, match="origin"):
        replay_from(changed)
    changed = copy.deepcopy(bundle)
    changed["manifest"]["index"][1], changed["manifest"]["index"][2] = (
        changed["manifest"]["index"][2],
        changed["manifest"]["index"][1],
    )
    with pytest.raises(ContractError, match="increase"):
        replay_from(changed)
    changed = copy.deepcopy(bundle)
    changed["artifacts"]["frame_1"]["entities"] = changed["artifacts"]["frame_1"]["entities"][1:]
    refresh_hashes(changed)
    with pytest.raises(ContractError, match="active entity omitted"):
        replay_from(changed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(coordinate_frame="NED"),
        lambda data: data["units"].update(velocity="km/h"),
        lambda data: data.update(frame_seq=True),
        lambda data: data["entities"][0].update(velocity_enu_mps=[True, 0, 0]),
        lambda data: data["entities"][0]["body"].update(radius_m=0),
        lambda data: data["entities"][0]["body"].update(shape="render-mesh"),
        lambda data: data["entities"].append(copy.deepcopy(data["entities"][0])),
        lambda data: data.update(unsupported_field="invented"),
        lambda data: data["entities"][0].update(valid_until_ns="0"),
    ],
)
def test_invalid_frame_fields_fail_closed(bundle, replay, mutation):
    data = bundle["artifacts"]["frame_0"]
    mutation(data)
    with pytest.raises(ContractError):
        normalize_frame(canonical_bytes(data), replay.run, replay.engine_origin_ns)


def test_null_motion_stays_null_and_validity_is_exclusive(replay):
    entity = replay.frames[3].entities[0]
    assert entity.position_enu_m is None and entity.velocity_enu_mps is None
    assert entity.valid_at(replay.frames[3].sim_time_ns)
    assert not entity.valid_at(entity.valid_until_ns)


def test_duplicate_and_nonfinite_json_rejected(replay):
    for raw in (b'{"schema":"x","schema":"y"}', b'{"value":NaN}'):
        with pytest.raises(ContractError):
            normalize_frame(raw, replay.run, replay.engine_origin_ns)


def test_integration_has_no_native_lifecycle_imports_or_calls(monkeypatch, bundle):
    import subprocess
    from aeroagentsim import Environment

    def prohibited(*args, **kwargs):
        pytest.fail("integration must not construct a simulation or start a process")

    monkeypatch.setattr(Environment, "__init__", prohibited)
    monkeypatch.setattr(subprocess, "Popen", prohibited)
    replay = replay_from(bundle)
    assert replay.seek(replay.start_ns).status == "exact"
    module = importlib.import_module("aeroagentsim.integration")
    for path in Path(module.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith(("traci", "simpy", "aeroagentsim.visualization")) for item in node.names)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("traci", "simpy", "aeroagentsim.visualization", "aeroagentsim.core")
                )
