from dataclasses import replace
import math

import pytest

from aeroagentsim.integration import (
    ContractError,
    Selection,
    SemanticBinding,
    SemanticSession,
    SharedViewStore,
    ViewKey,
    contract_payload,
)
from aeroagentsim.integration.contracts import thaw_json


def actor_binding(binding_id="actor-1", actor_id="aircraft.alpha", parameters=None, targets=None):
    return SemanticBinding(
        binding_id,
        (actor_id,),
        targets or ("actor_moving", "vertical_ascent", "vertical_descent", "actor_stationary"),
        "synthetic-request-registry",
        {"hu.actor.speed_mps": "actor.speed_mps", "hu.actor.vertical_speed_mps": "actor.vertical_speed_mps"},
        parameters or {},
    )


class RecordingEvaluator:
    """A boundary spy, not an Atlas evaluator or predicate implementation."""

    def __init__(self, output=None):
        self.requests = []
        self.output = output

    def evaluate_batch(self, request):
        self.requests.append(request)
        return self.output if self.output is not None else {target: None for target in request.binding.target_ids}


def test_absent_native_runtime_is_explicit_and_projection_uses_horizontal_speed(replay):
    evidence = SemanticSession().evaluate(replay.frames[0], (actor_binding(),), "eval-1", "binding-1")[0]
    assert not evidence.evaluator_available
    assert all(value is None for value in evidence.truths.values())
    assert all(reason == ("evaluator_unavailable",) for reason in evidence.unknown_reasons.values())
    assert evidence.request.current.values["hu.actor.speed_mps"] == 2.0
    assert evidence.request.current.values["hu.actor.vertical_speed_mps"] == 1.0
    assert evidence.request.occurrence_policy == "no_occurrences_emitted"


def test_true_false_unknown_tokens_pass_through_without_boolean_substitute(replay):
    targets = ("synthetic.true-token", "synthetic.false-token", "synthetic.unknown-token")
    spy = RecordingEvaluator(dict(zip(targets, (True, False, None))))
    evidence = SemanticSession(spy).evaluate(replay.frames[0], (actor_binding(targets=targets),), "eval-1", "binding-1")[0]
    assert tuple(evidence.truths.values()) == (True, False, None)
    assert evidence.unknown_reasons == {targets[2]: ("native_evaluation_unknown",)}
    assert len(spy.requests) == 1


@pytest.mark.parametrize("result", [{"actor_moving": 1}, {}, {"wrong-target": None}, {"actor_moving": "unknown"}])
def test_native_truth_contract_rejects_alias_values(replay, result):
    with pytest.raises(ContractError, match="True/False/None"):
        SemanticSession(RecordingEvaluator(result)).evaluate(
            replay.frames[0], (actor_binding(targets=("actor_moving",)),), "eval-1", "binding-1"
        )


def test_same_target_instances_have_separate_parameters_contexts_and_histories(replay):
    spy = RecordingEvaluator()
    session = SemanticSession(spy)
    a = actor_binding(
        "instance-a", "aircraft.alpha", {"local": {"threshold": 0.1, "reference": "lexical.threshold"}}, ("actor_stationary",)
    )
    b = actor_binding(
        "instance-b", "vehicle.main", {"local": {"threshold": 0.2, "reference": "lexical.threshold"}}, ("actor_stationary",)
    )
    session.evaluate(replay.frames[0], (a, b), "eval-1", "binding-1")
    session.evaluate(replay.frames[1], (a,), "eval-1", "binding-1")
    assert len(spy.requests) == 3  # One native boundary call per compatible envelope.
    assert len(spy.requests[0].history) == len(spy.requests[1].history) == 1
    assert len(spy.requests[2].history) == 2
    assert spy.requests[0].current.values["hu.actor.speed_mps"] == 2.0
    assert spy.requests[1].current.values["hu.actor.speed_mps"] == 1.0
    assert spy.requests[0].binding.parameters["local"]["reference"] == "lexical.threshold"
    left = thaw_json(spy.requests[0].current.values)
    right = thaw_json(spy.requests[1].current.values)
    left["cache"] = "private to this invocation"
    assert "cache" not in right and "cache" not in spy.requests[0].current.values
    with pytest.raises(TypeError):
        spy.requests[0].binding.parameters["local"]["threshold"] = 99


