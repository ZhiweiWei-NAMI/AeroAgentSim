"""Incident active/end journaling and visual weather fields through a real run."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from aeroagentsim.packs.lowalt.overlay import overlay
from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import header, project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _document(directory: Path, *, weather: bool) -> dict[str, Any]:
    from aeroagentsim.packs.lowalt.cooperation import merge_contract_snapshot

    document = copy.deepcopy(_base_document())
    snapshot = directory / "cooperation.registry.json"
    snapshot.write_text(json.dumps(merge_contract_snapshot(json.loads((BASE / "registry.snapshot.json").read_text()))))
    document["registry"]["snapshot"] = str(snapshot)
    config = document["engines"]["tasks"]["config"]
    config["cooperation"]["incidents"][0]["at_s"] = 2.0
    config["cooperation"]["incidents"][0]["end_s"] = 5.0
    if weather:
        document["registry"]["fields"].extend(row for row in overlay()["fields"] if row["id"] in {"lowalt.precipitation", "lowalt.visibility", "lowalt.light"})
        environment = document["engines"]["environment"]["config"]
        environment["produces"] += ["lowalt.precipitation", "lowalt.visibility", "lowalt.light"]
        environment["profiles"]["weather"].update({
            "lowalt.precipitation": {"mode": "constant", "value": 4 / 3600},
            "lowalt.visibility": {"mode": "constant", "value": 500.0},
            "lowalt.light": {"mode": "constant", "value": "dusk"},
        })
        weather_entity = next(e for e in document["entities"] if e["type"] == "lowalt:Environment")
        weather_entity["facts"].update({
            "lowalt.precipitation": 4 / 3600, "lowalt.visibility": 500.0, "lowalt.light": "dusk"})
        rule = next(r for r in document["bindings"]["rules"] if r["writer"] == "environment" and r["type"] == "lowalt:Environment")
        rule["fields"] += ["lowalt.precipitation", "lowalt.visibility", "lowalt.light"]
    return document


def _base_document() -> dict[str, Any]:
    import yaml

    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    config = document["engines"]["tasks"]["config"]
    config["cooperation"] = {
        "handoff_points": [],
        "incidents": [{"id": "incident-1", "kind": "fire", "position": [200.0, 0.0, 0.0], "at_s": 20.0, "radius_m": 40, "end_s": 60.0, "label": "Building fire", "label_zh": "建筑火情"}],
        "watchers": {"vehicles": ["drone-1"], "notice_radius_m": 300.0},
        "responder": {"mode": "prescribed", "order": ["report_only"], "rules": {"protected_task_kinds": ["medical"], "require_replacement": True, "min_battery_pct": 30.0, "max_response_distance_m": 1000.0}},
        "helpers": {"vehicles": [], "ask_radius_m": 1000.0, "mode": "prescribed", "rules": {"accept_when": "idle", "min_battery_pct": 25.0}, "selection": "nearest"},
        "photograph": {"dwell_s": 2.0, "altitude_m": 35.0},
    }
    return document


def _run(document: dict[str, Any], tmp_path: Path, seconds: int) -> list[dict[str, Any]]:
    scenario = load_scenario(document, base=BASE)
    directory = tmp_path / "run"
    session = RunSession(scenario, directory)
    try:
        session.start()
        session.run_until(seconds * 10**9)
        records = [project(record) for record in session.simulation.kernel.iter_records()]
        return records
    finally:
        session.close()


def _events(records: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [message for record in records for message in record["messages"] if message["schemaId"] == schema]


def _fact_values(records: list[dict[str, Any]], entity: str, field: str) -> list[Any]:
    return [fact["value"] for record in records for fact in record["facts"] if fact["entity"]["id"] == entity and fact["fieldId"] == field]


def test_incident_active_and_ended_are_journaled(tmp_path: Path) -> None:
    records = _run(_document(tmp_path, weather=False), tmp_path, 70)
    occurred = _events(records, "lowalt.incident.occurred")
    ended = _events(records, "lowalt.incident.ended")
    assert [int(m["at"]["ns"]) for m in occurred] == [2 * 10**9]
    assert [int(m["at"]["ns"]) for m in ended] == [5 * 10**9]
    assert _fact_values(records, "incident-1", "lowalt.incident_active") == [False, True, False]
    assert _fact_values(records, "incident-1", "lowalt.incident_radius_m") == [40.0]
    assert _fact_values(records, "incident-1", "lowalt.incident_end_ns") == [5 * 10**9]
    # Response phases stay independent of the visual active flag.
    assert _fact_values(records, "incident-1", "lowalt.incident_state") == ["pending", "occurred", "noticed", "reported"]


def test_weather_fields_journal_at_zero_and_replay_identically(tmp_path: Path) -> None:
    document = _document(tmp_path, weather=True)
    directories = []
    for name in ("first", "second"):
        scenario = load_scenario(copy.deepcopy(document), base=BASE)
        directory = tmp_path / name
        with RunSession(scenario, directory) as session:
            session.start()
            session.run_until(10 * 10**9)
        directories.append(directory)
    records = _run(document, tmp_path, 10)
    assert _fact_values(records, "weather", "lowalt.precipitation") == [4 / 3600]
    assert _fact_values(records, "weather", "lowalt.visibility") == [500.0]
    assert _fact_values(records, "weather", "lowalt.light") == ["dusk"]
    first = (directories[0] / "journal.jsonl").read_bytes()
    assert first == (directories[1] / "journal.jsonl").read_bytes()
    run_header = header(directories[0])
    assert run_header["scene"]["lighting"] == "dusk"


def test_header_lists_environment_gusts(tmp_path: Path) -> None:
    import yaml

    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    profile = document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"]
    profile["mode"] = "gust"
    profile["gusts"] = [
        {"start_ns": 10**9, "end_ns": 3 * 10**9, "value": [5.0, 0.0, 0.0]},
        {"start_ns": 8 * 10**9, "end_ns": 9 * 10**9, "value": [2.0, 1.0, 0.0]},
    ]
    directory = tmp_path / "run"
    with RunSession(load_scenario(document, base=BASE), directory) as session:
        session.start()
        session.run_until(10 * 10**9)
    environment = next(e for e in header(directory)["engines"] if e["plugin"] == "environment")
    assert environment["gusts"] == [[10**9, 3 * 10**9], [8 * 10**9, 9 * 10**9]]


def test_overlay_declares_weather_and_incident_fields() -> None:
    from aeroagentsim.packs.lowalt.overlay import INCIDENT_FIELDS

    assert "lowalt.incident_radius_m" in INCIDENT_FIELDS
    assert "lowalt.incident_end_ns" in INCIDENT_FIELDS
    assert "lowalt.incident_active" in INCIDENT_FIELDS
    fields = {row["id"]: row for row in overlay()["fields"]}
    assert fields["lowalt.precipitation"]["metadata"]["aerograph"] == "oo:digital_twin.precipitation.waterMassFlux"
    assert fields["lowalt.visibility"]["metadata"]["aerograph"] == "he.weather.visibility_m"
    assert fields["lowalt.light"]["metadata"]["role"] == "presentation"


def test_capture_snapshot_retains_journaled_nonspatial_weather(tmp_path: Path) -> None:
    from aerokernel import Stamp

    from aeroagentsim.observations.committed import committed_scene
    from aeroagentsim.observations.contracts import CaptureRequest

    directory = tmp_path / "photograph"
    scenario = load_scenario(_document(tmp_path, weather=True), base=BASE)
    with RunSession(scenario, directory) as session:
        session.start()
        session.run_until(4 * 10**9)
        view = session.simulation.kernel.view()
        actor = next(ref for ref in view._store.lives if ref.id == "drone-1")
        request = CaptureRequest("weather-photo", actor.run_id, actor, view.cut,
                                 {"revision": "fixture", "eye": [0, 0, 50], "target": [200, 0, 0]},
                                 "weather-fixture", Stamp("canonical", view.cut.instant.ns, 1, "canonical"), 128, 96, 10)
        snapshot = committed_scene(view._store, request, header(directory))
    weather = next(entity for entity in snapshot["entities"] if entity["key"]["id"] == "weather")
    facts = {field["fieldId"]: field for field in weather["fields"]}
    assert facts["lowalt.precipitation"]["value"] == 4 / 3600
    assert facts["lowalt.visibility"]["value"] == 500
    assert facts["lowalt.light"]["value"] == "dusk"
    assert all(facts[field]["producer"] == "environment" and facts[field]["validFrom"]["ns"] == "0"
               for field in ("lowalt.precipitation", "lowalt.visibility", "lowalt.light"))
