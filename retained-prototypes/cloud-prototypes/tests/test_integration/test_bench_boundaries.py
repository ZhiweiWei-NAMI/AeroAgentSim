import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aeroagentsim.integration import ContractError, RunIdentity, SemanticBinding, SemanticSession
from aeroagentsim.integration.bench import BenchContext, BenchStreamCursors, normalize_bench_scene
from aeroagentsim.integration.network import (
    PublicEventIdentity,
    join_link_evidence,
    normalize_link_properties,
    normalize_network_geometry,
    normalize_provider_receipt,
)


@pytest.fixture
def bench_bundle():
    return json.loads((Path(__file__).parent / "fixtures" / "bench_boundaries.json").read_bytes())


def context(bundle):
    return BenchContext(
        RunIdentity("synthetic-bench-attachment", bundle["scene_state"]["run_id"], "epoch-1", "manifest-1"),
        bundle["scene_state"]["scenario_digest"],
        bundle["engine_origin_ns"],
    )


def project(bundle, barrier=None, verifier=None):
    ids = [entity["entity_id"] for entity in bundle["resolved_entities"]]
    return normalize_bench_scene(
        bundle["scene_state"],
        tuple(bundle["resolved_entities"]),
        context(bundle),
        0,
        dict(zip(ids, ("aerial", "ground", "static"))),
        {entity: (bundle["engine_origin_ns"], None) for entity in ids},
        {entity: str(bundle["scene_state"]["at"]["sim_time_ns"] + 1_000_000_000) for entity in ids},
        barrier or (lambda value: value == {"fixture_only": "caller-validated-placeholder"}),
        digest_verifier=verifier,
        source_cursors={"after_transition": -1, "after_scene_tick": 1, "after_event_sequence": -1},
    )


def test_documented_motion_paths_exact_entity_join_and_unknown_optional_fields(bench_bundle):
    original = copy.deepcopy(bench_bundle)
    frame = project(bench_bundle)
    assert frame.sim_time_ns == str(bench_bundle["scene_state"]["at"]["sim_time_ns"])
    assert frame.relative_seconds == 1.000000001
    by_id = {entity.entity_id: entity for entity in frame.entities}
    actor = by_id["aircraft.alpha"]
    assert actor.position_enu_m == (1, 2, 3)
    assert actor.velocity_enu_mps == (3, 4, 2)
    assert actor.body is None
    assert actor.provenance["optional_state"]["mode"] is None
    assert actor.provenance["optional_state"]["armed"] is None
    assert actor.provenance["optional_state"]["battery"] is None
    assert actor.provenance["pose"]["position"]["agl_m"] == 10
    assert actor.provenance["pose"]["position"]["amsl_m"] == 100
    assert actor.provenance["pose"]["position"]["wgs84"]["ellipsoid_height_m"] == 120
    assert actor.provenance["pose"]["orientation_enu"]["qw"] == 1
    assert frame.provenance["digest_verification"] == "format_and_identity_only"
    assert frame.provenance["source_cursors"]["after_scene_tick"] == 1
    assert bench_bundle == original
    binding = SemanticBinding(
        "actor",
        ("aircraft.alpha",),
        ("actor_moving", "vertical_ascent", "vertical_descent", "actor_stationary"),
        "requested-targets-unverified-native-registry",
        {"hu.actor.speed_mps": "actor.speed_mps", "hu.actor.vertical_speed_mps": "actor.vertical_speed_mps"},
    )
    evidence = SemanticSession().evaluate(frame, (binding,), "eval-1", "binding-1")[0]
    assert dict(evidence.request.current.values) == {"hu.actor.speed_mps": 5, "hu.actor.vertical_speed_mps": 2}
    assert evidence.request.history_censored  # No fabricated observed tick 0.
    assert not evidence.evaluator_available


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["scene_state"]["at"].update(tick=0),
        lambda data: data["scene_state"]["at"].update(sim_time_ns=1.0),
        lambda data: data["scene_state"]["samples"][0].update(entity_id="invalid/alias"),
        lambda data: data["scene_state"]["samples"][0].update(stage="network"),
        lambda data: data["scene_state"]["samples"][0].update(run_id="9" * 64),
        lambda data: data["scene_state"]["samples"][0]["at"].update(tick=2),
        lambda data: data["scene_state"]["samples"][0]["linear_velocity_ned"].update(down_mps=99),
        lambda data: data["scene_state"]["samples"].pop(),
        lambda data: data["resolved_entities"][0].pop("source_provider_id"),
        lambda data: data["resolved_entities"][0].update(source_provider_id="wrong.provider"),
        lambda data: data["scene_state"].update(scene_state_digest="A" * 64),
        lambda data: data["scene_state"]["samples"][0]["pose"]["position"]["ned"].update(down_m=99),
    ],
)
def test_motion_consistency_checks_fail_closed(bench_bundle, mutation):
    mutation(bench_bundle)
    with pytest.raises(ContractError):
        project(bench_bundle)


