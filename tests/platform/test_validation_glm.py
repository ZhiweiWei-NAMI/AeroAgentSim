"""Parametrized rejection of invalid scenario documents via load_scenario."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import ScenarioError

Document = dict[str, Any]
Mutation = Callable[[Document], None]


def _nonfinite_position(doc: Document) -> None:
    facts = doc["entities"][0]["facts"]
    key = "he.aircraft.position_enu_m"
    facts[key] = [float("nan"), 0.0, 0.0]


CASES: list[tuple[str, Mutation]] = [
    ("negative-seed", lambda doc: doc["run"].update(seed=-1)),
    ("negative-until-ns", lambda doc: doc["run"].update(until_ns=-1)),
    ("zero-advance-ns", lambda doc: doc["run"].update(advance_ns=0)),
    ("boolean-advance-ns", lambda doc: doc["run"].update(advance_ns=True)),
    ("unknown-pacing", lambda doc: doc["run"].update(pacing="warp")),
    ("wrong-frame", lambda doc: doc["presentation"][0].update(frame="eci")),
    ("wrong-version", lambda doc: doc.update(format="aeroagentsim.scenario/v2")),
    ("nonfinite-position", _nonfinite_position),
]


@pytest.mark.parametrize(
    "mutate", [mutate for _, mutate in CASES], ids=[name for name, _ in CASES]
)
def test_invalid_scenario_rejected(document: Document, mutate: Mutation) -> None:
    doc = copy.deepcopy(document)
    mutate(doc)
    with pytest.raises(ScenarioError):
        load_scenario(doc)
