"""Recorded cooperative response branches use real flight receipts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.packs.lowalt.cooperation import (
    EVENTS,
    FRAGMENT_PATH,
    merge_contract_snapshot,
    validate_config,
)
from aeroagentsim.packs.lowalt.cooperation import (
    expand as expand_cooperation,
)
from aeroagentsim.packs.lowalt.everyday import _base, _group, _site
from aeroagentsim.packs.lowalt.food import food_document
from aeroagentsim.packs.lowalt.template import SECOND, expand, parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.narratives import NarrativeProjector, validate_declarations
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _snapshot(document: dict[str, Any], directory: Path) -> None:
    data = json.loads((BASE / "registry.snapshot.json").read_text())
    path = directory / "cooperation.registry.json"
    path.write_text(json.dumps(merge_contract_snapshot(data)))
    document["registry"]["snapshot"] = str(path)


def _config(helpers: list[str]) -> dict[str, Any]:
    return {
        "handoff_points": [{"id": "locker", "position": [260.0, 0.0, 0.0], "label": "Parcel locker", "label_zh": "包裹交接柜"}],
        "incidents": [{"id": "incident-1", "kind": "person_fall", "position": [200.0, 0.0, 0.0], "at_s": 20.0, "label": "Person fell", "label_zh": "行人跌倒"}],
        "watchers": {"vehicles": ["drone-1"], "notice_radius_m": 300.0},
        "responder": {"mode": "prescribed", "order": ["request_handoff", "respond_now", "finish_then_respond", "report_only"], "rules": {"protected_task_kinds": ["medical"], "require_replacement": True, "min_battery_pct": 30.0, "max_response_distance_m": 1000.0}},
        "helpers": {"vehicles": helpers, "ask_radius_m": 1000.0, "mode": "prescribed", "rules": {"accept_when": "idle", "min_battery_pct": 25.0}, "selection": "nearest"},
        "photograph": {"dwell_s": 2.0, "altitude_m": 35.0},
    }


def _document(directory: Path, *, helpers: int = 1, busy: bool = False, kind: str = "parcel") -> dict[str, Any]:
    document = _base("cooperation-test", "parcel", fleet_size=1)
    fleet = document["generators"]["fleet"]
    fleet["starts"] = [[0.0, 0.0, 0.0]]
    for entity in document["entities"]:
        if entity["id"] in {"home", "home-pad"}:
            entity["facts"]["lowalt.site_position"] = [0.0, 0.0, 0.0]
    _site(document, "destination", [800.0, 0.0, 0.0], "Customer")
    _group(document, kind, "order", [0], ["home"], ["destination"])
    config = document["engines"]["tasks"]["config"]
    config["vehicle_task_kinds"] = {"drone-1": [kind]}
    identities = []
    for index in range(1, helpers + 1):
        identity = f"courier-{index}"
        identities.append(identity)
        position = [260.0, 20.0, 0.0] if index == 1 else [100.0, -80.0, 0.0]
        facts = copy.deepcopy(fleet["facts"])
        facts["lowalt.position"] = position
        document["entities"].append({"id": identity, "type": "lowalt:UAV", "label": f"Helper {index}", "label_zh": f"协助者 {index}", "facts": facts})
        # Listed helpers may accept a parcel despite their ordinary allocation
        # role; this also keeps the original order with its intended requester.
        config["vehicle_task_kinds"][identity] = ["inspection"]
    if busy:
        _site(document, "helper-home", [260.0, 20.0, 0.0])
        _site(document, "helper-destination", [1600.0, 20.0, 0.0])
        _group(document, "inspection", "helper-order", [0], ["helper-home"], ["helper-destination"])
    config["cooperation"] = _config(identities)
    document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]["value"] = [0.0, 0.0, 0.0]
    # Handoff pads are shared landed space; a tiny authored airborne separation
    # radius keeps unrelated avoidance decisions out of these branch fixtures.
    for package in document["behaviours"]:
        if package["id"] == "lowalt.rules":
            package["predicates"]["closer_than_safety_distance"]["parameters"]["horizontal_m"] = 0.001
    _snapshot(document, directory)
    return document


def _run(document: dict[str, Any], seconds: int = 160) -> list[dict[str, Any]]:
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(seconds * SECOND)
        return [project(record) for record in sim.kernel.iter_records()]
    finally:
        sim.close()


def _events(records: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [message for record in records for message in record["messages"] if message["schemaId"] == schema]


def _fact_values(records: list[dict[str, Any]], entity: str, field: str) -> list[Any]:
    return [fact["value"] for record in records for fact in record["facts"] if fact["entity"]["id"] == entity and fact["fieldId"] == field]


def test_picked_up_parcel_handoff_and_photograph_are_receipt_driven(tmp_path: Path) -> None:
    records = _run(_document(tmp_path))
    occurred = _events(records, "lowalt.incident.occurred")
    assert len(occurred) == 1 and int(occurred[0]["at"]["ns"]) == 20 * SECOND
    accepted = _events(records, "lowalt.handoff.accepted")[0]
    assert accepted["payload"]["helper"] == "courier-1"
    assert accepted["payload"]["point"] == "locker"
    dropped = _events(records, "lowalt.handoff.dropped")[0]
    transferred = _events(records, "lowalt.handoff.transferred")[0]
    assert int(transferred["at"]["ns"]) >= int(dropped["at"]["ns"])
    assert "awaiting_transfer" in _fact_values(records, "order-1", "lowalt.task_state")
    delivered = _events(records, "lowalt.task.completed")
    assert [(event["payload"]["task"], event["payload"]["vehicle"]) for event in delivered] == [("order-1", "courier-1")]
    responding = _events(records, "lowalt.incident.responding")[0]
    photographed = _events(records, "lowalt.incident.photographed")[0]
    assert int(photographed["at"]["ns"]) >= int(responding["at"]["ns"]) + 2 * SECOND
    assert photographed["payload"]["vehicle"] == "drone-1"
    assert photographed["payload"]["position"] == [200.0, 0.0, 0.0]
    for vehicle in ("drone-1", "courier-1"):
        assert _fact_values(records, vehicle, "lowalt.payload_kg")[-1] == 0.0
    assert _fact_values(records, "handoff-incident-1", "lowalt.handoff_state") == ["idle", "requested", "accepted", "dropped", "transferred"]
    assert _fact_values(records, "incident-1", "lowalt.incident_state") == ["pending", "occurred", "noticed", "responding", "photographed"]
    receipts = [receipt for record in records for receipt in record["receipts"]]
    assert any(receipt["status"] == "succeeded" for receipt in receipts)
    assert "provenance" in transferred["payload"]


def test_busy_helper_declines_and_requester_finishes_then_responds(tmp_path: Path) -> None:
    records = _run(_document(tmp_path, busy=True))
    offer = _events(records, "lowalt.handoff.offer")[0]["payload"]
    assert offer["accepted"] is False and "busy" in offer["reason"]
    assert _events(records, "lowalt.handoff.declined")
    choices = [event["payload"]["choice"] for event in _events(records, "lowalt.incident.decision")]
    assert choices == ["request_handoff", "finish_then_respond"]
    completed = next(event for event in _events(records, "lowalt.task.completed") if event["payload"]["task"] == "order-1")
    photographed = _events(records, "lowalt.incident.photographed")[0]
    assert completed["payload"]["vehicle"] == "drone-1"
    assert int(photographed["at"]["ns"]) > int(completed["at"]["ns"])
    assert not _events(records, "lowalt.handoff.dropped")


def test_busy_helper_accepts_and_takes_parcel_after_current_task(tmp_path: Path) -> None:
    document = _document(tmp_path, busy=True)
    document["engines"]["tasks"]["config"]["cooperation"]["helpers"]["rules"]["accept_when"] = "always"
    records = _run(document, 240)
    original_completed = next(event for event in _events(records, "lowalt.task.completed") if event["payload"]["task"] == "helper-order-1")
    transferred = _events(records, "lowalt.handoff.transferred")[0]
    assert int(transferred["at"]["ns"]) > int(original_completed["at"]["ns"])
    delivery = next(event for event in _events(records, "lowalt.task.completed") if event["payload"]["task"] == "order-1")
    assert delivery["payload"]["vehicle"] == "courier-1"
    assert _events(records, "lowalt.incident.photographed")


@pytest.mark.parametrize("selection", ["nearest", "first"])
def test_two_offers_select_one_helper_and_release_the_other(tmp_path: Path, selection: str) -> None:
    document = _document(tmp_path, helpers=2)
    helpers = document["engines"]["tasks"]["config"]["cooperation"]["helpers"]
    helpers["selection"] = selection
    helpers["vehicles"] = ["courier-2", "courier-1"]
    records = _run(document)
    assert len(_events(records, "lowalt.handoff.offer")) == 2
    accepted = _events(records, "lowalt.handoff.accepted")[0]["payload"]
    selected = "courier-1" if selection == "nearest" else "courier-2"
    assert accepted["helper"] == selected
    assert accepted["others"] == ["courier-2" if selected == "courier-1" else "courier-1"]
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == selected
    assert len(_events(records, "lowalt.handoff.transferred")) == 1


def test_low_battery_records_blocked_reason_and_reports(tmp_path: Path) -> None:
    document = _document(tmp_path)
    fleet = document["generators"]["fleet"]["facts"]
    fleet.update({"lowalt.soc": 0.35, "lowalt.motion_energy_j": 84000.0})
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["responder"]["rules"]["min_battery_pct"] = 80.0
    records = _run(document, 60)
    decision = _events(records, "lowalt.incident.decision")[0]["payload"]
    assert decision["choice"] == "report_only"
    assert decision["allowed"] == ["report_only"]
    assert all("Battery" in reason for reason in decision["blocked"].values())
    assert _events(records, "lowalt.incident.reported")
    assert not _events(records, "lowalt.incident.photographed")


def test_protected_medical_task_cannot_be_interrupted_or_handed_off(tmp_path: Path) -> None:
    records = _run(_document(tmp_path, kind="medical"))
    decision = _events(records, "lowalt.incident.decision")[0]["payload"]
    assert "Protected task" in decision["blocked"]["request_handoff"]
    assert "respond_now" not in decision["allowed"]
    assert decision["choice"] == "finish_then_respond"
    assert not _events(records, "lowalt.handoff.requested")
    assert not _events(records, "lowalt.task.preempted")
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == "drone-1"


def test_far_incident_occurs_but_is_never_noticed(tmp_path: Path) -> None:
    document = _document(tmp_path)
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["incidents"][0]["position"] = [5000.0, 5000.0, 0.0]
    coop["watchers"]["notice_radius_m"] = 25.0
    records = _run(document, 90)
    assert len(_events(records, "lowalt.incident.occurred")) == 1
    assert not _events(records, "lowalt.incident.noticed")
    assert not _events(records, "lowalt.incident.decision")
    assert _fact_values(records, "incident-1", "lowalt.incident_state")[-1] == "occurred"


def test_task_before_pickup_is_reassigned_without_a_physical_drop(tmp_path: Path) -> None:
    document = _document(tmp_path)
    config = document["engines"]["tasks"]["config"]
    config["cooperation"]["incidents"][0]["at_s"] = 2.0
    next(entity for entity in document["entities"] if entity["id"] == "home")["facts"]["lowalt.site_position"] = [600.0, 0.0, 0.0]
    records = _run(document)
    assert _events(records, "lowalt.handoff.accepted")[0]["payload"]["point"] is None
    assert _events(records, "lowalt.handoff.transferred")[0]["payload"]["point"] is None
    assert not _events(records, "lowalt.handoff.dropped")
    assert _events(records, "lowalt.task.picked_up")[0]["payload"]["vehicle"] == "courier-1"
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == "courier-1"


def test_respond_now_suspends_and_resumes_original_parcel(tmp_path: Path) -> None:
    document = _document(tmp_path)
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["responder"]["order"] = ["respond_now", "report_only"]
    coop["responder"]["rules"]["require_replacement"] = False
    records = _run(document)
    assert _events(records, "lowalt.task.preempted")
    assert _events(records, "lowalt.task.resumed")
    assert _events(records, "lowalt.incident.photographed")
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == "drone-1"
    assert not _events(records, "lowalt.handoff.requested")


def test_legacy_generated_incident_loads_and_upgrades_in_place(tmp_path: Path) -> None:
    # A saved scene keeps its expanded generated rows; strip the facts a
    # pre-radius/end version would lack, then reopen it through the real loader.
    expanded = load_scenario(_document(tmp_path), base=BASE).document
    identity = None
    for entity in expanded["entities"]:
        if entity["type"] == "lowalt:Incident":
            identity = entity["id"]
            for fact in ("lowalt.incident_radius_m", "lowalt.incident_end_ns", "lowalt.incident_active"):
                entity["facts"].pop(fact, None)
    assert identity is not None
    records = _run(expanded, 30)
    assert _fact_values(records, identity, "lowalt.incident_active") == [False, True]
    assert _fact_values(records, identity, "lowalt.incident_radius_m") == [6.0]
    assert _events(records, "lowalt.incident.occurred")


def test_real_collision_with_authored_entity_still_raises(tmp_path: Path) -> None:
    # An authored entity (not a stale generated row) that collides with a
    # generated id is still a hard error.
    document = _document(tmp_path)
    document["entities"].append({
        "id": document["engines"]["tasks"]["config"]["cooperation"]["incidents"][0]["id"],
        "type": "lowalt:Incident", "label": "Authored over the generator", "label_zh": "作者覆盖",
        "business": "emergency", "facts": {"lowalt.incident_kind": "person_fall"},
    })
    with pytest.raises(ValueError, match="entity id collision"):
        _run(document, 1)


def test_zero_dwell_commits_responding_before_photographed(tmp_path: Path) -> None:
    document = _document(tmp_path)
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["responder"]["order"] = ["respond_now", "report_only"]
    coop["responder"]["rules"]["require_replacement"] = False
    coop["photograph"]["dwell_s"] = 0.0
    records = _run(document)
    responding = _events(records, "lowalt.incident.responding")[0]
    photographed = _events(records, "lowalt.incident.photographed")[0]
    assert responding["at"]["ns"] == photographed["at"]["ns"]
    assert responding["at"]["microstep"] < photographed["at"]["microstep"]
    assert _fact_values(records, "incident-1", "lowalt.incident_state") == ["pending", "occurred", "noticed", "responding", "photographed"]


def _reply(choice: str) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": json.dumps({"choice": choice, "reason": "Help while keeping delivery / 协助并保持交付"})}, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


@pytest.mark.parametrize("response,offer", [("request_handoff", "accept"), ("teleport", "accept"), ("request_handoff", "teleport"), ("request_handoff", "decline")])
def test_agent_decisions_are_journaled_without_prescribed_substitution(tmp_path: Path, response: str, offer: str) -> None:
    pytest.importorskip("langgraph")
    document = _document(tmp_path)
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["responder"].update(mode="agent", goal="Help without abandoning delivery / 帮助行人且保留交付")
    coop["helpers"].update(mode="agent", goal="Keep deliveries on time / 保持准时交付")
    coop["provider"] = {"mode": "stub", "model": "cooperation-test/v1", "responses": {"responder": [_reply(response), _reply("report_only")], "helper": [_reply(offer)]}}
    records = _run(document)
    handed_off = response == "request_handoff" and offer == "accept"
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == ("courier-1" if handed_off else "drone-1")
    assert bool(_events(records, "lowalt.incident.photographed")) is handed_off
    decisions = [event["payload"] for event in _events(records, "lowalt.incident.decision")]
    assert [event["choice"] for event in decisions] == ([] if response == "teleport" else ["request_handoff"] if handed_off else ["request_handoff", "report_only"])
    assert all(event["mode"] == "agent" for event in decisions)
    failures = _events(records, "lowalt.incident.decision_failed")
    assert [event["payload"]["role"] for event in failures] == (["responder"] if response == "teleport" else ["helper"] if offer == "teleport" else [])
    if response == "teleport":
        assert _fact_values(records, "incident-1", "lowalt.incident_state")[-1] == "decision_failed"
    elif not handed_off:
        assert _fact_values(records, "handoff-incident-1", "lowalt.handoff_state")[-1] == ("failed" if offer == "teleport" else "declined")
        assert _events(records, "lowalt.incident.reported")
    calls = [json.loads(event["payload"]["data_json"]) for event in _events(records, "aas.langgraph.record") if event["payload"]["phase"] == "model_call"]
    assert len(calls) == (1 if response == "teleport" else 2 if handed_off else 3)
    for call in calls:
        assert call["request"]["messages"][0]["content"].startswith("AEROAGENTSIM-COOPERATION-DECISION\n")


@pytest.mark.parametrize("role", ["responder", "helper"])
def test_agent_provider_failure_is_recorded_without_a_business_decision(tmp_path: Path, role: str) -> None:
    pytest.importorskip("langgraph")
    document = _document(tmp_path)
    coop = document["engines"]["tasks"]["config"]["cooperation"]
    coop["responder"].update(mode="agent", goal="Help / 协助")
    coop["helpers"].update(mode="agent", goal="Help / 协助")
    responses = {"responder": [_reply("request_handoff"), _reply("report_only")], "helper": [_reply("accept")]}
    responses[role] = [{"error": {"code": "TRANSPORT", "message": "injected outage"}}]
    coop["provider"] = {"mode": "stub", "model": "cooperation-test/v1", "responses": responses}
    records = _run(document)
    failures = _events(records, "lowalt.incident.decision_failed")
    assert len(failures) == 1 and failures[0]["payload"]["role"] == role
    assert "injected outage" in failures[0]["payload"]["reason"]
    calls = [json.loads(event["payload"]["data_json"]) for event in _events(records, "aas.langgraph.record") if event["payload"]["phase"] == "model_call"]
    assert any(call.get("error", {}).get("code") == "TRANSPORT" for call in calls)
    assert not _events(records, "lowalt.incident.photographed")
    assert not _events(records, "lowalt.handoff.accepted")
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == "drone-1"


@pytest.mark.parametrize("field,value,path", [
    ("helpers.vehicles", ["courier-9"], r"cooperation.helpers.vehicles\[0\]: unknown UAV courier-9"),
    ("watchers.notice_radius_m", float("nan"), r"cooperation.watchers.notice_radius_m"),
    ("responder.mode", "automatic", r"cooperation.responder.mode"),
    ("responder.order", ["teleport"], r"cooperation.responder.order"),
    ("responder.rules.require_replacement", 1, r"cooperation.responder.rules.require_replacement"),
    ("helpers.rules.accept_when", "sometimes", r"cooperation.helpers.rules.accept_when"),
    ("helpers.selection", "random", r"cooperation.helpers.selection"),
    ("photograph.altitude_m", float("inf"), r"cooperation.photograph.altitude_m"),
])
def test_invalid_configuration_names_the_path(field: str, value: Any, path: str) -> None:
    config = _config(["courier-1"])
    target = config
    members = field.split(".")
    for member in members[:-1]:
        target = target[member]
    target[members[-1]] = value
    with pytest.raises(ValueError, match=path):
        validate_config(config, vehicles={"drone-1", "courier-1"})


def test_unknown_keys_positions_and_provider_are_strict() -> None:
    config = _config(["courier-1"])
    config["helpers"]["silent_fallback"] = True
    with pytest.raises(ValueError, match=r"cooperation.helpers: unknown keys"):
        validate_config(config, vehicles={"drone-1", "courier-1"})
    config = _config(["courier-1"])
    config["incidents"][0]["position"] = [1.0, 2.0]
    with pytest.raises(ValueError, match=r"cooperation.incidents\[0\].position"):
        validate_config(config, vehicles={"drone-1", "courier-1"})
    config = _config(["courier-1"])
    config["responder"].update(mode="agent", goal="Help / 协助")
    with pytest.raises(ValueError, match=r"cooperation.provider"):
        validate_config(config, vehicles={"drone-1", "courier-1"})
    config["provider"] = {"profile": "assistant", "base_url": "http://untrusted"}
    with pytest.raises(ValueError, match=r"cooperation.provider"):
        validate_config(config, vehicles={"drone-1", "courier-1"})


def test_snapshot_union_retains_provenance_is_idempotent_and_rejects_conflicts() -> None:
    original = json.loads((BASE / "registry.snapshot.json").read_text())
    preserved = copy.deepcopy(original)
    merged = merge_contract_snapshot(original)
    assert original == preserved
    assert merge_contract_snapshot(merged) == merged
    fragment = json.loads(FRAGMENT_PATH.read_text())
    for descriptor in fragment["registry"]["types"]:
        assert descriptor in merged["registry"]["types"]
    assert merged["details"]["provenance"]["descriptors"]["types"]["oo:Incident"] == fragment["details"]["provenance"]["descriptors"]["types"]["oo:Incident"]
    next(row for row in merged["registry"]["types"] if row["id"] == "oo:Incident")["abstract"] = True
    with pytest.raises(ValueError, match="oo:Incident"):
        merge_contract_snapshot(merged)


def test_expansion_is_idempotent_and_requires_frozen_contracts(tmp_path: Path) -> None:
    document = _document(tmp_path)
    expand(document)
    expanded = copy.deepcopy(document)
    expand(document)
    assert document == expanded
    assert all(schema in document["event_narratives"] for schema in EVENTS)
    validate_declarations(document["event_narratives"])
    assert next(entity for entity in document["entities"] if entity["id"] == "handoff-incident-1")["type"] == "lowalt:Handoff"
    assert not any(row["typeId"] == "lowalt:Handoff" for row in document["presentation"])
    document = _document(tmp_path)
    document["registry"]["snapshot"] = str(BASE / "registry.snapshot.json")
    with pytest.raises(ValueError, match="cooperation.snapshot.json"):
        expand(document)


def test_food_document_expands_cooperation_with_generated_watchers(tmp_path: Path) -> None:
    document = food_document()
    coop = _config(["drone-2"])
    document["engines"]["tasks"]["config"]["cooperation"] = coop
    _snapshot(document, tmp_path)
    scenario = load_scenario(document, base=BASE)
    assert scenario.registry.is_a("lowalt:Incident", "oo:Incident")
    assert scenario.registry.is_a("lowalt:Handoff", "oo:CargoTransferReport")
    assert any(entity.id == "incident-1" for entity in scenario.manifest.entities)


def test_without_cooperation_expansion_and_journal_are_unchanged() -> None:
    document = parcel_document(fleet_size=1)
    original = copy.deepcopy(document)
    expand_cooperation(document)
    assert document == original
    assert _run(document, 25) == _run(original, 25)
    assert not any(row["id"].startswith(("lowalt.incident.", "lowalt.handoff.", "lowalt.coop.")) for row in document["registry"]["messages"])


@pytest.mark.parametrize("allocator", ["nearest", "auction", "hungarian", "llm"])
def test_per_vehicle_task_kinds_are_honoured_by_every_allocator(tmp_path: Path, allocator: str) -> None:
    if allocator == "llm":
        pytest.importorskip("langgraph")
    document = _document(tmp_path)
    config = document["engines"]["tasks"]["config"]
    del config["cooperation"]
    config["vehicle_kinds"] = {"parcel": ["parcel", "inspection"]}
    config["vehicle_task_kinds"] = {"drone-1": ["inspection"], "courier-1": ["parcel"]}
    document["algorithms"]["allocation"] = allocator
    records = _run(document)
    assert _events(records, "lowalt.task.assigned")[0]["payload"]["vehicle"] == "courier-1"
    assert _events(records, "lowalt.task.completed")[0]["payload"]["vehicle"] == "courier-1"
    if allocator == "llm":
        request = _events(records, "lowalt.allocation.request")[0]["payload"]
        kinds = {vehicle["id"]: vehicle["task_kinds"] for vehicle in json.loads(request["vehicles_json"])}
        assert kinds == {"drone-1": ["inspection"], "courier-1": ["parcel"]}


def test_vehicle_task_kinds_rejects_unknown_generated_id(tmp_path: Path) -> None:
    document = _document(tmp_path)
    document["engines"]["tasks"]["config"]["vehicle_task_kinds"]["drone-9"] = ["parcel"]
    with pytest.raises(ValueError, match=r"vehicle_task_kinds.drone-9"):
        expand(document)


def test_cooperation_kpis_and_narratives_only_use_recorded_events(tmp_path: Path) -> None:
    document = _document(tmp_path)
    scenario = load_scenario(document, base=BASE)
    sim = Simulation(scenario)
    try:
        sim.start()
        sim.run_until(160 * SECOND)
        projector = KPIProjector(scenario.registry, document["presentation"])
        narratives = NarrativeProjector(EVENTS, {"lowalt.position", "lowalt.site_position", "lowalt.incident_position"})
        for record in sim.kernel.iter_records():
            projector.consume(record)
            narratives.consume(record)
        values = projector.snapshot()
        assert values["incidents_occurred"]["value"] == 1
        assert values["incidents_noticed"]["value"] == 1
        assert values["incidents_photographed"]["value"] == 1
        assert values["handoffs_completed"]["value"] == 1
        assert values["incident_response_s"]["value"] > 0
        restored = KPIProjector.from_cursor(scenario.registry, projector.cursor(), document["presentation"])
        assert restored.snapshot() == values
        before = projector.snapshot()
        for record in sim.kernel.iter_records():
            projector.consume(record)
        after = projector.snapshot()
        for metric in ("incidents_occurred", "incidents_noticed", "incidents_photographed", "handoffs_completed", "incident_response_s"):
            assert after[metric] == before[metric]
    finally:
        sim.close()


def test_incident_capture_expansion_reuses_authored_camera_manifest(tmp_path: Path) -> None:
    document = _document(tmp_path)
    document["engines"]["capture"] = {"plugin": "lowalt_city_capture", "config": {}}
    document["engines"]["capture_bridge"] = {"plugin": "traffic_capture_bridge", "config": {
        "capture_target": "capture", "request_schema": "traffic.capture.render", "upload_schema": "traffic.capture.upload", "upload_lag_ns": 200000000,
        "camera": {"revision": "uav-gimbal-oblique/v1", "frame": "enu", "target_field": "traffic.incident.position_enu_m", "pitch_deg": 50.0, "fov": 96.0, "coincident_azimuth_deg": -90.0},
        "asset_digest": "traffic-camera-assets/v1", "width": 960, "height": 540, "timeout_s": 20.0,
        "pose_fields": {"lowalt:UAV": "lowalt.position"},
    }}
    expand(document)
    bridge = document["engines"]["lowalt_incident_capture"]
    assert bridge["plugin"] == "lowalt_incident_capture"
    assert bridge["config"]["camera"]["target_field"] == "lowalt.incident_position"
    assert bridge["config"]["capture_target"] == "capture"
    assert bridge["config"]["asset_digest"] == "traffic-camera-assets/v1"
