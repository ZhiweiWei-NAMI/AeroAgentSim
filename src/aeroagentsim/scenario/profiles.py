"""Explicit engine replacements applied before scenario pinning."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .loader import Scenario, ScenarioError, UniqueLoader, load_scenario, obj


def apply_engine_profile(scenario: Scenario, path: Path) -> Scenario:
    profile: dict[str, Any] = obj(
        yaml.load(path.read_text(), Loader=UniqueLoader), "profile"
    )
    if (
        set(profile) != {"format", "engines", "messages"}
        or profile["format"] != "aeroagentsim.engine-profile/v1"
    ):
        raise ScenarioError("profile requires engine-profile/v1, engines and messages")
    document = deepcopy(scenario.document)
    document["engines"].update(obj(profile["engines"], "profile.engines"))
    if not isinstance(profile["messages"], list):
        raise ScenarioError("profile.messages must be a list")
    known = {row["id"] for row in document["registry"]["messages"]}
    for row in profile["messages"]:
        if row["id"] in known:
            raise ScenarioError(f"profile duplicates message {row['id']}")
        document["registry"]["messages"].append(row)
        known.add(row["id"])
    return load_scenario(document, base=scenario.base)
