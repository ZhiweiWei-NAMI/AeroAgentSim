"""P1 contracts and failure cases against the actual kernel and source slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Cut, Instant
from aerokernel.codec import decode_record
from aerokernel.journal import prefixes, replay

from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.services.projector import header, project
from aeroagentsim.services.storage import RunStorage

POS = "he.aircraft.position_enu_m"
STATE = "aas.p1.order_state"
ACQ = "oo:shared.observationRecord.acquisitionTime"
AVAIL = "oo:shared.observationRecord.availableTime"


def test_arrival_records_and_separate_acceptance(
    slice_run: tuple[RunSession, Path],
) -> None:
    session, directory = slice_run
    commits = [project(record) for record in session.simulation.kernel.records[1:]]
    arrivals: dict[str, int] = {}
    accepted: dict[str, int] = {}
    records: list[str] = []
    facts: dict[str, dict[str, Any]] = {}
    for commit in commits:
        for message in commit["messages"]:
            if message["schemaId"] == "aas.motion.arrived":
                arrivals[message["payload"]["machine"]] = int(commit["at"]["ns"])
        for created in commit["created"]:
            if created["typeId"] == "aas:KinematicObservation":
                records.append(created["id"])
        for fact in commit["facts"]:
            facts.setdefault(fact["entity"]["id"], {})[fact["fieldId"]] = fact["value"]
            if fact["fieldId"] == STATE and fact["value"] == "accepted":
                accepted[fact["entity"]["id"]] = int(commit["at"]["ns"])
    assert len(arrivals) == len(accepted) == len(records) == 10
    for order, at in accepted.items():
        assert at > arrivals[order] + 20_000_000
    for record_id in records:
        record = facts[record_id]
        assert record[AVAIL]["value"] - record[ACQ]["value"] == pytest.approx(0.02)
        assert record[AVAIL]["clockRef"] == record[ACQ]["clockRef"] == "canonical"
        assert record["aas.p1.position_sample"][0] in (30.0, 60.0)
    actions = [
        receipt["status"] for commit in commits for receipt in commit["receipts"]
    ]
    assert actions.count("succeeded") == 10
    assert {"submitted", "accepted", "executing", "succeeded"} <= set(actions)
    assert RunStorage(directory).metadata()["status"] == "completed"


def test_arrival_without_acceptance_never_completes(document: dict[str, Any]) -> None:
    document["engines"].pop("acceptance")
    for sample in document["bindings"]["samples"]:
        sample["upstream"].remove("acceptance")
    document["bindings"]["rules"] = [
        r for r in document["bindings"]["rules"] if r["writer"] != "acceptance"
    ]
    for entity in document["entities"]:
        entity["facts"].pop("aas.p1.acceptance_state", None)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    view = simulation.run_until(document["run"]["until_ns"])
    for ref in simulation.scenario.manifest.entities:
        if ref.type_id == "oo:Order":
            assert view.field((ref, STATE), view.instant).value == "awaiting_acceptance"  # type: ignore[union-attr]
    simulation.close()


def test_delayed_record_is_absent_before_release(document: dict[str, Any]) -> None:
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(6_000_000_000)
    commits = [project(record) for record in simulation.kernel.records[1:]]
    arrival = next(
        c
        for c in commits
        if any(m["schemaId"] == "aas.motion.arrived" for m in c["messages"])
    )
    at = int(arrival["at"]["ns"])
    creates = [
        (c, e)
        for c in commits
        for e in c["created"]
        if e["typeId"] == "aas:KinematicObservation"
    ]
    assert creates
    assert all(int(c["at"]["ns"]) == at + 20_000_000 for c, _ in creates)
    simulation.close()


def test_same_seed_bytes_and_viewer_cadence(
    document: dict[str, Any], tmp_path: Path
) -> None:
    scenario = load_scenario(document)
    results: list[bytes] = []
    for name, poll in [("one", False), ("two", True)]:
        with RunSession(scenario, tmp_path / name) as session:
            session.start()
            for ns in range(
                scenario.advance_ns, scenario.until_ns + 1, scenario.advance_ns
            ):
                session.run_until(ns)
                if poll:
                    for _ in range(4):
                        header(session.storage.directory)
                        [project(record) for record in session.storage.records(1, 25)]
            results.append(session.simulation.kernel.journal.bytes)
    assert results[0] == results[1]


def test_engine_free_replay_fields_actions_events_for_prefixes(
    slice_run: tuple[RunSession, Path],
) -> None:
    session, directory = slice_run
    live = session.simulation.kernel
    data = (directory / "journal.jsonl").read_bytes()
    records = live.records
    selected = {0, 1, 2, 3, len(records)}
    # Cover each semantic boundary, rather than replaying identical feedback
    # shapes at all 220 physical steps (the separate small run checks all cuts).
    seen: set[str] = set()
    for i, record in enumerate(records):
        tags = {"edge" for item in record["items"] if "edge" in item}
        tags.update("sample" for item in record["items"] if "sample_frame" in item)
        tags.update(
            "receipt/" + str(item["receipt"]["status"])
            for item in record["items"]
            if "receipt" in item
        )
        tags.update(
            "receipt/" + str(item["status"])
            for item in record["items"]
            if item.get("kind") == "receipt"
        )
        if tags - seen:
            selected.add(i + 1)
            seen.update(tags)
    for index, prefix in enumerate(prefixes(data)):
        if index not in selected:
            continue
        reconstructed = replay(prefix)
        assert reconstructed.records == records[:index]
        cut = reconstructed.view().cut
        assert cut == Cut(
            index,
            Instant(0) if index == 0 else decode_record(records[index - 1]["instant"]),
        )
        historical = live.view(cut)
        command_ids = {
            item["receipt"]["command_id"]
            for record in records[:index]
            for item in record["items"]
            if "receipt" in item
        }
        command_ids.update(
            item["command_id"]
            for record in records[:index]
            for item in record["items"]
            if item.get("kind") == "receipt"
        )
        for command_id in command_ids:
            assert reconstructed.view().action(command_id) == historical.action(
                command_id
            )
        assert reconstructed.view().relations(
            "oo:relation:observation-subject", cut.instant
        ) == historical.relations("oo:relation:observation-subject", cut.instant)
        assert reconstructed.view().sample_frames(
            "aas.p1.reached_x"
        ) == historical.sample_frames("aas.p1.reached_x")
        for ref in session.scenario.manifest.entities:
            if index < 2:
                continue
            fields = session.scenario.initial[ref.id]
            for field in fields:
                assert reconstructed.view().field(
                    (ref, field), cut.instant
                ) == historical.field((ref, field), cut.instant)
    assert not replay(data).incomplete


def test_small_run_replays_every_complete_prefix(document: dict[str, Any]) -> None:
    # A compact real execution covers intent-only, reset, dirty and seal prefixes.
    document["run"]["until_ns"] = 0
    simulation = Simulation(load_scenario(document))
    simulation.start()
    for index, prefix in enumerate(prefixes(simulation.kernel.journal.bytes)):
        assert replay(prefix).records == simulation.kernel.records[:index]
    simulation.close()


def test_projector_contract_and_lossless_times(
    slice_run: tuple[RunSession, Path],
) -> None:
    session, directory = slice_run
    run_header = header(directory)
    assert run_header["contract"] == "aeroagentsim.viewer-feed/v1"
    runtime_bytes = (directory / "runtime.registry.json").read_bytes()
    assert run_header["registryDigest"] == hashlib.sha256(runtime_bytes).hexdigest()
    assert run_header["runtimeRegistry"] == json.loads(runtime_bytes)
    assert run_header["messages"] == run_header["runtimeRegistry"]["messages"]
    commands = [m for m in run_header["messages"] if m["kind"] == "command"]
    assert commands and all(
        "schema" in m and "result_schema" in m and "cancel_support" in m
        for m in commands
    )
    sample = next(
        f for f in run_header["fields"] if f["fieldId"] == "aas.p1.position_sample"
    )
    assert sample["frame"] == session.scenario.registry.field("aas.p1.position_sample").metadata["frame"]
    linked = [
        m
        for record in session.simulation.kernel.records
        for m in project(record)["messages"]
        if m["kind"] == "command"
    ]
    assert linked and all(m["subjects"][0]["id"].startswith("uav-") for m in linked)
    assert all(isinstance(f["unit"], (str, type(None))) for f in run_header["fields"])
    assert all(isinstance(f["frame"], (str, type(None))) for f in run_header["fields"])
    shape = {
        "commitIndex",
        "at",
        "created",
        "removed",
        "facts",
        "retracted",
        "edges",
        "messages",
        "receipts",
    }
    for record in session.simulation.kernel.records[1:]:
        commit = project(record)
        assert set(commit) == shape
        assert isinstance(commit["at"]["ns"], str)
        assert int(commit["at"]["ns"]) >= 0
    artificial = {
        "index": 3,
        "instant": {"$type": "Instant", "fields": {"ns": 2**63 + 7, "microstep": 2}},
        "items": [],
    }
    assert project(artificial)["at"]["ns"] == str(2**63 + 7)


def test_threshold_known_false_to_true_only(
    slice_run: tuple[RunSession, Path], document: dict[str, Any]
) -> None:
    session, _ = slice_run
    events = [
        m
        for r in session.simulation.kernel.records[1:]
        for m in project(r)["messages"]
        if m["schemaId"] == "aas.p1.reached_x"
    ]
    assert len(events) == 5
    assert all(m["payload"]["from_ns"] < m["payload"]["to_ns"] for m in events)
    # An initial true value has no prior false sample and emits nothing.
    for entity in document["entities"]:
        if entity["type"] == "oo:UAV":
            entity["facts"][POS][0] = 20.0
    document["run"]["until_ns"] = 1_000_000_000
    simulation = Simulation(load_scenario(document))
    simulation.start()
    simulation.run_until(1_000_000_000)
    assert not [
        m
        for r in simulation.kernel.records[1:]
        for m in project(r)["messages"]
        if m["schemaId"] == "aas.p1.reached_x"
    ]
    simulation.close()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format="v0"), "format"),
        (lambda d: d["entities"][0]["facts"].update({POS: [0.0, 0.0]}), "vector"),
        (lambda d: d["entities"].append(d["entities"][0]), "duplicate"),
        (lambda d: d["run"].update(seed=True), "seed"),
        (lambda d: d["engines"]["motion"].update(plugin="missing"), "unknown plugin"),
        (lambda d: d["entities"][0]["facts"].pop("aas.p1.energy_j"), "energy"),
    ],
)
def test_bad_scenarios_are_actionable(
    document: dict[str, Any], change: Any, message: str
) -> None:
    change(document)
    with pytest.raises((ScenarioError, ValueError), match=message):
        simulation = Simulation(load_scenario(document))
        simulation.close()


def test_duplicate_yaml_key(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("format: one\nformat: two\n")
    with pytest.raises(ScenarioError, match="duplicate key"):
        load_scenario(path)


def test_no_legacy_imports() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import aeroagentsim.platform; import sys; assert "simpy" not in sys.modules; assert "aeroagentsim.core" not in sys.modules',
        ],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_relation_and_sampled_frame_gate(slice_run: tuple[RunSession, Path]) -> None:
    session, _ = slice_run
    kernel = session.simulation.kernel
    view = kernel.view()
    edges = view.relations("oo:relation:observation-subject", view.instant)
    assert len(edges) == 10
    assert all(edge.source.type_id == "aas:KinematicObservation" for edge in edges)
    frames = view.sample_frames("aas.p1.reached_x")
    assert frames
    times = [frame.frame.physical_ns for frame in frames]
    assert len(times) == len(set(times))
    assert frames[0].frame.physical_ns == 0
    assert all(
        state["value"] is False for state in frames[0].frame.result["states"].values()
    )