def test_sparse_stationary_history_keeps_valid_samples_without_guessing_truth(replay):
    session = SemanticSession()
    binding = actor_binding(targets=("actor_stationary",))
    session.evaluate(replay.frames[0], (binding,), "eval-1", "binding-1")
    session.evaluate(replay.frames[1], (binding,), "eval-1", "binding-1")
    evidence = session.evaluate(replay.frames[2], (binding,), "eval-1", "binding-1")[0]
    assert [sample.relative_seconds for sample in evidence.request.history] == [0, 1, 4]
    assert [sample.values["hu.actor.speed_mps"] for sample in evidence.request.history] == [2, 0, 0]
    assert not evidence.request.history_censored
    assert evidence.request.current.provenance["actors"]["aircraft.alpha"]["sample_time_ns"] == replay.frames[1].sim_time_ns
    assert evidence.truths["actor_stationary"] is None


def test_gaps_have_explicit_null_boundaries_and_no_event_occurrences(replay):
    session = SemanticSession()
    binding = actor_binding(targets=("synthetic.event-latch",))
    for frame in replay.frames[:4]:
        evidence = session.evaluate(frame, (binding,), "eval-1", "binding-1")[0]
    boundaries = [sample for sample in evidence.request.history if sample.frame_key is None]
    assert len(boundaries) == 1
    assert boundaries[0].relative_seconds == 5
    assert all(value is None for value in boundaries[0].values.values())
    assert "observation_expired" in boundaries[0].unknown_reasons
    assert "replay_gap:authored_unavailable_interval" in boundaries[0].unknown_reasons
    assert evidence.request.history_censored
    assert evidence.request.current.values["hu.actor.speed_mps"] is None
    assert evidence.request.occurrence_policy == "no_occurrences_emitted"


def test_full_epoch_history_retained_for_latches_and_transitive_windows(replay):
    session = SemanticSession()
    binding = actor_binding()
    for frame in replay.frames:
        evidence = session.evaluate(frame, (binding,), "eval-1", "binding-1")[0]
    assert evidence.request.history[0].relative_seconds == 0
    assert evidence.request.history[-1].relative_seconds == 20
    assert evidence.request.history_censored
    assert len(evidence.request.history) >= len(replay.frames)
    assert any(sample.relative_seconds == 5 for sample in evidence.request.history)


def test_parameter_registry_and_binding_revisions_are_pinned(replay):
    session = SemanticSession()
    binding = actor_binding(parameters={"threshold": 0.1})
    first = session.evaluate(replay.frames[0], (binding,), "eval-1", "binding-1")[0]
    changed = replace(binding, parameters={"threshold": 0.2})
    with pytest.raises(ContractError, match="evaluation revision"):
        session.evaluate(replay.frames[0], (changed,), "eval-1", "binding-1")
    revised = session.evaluate(replay.frames[0], (changed,), "eval-2", "binding-1")[0]
    assert revised.request.view_key != first.request.view_key
    assert revised.request.history == first.request.history
    remapped = actor_binding(actor_id="vehicle.main", parameters={"threshold": 0.2})
    with pytest.raises(ContractError, match="binding epoch"):
        session.evaluate(replay.frames[1], (remapped,), "eval-2", "binding-1")
    new_epoch = session.evaluate(replay.frames[1], (remapped,), "eval-2", "binding-2")[0]
    assert len(new_epoch.request.history) == 1
    assert new_epoch.request.history_censored  # History begins after the engine origin.
    with pytest.raises(ContractError, match="evaluation revision"):
        session.evaluate(replay.frames[1], (replace(remapped, registry_revision="another-registry"),), "eval-2", "binding-2")


