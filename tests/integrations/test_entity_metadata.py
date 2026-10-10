"""Optional entity metadata (label/business/vehicle_class) end-to-end contract.

Metadata is authored top-level entity fields; the loader validates it strictly,
the fleet generator propagates it, and the pinned scenario plus viewer header
expose exactly what was authored — no invented defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import header

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def test_entity_metadata_load_generator_header_and_invalid(tmp_path: Path) -> None:
    document = parcel_document(fleet_size=2)
    document["generators"]["fleet"]["label"] = "Drone P{index}"
    document["generators"]["fleet"]["business"] = "logistics"
    document["generators"]["fleet"]["vehicle_class"] = "parcel"
    document["entities"].append(
        {
            "id": "tower-1",
            "type": "lowalt:Customer",
            "label": "North Vertiport",
            "business": "logistics",
            "facts": {"lowalt.site_position": [0.0, 0.0, 50.0]},
        }
    )
    scenario = load_scenario(document, base=BASE)

    generated = [
        e for e in scenario.document["entities"] if e["id"].startswith("drone-")
    ]
    assert [e["label"] for e in generated] == ["Drone P1", "Drone P2"]
    assert all(e["business"] == "logistics" for e in generated)
    # Metadata class must agree with the authoritative motion/energy fact.
    assert all(
        e["vehicle_class"] == e["facts"]["lowalt.vehicle_class"] == "parcel"
        for e in generated
    )
    # Pinned scenario retains authored metadata verbatim.
    pinned = next(e for e in scenario.document["entities"] if e["id"] == "tower-1")
    assert pinned["label"] == "North Vertiport"
    assert pinned["business"] == "logistics"

    from aeroagentsim.platform import RunSession

    directory = tmp_path / "run"
    session = RunSession(scenario, directory)
    try:
        session.simulation.start()
        session.simulation.run_until(1_000_000_000)
    finally:
        session.close()
    header_result = header(directory)
    entities = {e["id"]: e for e in header_result["entities"]}
    assert entities["drone-1"]["label"] == "Drone P1"
    assert entities["drone-2"]["vehicle_class"] == "parcel"
    assert entities["tower-1"]["label"] == "North Vertiport"
    # Customers declare no metadata and receive none.
    assert "label" not in entities["customer-1"]
    assert "business" not in entities["customer-1"]


def test_invalid_entity_metadata_rejected() -> None:
    document = parcel_document(fleet_size=1)
    document["entities"].append(
        {"id": "x-1", "type": "lowalt:Customer", "label": "", "facts": {}}
    )
    with pytest.raises(ValueError, match="label"):
        load_scenario(document, base=BASE)


def test_invalid_fleet_metadata_rejected() -> None:
    document = parcel_document(fleet_size=1)
    document["generators"]["fleet"]["label"] = "Drone {n}"
    with pytest.raises(ValueError, match="label"):
        load_scenario(document, base=BASE)

    document = parcel_document(fleet_size=1)
    document["generators"]["fleet"]["vehicle_class"] = "medical"
    with pytest.raises(ValueError, match="vehicle_class"):
        load_scenario(document, base=BASE)

    document = parcel_document(fleet_size=1)
    document["generators"]["fleet"]["business"] = 3
    with pytest.raises(ValueError, match="business"):
        load_scenario(document, base=BASE)
