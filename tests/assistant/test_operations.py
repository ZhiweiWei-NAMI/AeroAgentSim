from __future__ import annotations

import pytest

from aeroagentsim.assistant.operations import OperationError, operation_catalogue, validate_operation
from aeroagentsim.assistant.places import find_places, place_catalogue


@pytest.mark.parametrize("spec", operation_catalogue(), ids=lambda s: s["name"])
def test_catalogue_examples_validate(spec: dict) -> None:
    assert spec["description"] and spec["description_zh"]
    assert validate_operation(spec["example"])[0] == spec["name"]


@pytest.mark.parametrize("params", [{"seconds": True}, {"seconds": 0}, {"seconds": float("nan")}, {"seconds": 10, "extra": 1}])
def test_strict_parameters(params: dict) -> None:
    with pytest.raises(OperationError):
        validate_operation({"id": "op-4", "op": "set_duration", "params": params})


def test_places_are_shipped_and_distance_is_horizontal() -> None:
    rows = place_catalogue()
    assert {"plaza", "hospital", "clinic", "east", "north", "bund", "customer-31", "street-east"} <= {r["key"] for r in rows}
    east = next(r for r in rows if r["key"] == "east")
    assert east["position"] == [328.337, -136.882, 12.0]
    matches = find_places("East", near="east", role="handoff_point", limit=3)
    assert matches[0]["distance_m"] == 0
    assert all(len(r["position"]) == 3 for r in rows)
    assert all(r["position"][2] == 0 for r in rows if r["level"] == "street")
    rows[0]["position"][0] = 999
    assert place_catalogue()[0]["position"][0] != 999