@pytest.mark.parametrize("field", ["attachment_id", "run_id", "run_epoch", "manifest_revision"])
def test_history_isolation_across_namespaces(replay, field):
    session = SemanticSession()
    binding = actor_binding()
    session.evaluate(replay.frames[0], (binding,), "eval-1", "binding-1")
    key = replace(replay.frames[1].key, run=replace(replay.run, **{field: "different"}))
    evidence = session.evaluate(replace(replay.frames[1], key=key), (binding,), "eval-1", "binding-1")[0]
    assert len(evidence.request.history) == 1


def test_backward_observations_require_new_history_session(replay):
    session = SemanticSession()
    binding = actor_binding()
    session.evaluate(replay.frames[2], (binding,), "eval-1", "binding-1")
    with pytest.raises(ContractError, match="backward"):
        session.evaluate(replay.frames[1], (binding,), "eval-1", "binding-1")


def test_physical_surface_clearance_and_missing_body_are_distinct(replay):
    pair = SemanticBinding(
        "pair-1",
        ("aircraft.alpha", "vehicle.main"),
        ("pair_close",),
        "synthetic-request-registry",
        {"hu.pair.clearance_m": "pair.surface_clearance_m"},
    )
    result = SemanticSession().evaluate(replay.frames[0], (pair,), "eval-1", "binding-1")[0]
    assert result.request.current.values["hu.pair.clearance_m"] == pytest.approx(math.sqrt(13) - 3)
    assert result.truths["pair_close"] is None
    unresolved = replace(pair, actor_ids=("aircraft.alpha", "antenna.fixed"))
    result = SemanticSession().evaluate(replay.frames[0], (unresolved,), "eval-1", "binding-1")[0]
    assert result.request.current.values["hu.pair.clearance_m"] is None
    assert "physical_body_geometry_unavailable" in result.request.current.unknown_reasons


def test_pair_closing_cpa_and_undefined_denominators(replay):
    pair = SemanticBinding(
        "pair-1",
        ("aircraft.alpha", "vehicle.main"),
        ("pair_approaching",),
        "synthetic-request-registry",
        {"hu.pair.closing_speed_mps": "pair.closing_speed_mps", "hu.pair.cpa_time_s": "pair.cpa_time_s"},
    )
    result = SemanticSession().evaluate(replay.frames[0], (pair,), "eval-1", "binding-1")[0]
    assert result.request.current.values["hu.pair.closing_speed_mps"] == pytest.approx(1 / math.sqrt(13))
    assert result.request.current.values["hu.pair.cpa_time_s"] == 0.5
    a, b, *rest = replay.frames[0].entities
    coincident = replace(
        replay.frames[0], entities=(a, replace(b, position_enu_m=a.position_enu_m, velocity_enu_mps=a.velocity_enu_mps), *rest)
    )
    result = SemanticSession().evaluate(coincident, (pair,), "eval-1", "binding-1")[0]
    assert tuple(result.request.current.values.values()) == (None, None)
    past = replace(
        replay.frames[0],
        entities=(
            replace(a, position_enu_m=(0, 0, 0), velocity_enu_mps=(0, 0, 0)),
            replace(b, position_enu_m=(1, 0, 0), velocity_enu_mps=(1, 0, 0)),
            *rest,
        ),
    )
    assert (
        SemanticSession().evaluate(past, (pair,), "eval-1", "binding-1")[0].request.current.values["hu.pair.cpa_time_s"]
        == -1.0
    )


def test_binding_maps_are_explicit_and_do_not_infer_compound_owner_pairs():
    with pytest.raises(ContractError, match="arity"):
        SemanticBinding(
            "bad", ("aircraft.alpha/vehicle.main",), ("pair_close",), "registry", {"clearance": "pair.surface_clearance_m"}
        )


