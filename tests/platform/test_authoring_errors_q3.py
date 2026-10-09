"""The four friction probes retain authored locations and genuine causes."""

from __future__ import annotations

from typing import Any

import pytest
from aerokernel import Kernel
from aerokernel.errors import KernelError

from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.scenario import ScenarioError, load_scenario


@pytest.mark.parametrize(
    "value,code",
    [
        (-5, "VALUE_SCHEMA"),
        (False, "VALUE_SCHEMA"),
        ("-5.0", "VALUE_SCHEMA"),
        (-5.0, "VALUE_BOUND"),
    ],
)
def test_integer_number_probe_and_strict_values(
    document: dict[str, Any],
    value: Any,
    code: str,
) -> None:
    item = document["entities"][0]
    document["registry"]["fields"].append(
        {
            "id": "q3.number",
            "type": item["type"],
            "schema": {"type": "number", "minimum": 0.0},
            "metadata": {},
        }
    )
    item["facts"]["q3.number"] = value
    with pytest.raises(ScenarioError) as caught:
        load_scenario(document)
    assert f"entities.{item['id']}.facts.q3.number" in str(caught.value)
    assert caught.value.code == code
    assert isinstance(caught.value.__cause__, KernelError)
    assert caught.value.__cause__.code == code


def test_float_is_preserved_and_not_coerced(document: dict[str, Any]) -> None:
    item = document["entities"][0]
    field = "aas.p1.energy_j"
    item["facts"][field] = 1.0
    scenario = load_scenario(document)
    assert type(scenario.initial[item["id"]][field]) is float


def test_enum_error_has_entity_and_field_and_original_code(
    document: dict[str, Any],
) -> None:
    item = next(e for e in document["entities"] if "aas.p1.order_state" in e["facts"])
    item["facts"]["aas.p1.order_state"] = "excluded"
    with pytest.raises(ScenarioError) as caught:
        load_scenario(document)
    assert f"entities.{item['id']}.facts.aas.p1.order_state" in str(caught.value)
    assert caught.value.code == "VALUE_ENUM"
    assert isinstance(caught.value.__cause__, KernelError)
    assert caught.value.__cause__.code == "VALUE_ENUM"


def test_unknown_plugin_probe(document: dict[str, Any]) -> None:
    document["engines"]["compute"] = {"plugin": "review_missing", "config": {}}
    with pytest.raises(
        ScenarioError, match=r"engines.compute.*review_missing.*install"
    ) as caught:
        Simulation(load_scenario(document))
    assert isinstance(caught.value.__cause__, ValueError)


def test_unsupported_ast_probe(document: dict[str, Any]) -> None:
    document["engines"]["threshold"]["config"]["ast"]["op"] = "and"
    with pytest.raises(
        ScenarioError, match=r"engines.threshold.config.*threshold.ast.*unsupported"
    ) as caught:
        Simulation(load_scenario(document))
    assert isinstance(caught.value.__cause__, ValueError)


def test_missing_sample_probe_names_engine_and_context(
    document: dict[str, Any],
) -> None:
    context = document["engines"]["threshold"]["config"]["context"]
    document["bindings"]["samples"] = []
    with pytest.raises(ScenarioError) as caught:
        Simulation(load_scenario(document))
    assert "engines.threshold.config.context" in str(caught.value)
    assert context in str(caught.value)
    assert "bindings.samples" in str(caught.value)


def test_sample_context_cannot_bind_another_engine(document: dict[str, Any]) -> None:
    document["bindings"]["samples"][0]["partition"] = "motion"
    with pytest.raises(
        ScenarioError, match=r"engines.threshold.*bound to engine 'motion'"
    ):
        Simulation(load_scenario(document))


@pytest.mark.parametrize("target", ["factory", "catalog", "kernel"])
def test_simulation_never_leaks_stop_iteration(
    document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    def exhausted(*args: Any, **kwargs: Any) -> Any:
        raise StopIteration("scratch plugin exhausted")

    if target == "factory":
        monkeypatch.setattr(EngineCatalog, "build", exhausted)
    elif target == "catalog":
        monkeypatch.setattr(EngineCatalog, "__init__", exhausted)
    else:
        monkeypatch.setattr(Kernel, "bind", exhausted)
    with pytest.raises(ScenarioError) as caught:
        Simulation(load_scenario(document))
    assert isinstance(caught.value.__cause__, StopIteration)
    assert (
        "engines." in str(caught.value)
        if target == "factory"
        else "scenario.bind" in str(caught.value)
    )


def test_engine_registry_value_error_retains_location_code_and_cause(
    document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = KernelError("VALUE_SCHEMA", "scratch configuration value")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise original

    monkeypatch.setattr(EngineCatalog, "build", fail)
    with pytest.raises(ScenarioError) as caught:
        Simulation(load_scenario(document))
    assert "engines." in str(caught.value) and ".config" in str(caught.value)
    assert caught.value.code == "VALUE_SCHEMA"
    assert caught.value.__cause__ is original