def test_injected_barrier_and_digest_verifiers_are_enforced(bench_bundle):
    with pytest.raises(ContractError, match="barrier"):
        project(bench_bundle, barrier=lambda value: False)
    with pytest.raises(ContractError, match="digest verification"):
        project(bench_bundle, verifier=lambda value: False)


def envelopes(bundle):
    run = bundle["scene_state"]["run_id"]
    return {
        "scene.state": {
            "schema_version": "aero-bench.scene-state-stream-event/v1",
            "run_id": run,
            "scene_state": bundle["scene_state"],
        },
        "run.transition": {
            "schema_version": "aero-bench.run-transition-event/v1",
            "run_id": run,
            "transition": {"sequence": 0, "fixture_only": True},
        },
        "run.event": {
            "schema_version": "aero-bench.public-run-event-stream-event/v1",
            "run_id": run,
            "event": {
                "run_id": run,
                "sequence": 0,
                "event_id": "event.0000000000000000",
                "event_digest": "8" * 64,
                "at": bundle["scene_state"]["at"],
            },
        },
    }


def test_three_source_cursors_are_independent_and_advance_only_after_processing(bench_bundle):
    ctx = context(bench_bundle)
    cursors = BenchStreamCursors(ctx.run)
    assert dict(cursors.query()) == {"after_transition": -1, "after_scene_tick": 0, "after_event_sequence": -1}
    events = envelopes(bench_bundle)
    calls = []
    for family in ("scene.state", "run.event", "run.transition"):
        previous = cursors

        def process(value):
            calls.append(value)
            assert previous.canonical_sequence == len(calls) - 1
            return "processed"

        cursors, result = cursors.process(family, events[family], process, ctx.run)
        assert result == "processed"
    assert dict(cursors.query()) == {"after_transition": 0, "after_scene_tick": 1, "after_event_sequence": 0}
    assert cursors.canonical_sequence == 3
    duplicate, result = cursors.process(
        "scene.state", events["scene.state"], lambda _: pytest.fail("duplicate processed"), ctx.run
    )
    assert duplicate is cursors and result is None
    events["scene.state"]["scene_state"]["scene_state_digest"] = "9" * 64
    with pytest.raises(ContractError, match="conflicting duplicate"):
        cursors.process("scene.state", events["scene.state"], lambda _: None, ctx.run)


def test_cursor_namespace_gaps_wire_id_and_failed_processing(bench_bundle):
    ctx, events = context(bench_bundle), envelopes(bench_bundle)
    cursors = BenchStreamCursors(ctx.run)
    with pytest.raises(ContractError, match="epoch"):
        cursors.process("run.event", events["run.event"], lambda _: None, replace(ctx.run, run_epoch="other"))
    with pytest.raises(ContractError, match="wire ID"):
        cursors.process("run.event", events["run.event"], lambda _: None, ctx.run, sse_id="event.0")

    def fail(value):
        raise ContractError("payload rejected by full family validator")

    with pytest.raises(ContractError, match="payload rejected"):
        cursors.process("scene.state", events["scene.state"], fail, ctx.run)
    assert cursors.after_scene_tick == 0
    events["scene.state"]["scene_state"]["at"]["tick"] = 3
    with pytest.raises(ContractError, match="cursor gap"):
        cursors.process("scene.state", events["scene.state"], lambda _: None, ctx.run)


def geometry(bundle):
    return normalize_network_geometry(
        bundle["network_geometry"],
        context(bundle),
        {"node.air": ("aircraft.alpha", "endpoint.air"), "node.ground": ("vehicle.main", "endpoint.ground")},
    )


def event_identity(bundle, scene=None):
    return PublicEventIdentity(
        context(bundle), 1, str(bundle["scene_state"]["at"]["sim_time_ns"]), 0, "event.0000000000000000", "8" * 64, scene
    )