def test_equivalent_field_order_does_not_change_binding_epoch(replay):
    binding = actor_binding()
    session = SemanticSession()
    session.evaluate(replay.frames[0], (binding,), "eval-1", "binding-1")
    reordered = replace(binding, state_fields=dict(reversed(tuple(binding.state_fields.items()))))
    evidence = session.evaluate(replay.frames[1], (reordered,), "eval-1", "binding-1")[0]
    assert len(evidence.request.history) == 2
    with pytest.raises(ContractError, match="unresolved"):
        SemanticBinding("bad", ("aircraft.alpha",), ("custom-target",), "registry", {"battery": "guessed.battery"})


@pytest.mark.parametrize(
    "key_change",
    [
        lambda key: replace(key, evaluation_revision="eval-2"),
        lambda key: replace(key, binding_epoch="binding-2"),
        lambda key: replace(key, frame=replace(key.frame, frame_seq=99)),
        lambda key: replace(key, frame=replace(key.frame, frame_hash="new-hash")),
        lambda key: replace(key, frame=replace(key.frame, stage_evidence_key="new-stage")),
        lambda key: replace(key, frame=replace(key.frame, run=replace(key.frame.run, run_epoch="epoch-2"))),
        lambda key: replace(key, frame=replace(key.frame, run=replace(key.frame.run, manifest_revision="manifest-2"))),
    ],
)
def test_shared_cursor_rejects_stale_selection_and_evidence(replay, key_change):
    binding = actor_binding()
    evidence = SemanticSession().evaluate(replay.frames[0], (binding,), "eval-1", "binding-1")
    old = evidence[0].request.view_key
    selection = Selection(old, "predicate", binding.actor_ids, binding.binding_id, target_id="actor_moving")
    store = SharedViewStore()
    store.activate(old, "sealed_replay")
    assert store.select(selection) and store.apply_evidence(old, evidence)
    store.activate(key_change(old), "sealed_replay")
    assert store.snapshot.selection is None and store.snapshot.evidence == ()
    assert not store.select(selection) and not store.apply_evidence(old, evidence)


def test_graph_selection_is_only_state_and_cannot_load_fixture_or_issue_controls(replay):
    key = ViewKey(replay.frames[0].key, "eval-1", "binding-1")
    store = SharedViewStore()
    store.activate(key, "live_observation")
    selection = Selection(key, "graph_node", ("aircraft.alpha",), graph_node_id="node.aircraft.alpha")
    assert store.select(selection)
    assert store.snapshot.selection == selection
    assert store.snapshot.view_key.frame == replay.frames[0].key
    assert not hasattr(store, "choose") and not hasattr(store, "control")
    with pytest.raises(ContractError):
        store.activate(key, "simulation")


def test_framework_neutral_view_export_keeps_full_keys_time_and_null_truth(replay):
    evidence = SemanticSession().evaluate(replay.frames[0], (actor_binding(),), "eval-1", "binding-1")
    store = SharedViewStore()
    key = evidence[0].request.view_key
    store.activate(key, "sealed_replay")
    store.apply_evidence(key, evidence)
    store.select(Selection(key, "state", ("aircraft.alpha",), "actor-1", state_id="hu.actor.speed_mps"))
    payload = contract_payload(store.snapshot)
    assert payload["schema"] == "aeroagentsim.shared-view/v1"
    assert payload["view_key"]["frame"]["run"]["run_epoch"] == "epoch-1"
    assert payload["view_key"]["frame"]["frame_hash"] == replay.frames[0].key.frame_hash
    assert payload["selection"]["view_key"] == payload["view_key"]
    assert payload["evidence"][0]["request"]["current"]["sim_time_ns"] == replay.engine_origin_ns
    assert payload["evidence"][0]["truths"]["actor_moving"] is None
    payload["selection"]["entity_ids"].append("modified only in consumer copy")
    assert store.snapshot.selection.entity_ids == ("aircraft.alpha",)
