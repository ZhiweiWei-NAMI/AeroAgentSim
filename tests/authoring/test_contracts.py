"""Scientific authoring contracts: actual state evolution and explicit invalidity."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerokernel import Fact, Instant

from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario


def test_authored_command_changes_authoritative_motion(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path, Path("/mnt/data2/weizhiwei/AeroGraph"))
    workspace = store.create("motion")
    workspace = store.place(
        workspace["id"],
        {
            "id": "actor-1",
            "type": "oo:UAV",
            "kind": "entity",
            "position": [0, 0, 10],
            "engine": "kinematic",
        },
    )
    scenario_doc = workspace["scenario"]
    scenario_doc["bindings"]["commands"] = [
        {
            "schema": "aas.studio.move_to",
            "target": "motion",
            "at_ns": 100_000_000,
            "payload": {"entity": "actor-1", "target": [5.0, 0.0, 10.0]},
        }
    ]
    scenario = load_scenario(scenario_doc)
    simulation = Simulation(scenario)
    try:
        simulation.start()
        view = simulation.run_until(3_000_000_000)
        ref = scenario.manifest.entities[0]
        position = view.field(
            (ref, "aas.studio.position_enu_m"), Instant(3_000_000_000)
        )
        energy = view.field((ref, "aas.studio.energy_j"), Instant(3_000_000_000))
        assert isinstance(position, Fact)
        assert list(position.value) == [5.0, 0.0, 10.0]
        assert isinstance(energy, Fact)
        assert energy.value < 100_000.0
    finally:
        simulation.close()


def test_airspace_rejects_collinear_polygon_without_modifying_draft(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(tmp_path, Path("/mnt/data2/weizhiwei/AeroGraph"))
    workspace = store.create("airspace")
    with pytest.raises(ValueError, match="nonzero area"):
        store.place(
            workspace["id"],
            {
                "id": "zone",
                "type": "aas:StudioAirspace",
                "kind": "airspace",
                "position": [0, 0, 0],
                "engine": "workflow",
                "polygon": [[0, 0], [10, 0], [20, 0]],
                "floor_m": 0,
                "ceiling_m": 100,
            },
        )
    assert store.get(workspace["id"]) == workspace


def test_valid_airspace_retains_authored_geometry_and_writer(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path, Path("/mnt/data2/weizhiwei/AeroGraph"))
    workspace = store.create("airspace")
    workspace = store.place(
        workspace["id"],
        {
            "id": "zone",
            "type": "aas:StudioAirspace",
            "kind": "airspace",
            "position": [0, 0, 0],
            "engine": "workflow",
            "polygon": [[0, 0], [10, 0], [0, 20]],
            "floor_m": 0,
            "ceiling_m": 100,
        },
    )
    assert store.validate(workspace["id"])["valid"]
    entity = workspace["scenario"]["entities"][0]
    assert entity["facts"]["aas.studio.airspace"]["polygon"] == [
        [0.0, 0.0],
        [10.0, 0.0],
        [0.0, 20.0],
    ]
    assert {b["writer"] for b in workspace["scenario"]["bindings"]["exact"]} == {
        "operations"
    }


def test_browser_numbers_restore_only_declared_quantities(tmp_path: Path) -> None:
    from aeroagentsim.authoring.wire import normalize_wire

    store = WorkspaceStore(tmp_path, Path("/mnt/data2/weizhiwei/AeroGraph"))
    workspace = store.create("wire")
    workspace = store.place(
        workspace["id"],
        {
            "id": "a",
            "type": "oo:UAV",
            "kind": "entity",
            "position": [0, 0, 10],
            "engine": "kinematic",
        },
    )
    # JavaScript JSON.stringify erases the .0 spelling of floating numbers.
    document = workspace["scenario"]
    document["entities"][0]["facts"]["aas.studio.position_enu_m"] = [0, 0, 10]
    document["entities"][0]["facts"]["aas.studio.energy_j"] = 100000
    result = normalize_wire(document, store.catalog)
    assert all(
        type(v) is float
        for v in result["entities"][0]["facts"]["aas.studio.position_enu_m"]
    )
    assert type(result["run"]["seed"]) is int
    for invalid in (None, False, "0"):
        document["entities"][0]["facts"]["aas.studio.position_enu_m"] = [invalid, 0, 10]
        restored = normalize_wire(document, store.catalog)
        assert (
            restored["entities"][0]["facts"]["aas.studio.position_enu_m"][0] is invalid
        )
        with pytest.raises(ValueError):
            load_scenario(restored)