def test_geometry_properties_receipt_remain_separate_and_units_direction_preserved(bench_bundle):
    frame = geometry(bench_bundle)
    props = normalize_link_properties(bench_bundle["public_link_document"], event_identity(bench_bundle))
    receipt = normalize_provider_receipt(bench_bundle["provider_receipt"], context(bench_bundle))
    assert not hasattr(frame.links[0], "rssi_dbm")
    assert not hasattr(receipt, "links")
    assert "delivered_throughput_bps" not in props.properties
    assert receipt.observations["delivered_throughput_bps"].unit == "bps"
    assert receipt.observations["simulator_time_ns"].value == str(bench_bundle["scene_state"]["at"]["sim_time_ns"])
    assert receipt.count_semantics == "accumulation_reset_window_unresolved"
    assert props.sensitivity_state == "above-sensitivity"
    assert props.properties["forward_rssi_dbm"].value == -70
    assert props.properties["reverse_rssi_dbm"].value == -72
    assert props.properties["forward_rssi_dbm"].unit == "dBm"
    assert props.properties["reverse_snr_db"].unit == "dB"
    assert props.properties["source_queue_bytes"].unit == "bytes"
    assert props.properties["obstruction_volume_ids"].value == ("building.one", "building.two")
    assert props.properties["forward_rssi_dbm"].provenance["source"] == "authored-fixture"
    assert join_link_evidence(frame, props).status == "pending"
    assert join_link_evidence(frame, props).reason == "source_event_scene_link_unverified"
    verified = replace(props, identity=event_identity(bench_bundle, frame.scene_state_digest))
    assert join_link_evidence(frame, verified).status == "joined"
    assert join_link_evidence(frame, replace(verified, identity=replace(verified.identity, tick=2))).status == "pending"
    assert join_link_evidence(frame, replace(verified, target_entity_id="other.entity")).status == "pending"
    changed_context = replace(verified.identity.context, run=replace(verified.identity.context.run, run_epoch="other"))
    assert (
        join_link_evidence(frame, replace(verified, identity=replace(verified.identity, context=changed_context))).status
        == "pending"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc["public_properties"].append(copy.deepcopy(doc["public_properties"][0])),
        lambda doc: next(item for item in doc["public_properties"] if item["name"] == "obstruction_volume_ids").update(
            value="__import__('os')"
        ),
        lambda doc: next(item for item in doc["public_properties"] if item["name"] == "obstruction_volume_ids").update(
            value='{"not":"a-list"}'
        ),
        lambda doc: next(item for item in doc["public_properties"] if item["name"] == "obstruction_volume_count").update(
            value=3
        ),
        lambda doc: next(item for item in doc["public_properties"] if item["name"] == "source_queue_bytes").update(value=-1),
        lambda doc: doc["public_properties"].append({"name": "per_link_throughput_guessed", "value": 2048, "provenance": {}}),
    ],
)
def test_malformed_duplicate_or_unsupported_network_properties_rejected(bench_bundle, mutation):
    mutation(bench_bundle["public_link_document"])
    with pytest.raises(ContractError):
        normalize_link_properties(bench_bundle["public_link_document"], event_identity(bench_bundle))


def test_empty_network_arrays_null_digests_and_missing_properties_preserved(bench_bundle):
    data = bench_bundle["network_geometry"]
    data.update(nodes=[], links=[])
    frame = normalize_network_geometry(data, context(bench_bundle), {})
    assert frame.nodes == frame.links == ()
    for item in bench_bundle["provider_receipt"]["payload"]:
        if item["name"] in ("accepted_scene_state_digest", "link_state_digest"):
            item["value"] = None
    receipt = normalize_provider_receipt(bench_bundle["provider_receipt"], context(bench_bundle))
    assert receipt.observations["accepted_scene_state_digest"].value is None
    assert receipt.observations["link_state_digest"].value is None
    bench_bundle["public_link_document"]["public_properties"] = []
    props = normalize_link_properties(bench_bundle["public_link_document"], event_identity(bench_bundle))
    assert all(value.value is None for value in props.properties.values())


def test_duplicate_receipt_quantities_and_wrong_node_alias_rejected(bench_bundle):
    receipt = bench_bundle["provider_receipt"]
    receipt["payload"].append(copy.deepcopy(receipt["payload"][0]))
    with pytest.raises(ContractError, match="duplicate"):
        normalize_provider_receipt(receipt, context(bench_bundle))
    with pytest.raises(ContractError, match="alias"):
        normalize_network_geometry(
            bench_bundle["network_geometry"],
            context(bench_bundle),
            {"node.air": ("wrong.entity", "endpoint.air"), "node.ground": ("vehicle.main", "endpoint.ground")},
        )
