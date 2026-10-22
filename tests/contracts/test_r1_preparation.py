"""Final-seed, side-effect-free preparation and isolated discovery contracts.

These tests are generated acceptance coverage, not evidence of an executed run.
"""

from __future__ import annotations

import builtins
import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import urllib.request
from dataclasses import FrozenInstanceError
from datetime import date, datetime
from multiprocessing.process import BaseProcess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import aeroagentsim.preparation_api as preparation_api
import aeroagentsim.scenario.preparation as preparation
from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.preparation_api import (
    PREPARATION_API_VERSION,
    PreparedScenario,
    PreparerPlugin,
    ReadOnlyResources,
    discover_preparers,
    document_digest,
    resource_manifest,
)
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.scenario.loader import UniqueLoader

ROOT = Path(__file__).resolve().parents[2]
RESCUE = ROOT / "scenarios/templates/flood-rescue"


@pytest.fixture(autouse=True)
def isolate_installed_preparers(monkeypatch: pytest.MonkeyPatch) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.preparers"
        return ()

    monkeypatch.setattr(preparation_api, "entry_points", query)


def document() -> dict[str, Any]:
    return {
        "format": "aeroagentsim.scenario/v2",
        "id": "independent-fleet",
        "preparation": {"plugin": "fleet", "api_version": 1, "config": {"count": 2}},
        "registry": {
            "snapshot": str(demo_source("traffic-accident") / "registry.snapshot.json"),
            "types": [{"id": "example:Unit", "parents": ["oo:ModelObject"], "abstract": False}],
            "fields": [{
                "id": "example.position", "type": "example:Unit",
                "schema": {"type": "vector", "length": 3, "items": {"type": "number"}},
                "metadata": {"frame": "enu", "transform_revision": "example/v1"},
            }],
        },
        "entities": [],
        "bindings": {
            "rules": [{"writer": "units", "type": "example:Unit", "fields": ["example.position"]}],
            "lifecycle": [{"controller": "units", "type": "example:Unit"}],
        },
        "engines": {"units": {"plugin": "test", "config": {}}},
        "run": {"seed": 42, "pacing": "fast", "until_ns": 1, "advance_ns": 1},
        "outputs": {"durability": "flush"},
    }


def validate_fleet_config(config: dict[str, Any]) -> None:
    if set(config) != {"count"} or type(config["count"]) is not int or not 1 <= config["count"] <= 3:
        raise ValueError("preparation.config: count must be an integer in [1, 3]")


def fleet(
    authored: dict[str, Any], final_seed: int, resources: ReadOnlyResources,
) -> PreparedScenario:
    result = copy.deepcopy(authored)
    result["entities"] = [{
        "id": f"unit-{index}", "type": "example:Unit",
        "facts": {"example.position": [float(final_seed), float(index), 0.0]},
    } for index in range(authored["preparation"]["config"]["count"])]
    return PreparedScenario(result, resource_manifest=resource_manifest(resources))


def fleet_plugin() -> PreparerPlugin:
    return PreparerPlugin("fleet", fleet, revision="fleet/1", validate_config=validate_fleet_config)


def entry(name: str, value: Any, calls: list[str] | None = None) -> SimpleNamespace:
    def load() -> Any:
        if calls is not None:
            calls.append(name)
        return value

    return SimpleNamespace(
        name=name, value=f"independent.{name}:plugin", load=load,
        dist=SimpleNamespace(name="independent-preparation", version="0.1"),
    )


def install(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.preparers"
        return entries

    monkeypatch.setattr(preparation_api, "entry_points", query)


def test_two_final_seeds_have_exact_expected_fleets_and_same_seed_is_repeatable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, entry("fleet", fleet_plugin))
    authored = document()
    before = copy.deepcopy(authored)
    first = load_scenario(authored, final_seed=0)
    second = load_scenario(authored, final_seed=7)
    expected = lambda seed: {
        f"unit-{index}": {"example.position": [float(seed), float(index), 0.0]}
        for index in range(2)
    }
    assert first.initial == expected(0)
    assert second.initial == expected(7)
    assert first.initial != second.initial
    assert first.seed == 0 and second.seed == 7
    again = load_scenario(authored, final_seed=0)
    assert document_digest(first.document) == document_digest(again.document)
    assert first.preparation == again.preparation
    assert first.preparation is not None
    selected = first.preparation.contribution_selections[0]
    assert selected["name"] == "fleet" and selected["final_seed"] == 0
    assert selected["api_version"] == 1 and selected["revision"] == "fleet/1"
    assert selected["entry_point"] == "independent.fleet:plugin"
    assert selected["distribution"] == ["independent-preparation", "0.1"]
    assert selected["config_digest"] == document_digest({"count": 2})
    assert "config" not in selected
    assert authored == before
    # Detached previous outputs cannot contaminate the next preparation.
    first.preparation.document["entities"].clear()
    first.preparation.contribution_selections[0]["revision"] = "tampered"
    assert load_scenario(authored, final_seed=0).preparation == again.preparation


def test_lowalt_seeded_fleet_preserves_existing_seed_one_oracle() -> None:
    authored = yaml.load((RESCUE / "scenario.yaml").read_bytes(), Loader=UniqueLoader)
    before = copy.deepcopy(authored)
    first = load_scenario(authored, base=RESCUE, final_seed=1)
    second = load_scenario(authored, base=RESCUE, final_seed=2)
    profiles = first.engines["tasks"]["config"]["rescue"]["vehicles"]
    # Existing independent regression expectations from test_lowalt_rescue.py;
    # do not compute an expected fleet by invoking the sampler under test.
    assert {name: {key: row[key] for key in ("position", "capabilities", "soc")}
            for name, row in profiles.items()} == {
        "U1": {"position": [-134.691, -55.77, 168.189], "capabilities": ["inspect", "relay", "deliver"], "soc": 0.7561},
        "U2": {"position": [-221.842, -401.496, 171.788], "capabilities": ["inspect", "deliver"], "soc": 0.6118},
        "U3": {"position": [-21.371, 33.581, 175.071], "capabilities": ["inspect", "relay"], "soc": 0.9574},
        "U4": {"position": [34.98, -273.365, 131.452], "capabilities": ["inspect", "relay", "deliver"], "soc": 0.4632},
    }
    assert profiles["U3"]["payload_kg"] == 0.0
    assert first.initial != second.initial
    for scene in (first, second):
        for name, profile in scene.engines["tasks"]["config"]["rescue"]["vehicles"].items():
            assert scene.initial[name]["lowalt.position"] == profile["position"]
            assert scene.initial[name]["lowalt.payload_kg"] == profile["payload_kg"]
    assert document_digest(first.document) == document_digest(
        load_scenario(authored, base=RESCUE, final_seed=1).document
    )
    assert authored == before
    assert first.preparation is not None
    assert first.preparation.contribution_selections[0]["revision"] == "lowalt-legacy/v1"
    # Already materialized compatibility documents do not sample another fleet.
    assert load_scenario(first.document, base=RESCUE).initial == first.initial


def test_template_preview_is_not_the_source_of_later_seeded_runs() -> None:
    from aeroagentsim.scenarios import BUILTINS

    registry = TemplateRegistry(plugins=tuple(factory() for factory in BUILTINS.values()))
    template = registry.apply("flood-rescue", {"participation": "all", "radio": "off"})
    before = copy.deepcopy(template.document)
    first = template.prepare(final_seed=0)
    second = template.prepare(final_seed=1)
    assert first.seed == 0 and second.seed == 1 and first.initial != second.initial
    assert template.document == before
    assert template.document["engines"]["tasks"]["config"]["rescue"]["scene_base"] == "lowalt"


def test_preparation_performs_no_writes_network_provider_calls_or_process_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.agents.langgraph_client import JournalClient, ScriptedProvider
    from aeroagentsim.agents.provider import OpenAIProvider
    from aeroagentsim.packs.lowalt.preparation import prepare as lowalt_prepare

    authored = yaml.load((RESCUE / "scenario.yaml").read_bytes(), Loader=UniqueLoader)
    resources = {"registry.snapshot": (RESCUE / "registry.snapshot.json").read_bytes()}
    before = copy.deepcopy(authored)
    activities: list[str] = []
    original_open, original_io_open, original_os_open = builtins.open, io.open, os.open

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        activities.append("provider/network/process")
        raise AssertionError("preparation must not perform external activity")

    def guard_open(original: Any) -> Any:
        def guarded(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if any(flag in mode for flag in "wax+"):
                activities.append("file write")
                raise AssertionError("preparation must not open files for writing")
            return original(file, mode, *args, **kwargs)
        return guarded

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> Any:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            activities.append("file write")
            raise AssertionError("preparation must not open files for writing")
        return original_os_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as guards:
        # Cold lazy imports are code loading, not preparer output. Disable the
        # interpreter's optional .pyc cache so it cannot trip the write guards.
        guards.setattr(sys, "dont_write_bytecode", True)
        guards.setattr(builtins, "open", guard_open(original_open))
        guards.setattr(io, "open", guard_open(original_io_open))
        guards.setattr(os, "open", guarded_os_open)
        guards.setattr(socket, "create_connection", forbidden)
        guards.setattr(socket.socket, "connect", forbidden)
        guards.setattr(socket.socket, "connect_ex", forbidden)
        guards.setattr(urllib.request, "urlopen", forbidden)
        guards.setattr(subprocess, "Popen", forbidden)
        guards.setattr(BaseProcess, "start", forbidden)
        guards.setattr(os, "system", forbidden)
        for provider, methods in (
            (JournalClient, ("__init__", "complete", "ask_json")),
            (ScriptedProvider, ("__init__", "complete", "scripted")),
            (OpenAIProvider, ("__init__", "from_config", "complete")),
        ):
            for method in methods:
                guards.setattr(provider, method, forbidden)
        # Test the pure callable independently with captured bytes and direct
        # resource-file reads blocked, then host acquisition with read-only I/O.
        with guards.context() as no_reads:
            no_reads.setattr(builtins, "open", forbidden)
            no_reads.setattr(io, "open", forbidden)
            no_reads.setattr(os, "open", forbidden)
            no_reads.setattr(Path, "open", forbidden)
            no_reads.setattr(Path, "read_bytes", forbidden)
            no_reads.setattr(Path, "read_text", forbidden)
            pure = lowalt_prepare(authored, 0, resources)
        driven = preparation.prepare_scenario(authored, 0, base=RESCUE)
        assert driven.document == pure.document
        assert preparation.prepare_scenario(authored, 0, base=RESCUE) == driven
    assert activities == []
    assert authored == before
    assert driven.resource_manifest == resource_manifest(resources)


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0, None])
def test_selector_api_version_is_exact_and_fails_before_preparer(
    monkeypatch: pytest.MonkeyPatch, version: Any,
) -> None:
    authored = document()
    authored["preparation"]["api_version"] = version
    monkeypatch.setattr(preparation, "discover_preparers", lambda: pytest.fail("discovery ran"))
    with pytest.raises(ScenarioError, match="version"):
        load_scenario(authored)


@pytest.mark.parametrize("selector", [None, "fleet", [], {}, {"plugin": "fleet", "api_version": 1}])
def test_malformed_selection_is_rejected(monkeypatch: pytest.MonkeyPatch, selector: Any) -> None:
    authored = document()
    authored["preparation"] = selector
    monkeypatch.setattr(preparation, "discover_preparers", lambda: pytest.fail("discovery ran"))
    with pytest.raises(ScenarioError, match="preparation"):
        load_scenario(authored)


@pytest.mark.parametrize("config", [None, [], "config", {}, {"count": True}, {"count": 0}, {"count": 2, "typo": 1}])
def test_wrong_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch, config: Any) -> None:
    authored = document()
    authored["preparation"]["config"] = config
    plugin = PreparerPlugin(
        "fleet", lambda *args: pytest.fail("preparer ran"), revision="test/1",
        validate_config=validate_fleet_config,
    )
    install(monkeypatch, entry("fleet", plugin))
    with pytest.raises(ScenarioError, match="config"):
        load_scenario(authored)


@pytest.mark.parametrize("seed", [True, 1.0, "1", -1])
def test_invalid_final_seed_never_reaches_preparation(
    monkeypatch: pytest.MonkeyPatch, seed: Any,
) -> None:
    monkeypatch.setattr(preparation, "discover_preparers", lambda: pytest.fail("discovery ran"))
    with pytest.raises(ScenarioError, match="run.seed"):
        load_scenario(document(), final_seed=seed)


def test_unknown_version_selector_and_unknown_builtin_config_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authored = document()
    authored["format"] = "aeroagentsim.scenario/v3"
    with pytest.raises(ScenarioError, match="format"):
        load_scenario(authored)
    authored = document()
    authored["preparation"]["plugin"] = "not-installed"
    with pytest.raises(ScenarioError, match="unknown preparer"):
        load_scenario(authored)
    for name in ("explicit", "lowalt-legacy"):
        authored["preparation"] = {"plugin": name, "api_version": 1, "config": {"typo": True}}
        with pytest.raises(ScenarioError, match="config"):
            load_scenario(authored)
    authored["preparation"] = {"plugin": "explicit", "api_version": 1, "config": {}, "typo": True}
    with pytest.raises(ScenarioError, match="unknown keys"):
        load_scenario(authored)


def test_v2_legacy_adapter_keeps_selection_after_scene_base_materialization() -> None:
    authored = yaml.load((RESCUE / "scenario.yaml").read_bytes(), Loader=UniqueLoader)
    authored["format"] = "aeroagentsim.scenario/v2"
    authored["preparation"] = {"plugin": "lowalt-legacy", "api_version": 1, "config": {}}
    scene = load_scenario(authored, base=RESCUE, final_seed=0)
    assert scene.document["format"] == authored["format"]
    assert scene.document["preparation"] == authored["preparation"]
    assert scene.preparation is not None
    assert scene.preparation.contribution_selections[0]["name"] == "lowalt-legacy"


def test_generic_v2_admission_does_not_apply_city_or_vehicle_class_policy() -> None:
    authored = document()
    authored["preparation"] = {"plugin": "explicit", "api_version": 1, "config": {}}
    authored["city"] = "independent-city"
    authored["registry"]["fields"].append({
        "id": "lowalt.vehicle_class", "type": "example:Unit",
        "schema": {"type": "string"}, "metadata": {},
    })
    authored["bindings"]["rules"][0]["fields"].append("lowalt.vehicle_class")
    authored["entities"] = [{
        "id": "independent-unit", "type": "example:Unit", "vehicle_class": "display-class",
        "facts": {"example.position": [0.0, 0.0, 0.0], "lowalt.vehicle_class": "other-class"},
    }]
    loaded = load_scenario(authored)
    assert loaded.document["city"] == "independent-city"
    assert loaded.document["entities"] == authored["entities"]
    legacy = copy.deepcopy(authored)
    legacy["format"] = "aeroagentsim.scenario/v1"
    del legacy["preparation"]
    with pytest.raises(ScenarioError, match="city"):
        load_scenario(legacy)
    legacy["city"] = "huangpu"
    with pytest.raises(ScenarioError, match="vehicle_class"):
        load_scenario(legacy)


def test_vehicle_task_kinds_only_does_not_install_or_conflict_with_cooperation_types(
    tmp_path: Path,
) -> None:
    from aeroagentsim.packs.lowalt.template import expand

    base = ROOT / "scenarios/templates/parcel-delivery"
    authored = yaml.load((base / "scenario.yaml").read_bytes(), Loader=UniqueLoader)
    expand(authored)
    del authored["algorithms"]
    del authored["generators"]
    authored["engines"]["tasks"]["config"]["vehicle_task_kinds"] = {"drone-1": ["parcel"]}
    snapshot = json.loads((base / "registry.snapshot.json").read_bytes())
    snapshot["registry"]["types"].extend({
        "id": name, "parents": ["oo:ModelObject"], "abstract": False,
    } for name in ("lowalt:Incident", "lowalt:Handoff", "lowalt:HandoffPoint"))
    path = tmp_path / "with-unused-cooperation-types.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    authored["registry"]["snapshot"] = str(path)
    legacy = copy.deepcopy(authored)
    expand(legacy)  # Historical direct helper: no cooperation overlay is selected.
    prepared = preparation.prepare_scenario(authored, base=base)
    assert document_digest(prepared.document) == document_digest(legacy)
    assert not any(row["id"] == "lowalt:Incident" for row in prepared.document["registry"]["types"])


def test_declared_resources_are_snapshotted_read_only_and_hashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authored = document()
    authored["preparation"]["resources"] = {"fixture": "fixture.json"}
    snapshot = Path(authored["registry"]["snapshot"]).read_bytes()
    supplied = {"registry.snapshot": snapshot, "fixture": b'{"known":null}'}

    def check_resources(doc: dict[str, Any], seed: int, resources: ReadOnlyResources) -> PreparedScenario:
        assert resources["fixture"] == b'{"known":null}'
        with pytest.raises(TypeError):
            resources["fixture"] = b"changed"  # type: ignore[index]
        return fleet(doc, seed, resources)

    plugin = PreparerPlugin("fleet", check_resources, "fleet/2", validate_config=validate_fleet_config)
    install(monkeypatch, entry("fleet", plugin))
    result = preparation.prepare_scenario(authored, 0, supplied)
    assert result.resource_manifest == resource_manifest(supplied)
    assert result.document["preparation"]["config"] == {"count": 2}
    assert json.loads(supplied["fixture"]) == {"known": None}
    with pytest.raises(ScenarioError, match="resource names"):
        preparation.prepare_scenario(authored, 0, {"registry.snapshot": snapshot})
    authored["preparation"]["resources"] = {"registry.snapshot": "different.json"}
    with pytest.raises(ScenarioError, match="reserved resource"):
        preparation.prepare_scenario(authored, 0, supplied)


@pytest.mark.parametrize("yaml_value,expected_type", [
    ("2026-07-13", date),
    ("2026-07-13T12:34:56.123456+02:00", datetime),
    ("!!binary aGVsbG8=", bytes),
    ("!!set {second: null, first: null}", set),
    ("!!pairs [{first: 1}, {second: 2}]", list),
    ("!!omap [{first: 1}, {second: 2}]", list),
])
def test_legacy_yaml_digest_accepts_safe_loader_values_without_rewriting(
    yaml_value: str, expected_type: type,
) -> None:
    value = yaml.load(yaml_value, Loader=UniqueLoader)
    assert type(value) is expected_type
    document = {"format": "aeroagentsim.scenario/v1", "value": value}
    before = copy.deepcopy(document)
    prepared = PreparedScenario(document)
    assert prepared.document == before
    assert type(prepared.document["value"]) is expected_type
    digest = document_digest(document, legacy_yaml=True)
    assert digest == document_digest(copy.deepcopy(document), legacy_yaml=True)
    assert document == before
    if expected_type is not list:  # Pairs/ordered maps are already JSON-compatible.
        with pytest.raises(ValueError, match="expected finite JSON data"):
            document_digest(document)
        with pytest.raises(ValueError, match="expected finite JSON data"):
            PreparedScenario({**document, "format": "aeroagentsim.scenario/v2"})


@pytest.mark.parametrize("yaml_value", [
    pytest.param(".inf", id="infinity"),
    pytest.param("-.inf", id="negative-infinity"),
    pytest.param(".nan", id="nan"),
    pytest.param("!!pairs [{ceiling: .inf}, {unknown: .nan}]", id="pairs-nonfinite"),
    pytest.param("!!omap [{ceiling: -.inf}, {unknown: .nan}]", id="omap-nonfinite"),
    pytest.param("!!set {.inf: null, .nan: null, 2026-07-13: null}", id="mixed-set"),
    pytest.param('&loop [*loop, !!binary aGVsbG8=]', id="recursive-sequence"),
    pytest.param('&loop {self: *loop, date: 2026-07-13}', id="recursive-mapping"),
    pytest.param('"\\uD800"', id="escaped-surrogate"),
    pytest.param("0x" + "f" * 4000, id="large-hex-integer"),
])
def test_legacy_digest_and_prepared_output_are_total_for_safe_loader_values(
    yaml_value: str,
) -> None:
    # Use independent constructions: NaN cannot be checked by value equality,
    # and recursive YAML aliases cannot be compared with ordinary tree walkers.
    documents = [
        {"format": "aeroagentsim.scenario/v1", "value": yaml.safe_load(yaml_value)}
        for _ in range(2)
    ]
    first = document_digest(documents[0], legacy_yaml=True)
    assert first == document_digest(documents[1], legacy_yaml=True)
    for document in documents:
        prepared = PreparedScenario(document)
        assert first == document_digest(prepared.document, legacy_yaml=True)


@pytest.mark.parametrize("negative", [False, True])
def test_legacy_large_integer_digest_and_envelope_never_use_decimal_json(
    monkeypatch: pytest.MonkeyPatch, negative: bool,
) -> None:
    digit_limit = sys.get_int_max_str_digits()
    hex_text = ("-0x" if negative else "0x") + "f" * 4000
    number = int(hex_text, 16)

    def forbidden(value: Any) -> bytes:
        pytest.fail("legacy digest/envelope must not attempt decimal JSON encoding")

    # Reject even an attempted JSON-first path, irrespective of the interpreter's
    # existing limit (including when the caller started Python with no limit).
    monkeypatch.setattr(preparation_api, "_canonical_json", forbidden)
    evidence = b'aeroagentsim.yaml-evidence/v1\0' + (
        b'["int","' + hex_text.encode("ascii") + b'"]'
    )
    assert document_digest(number, legacy_yaml=True) == (
        "sha256:" + hashlib.sha256(evidence).hexdigest()
    )
    document = {
        "format": "aeroagentsim.scenario/v1",
        "engines": {"probe": {"plugin": "probe", "config": {"value": number}}},
        "run": {"seed": abs(number)},
    }
    selections = ({"final_seed": abs(number)},)
    prepared = PreparedScenario(document, selections)
    assert prepared.document == document
    assert prepared.contribution_selections == selections
    assert document_digest(prepared.document, legacy_yaml=True) == document_digest(
        copy.deepcopy(document), legacy_yaml=True,
    )
    assert document_digest(number, legacy_yaml=True) != document_digest(
        hex_text, legacy_yaml=True,
    )
    assert sys.get_int_max_str_digits() == digit_limit


@pytest.mark.parametrize("format_id", ["aeroagentsim.scenario/v1", "aeroagentsim.scenario/v2"])
@pytest.mark.parametrize("value", [
    float("inf"), float("-inf"), float("nan"), date(2026, 7, 13),
    b"binary", {"member"}, {1: "non-string-key"}, "\ud800", {"\ud800": "key"},
])
def test_selection_metadata_stays_strict_finite_json(format_id: str, value: Any) -> None:
    with pytest.raises(ValueError):
        PreparedScenario({"format": format_id}, ({"value": value},))


@pytest.mark.parametrize("leaf", ["null", "!!binary aGVsbG8="])
def test_legacy_digest_handles_deep_safe_loader_alias_nesting(leaf: str) -> None:
    # Shallow YAML syntax can construct a value deeper than Python's recursion
    # limit: each newly anchored sequence contains the preceding sequence.
    depth = 1500
    source = f"n0: &n0 [{leaf}]\n" + "\n".join(
        f"n{index}: &n{index} [*n{index - 1}]" for index in range(1, depth)
    )
    first = yaml.safe_load(source)[f"n{depth - 1}"]
    second = yaml.safe_load(source)[f"n{depth - 1}"]
    assert document_digest(first, legacy_yaml=True) == document_digest(second, legacy_yaml=True)


@pytest.mark.parametrize("yaml_value", [
    ".inf", "-.inf", ".nan", "!!pairs [{ceiling: .inf}]",
    "!!omap [{unknown: .nan}]", "!!binary aGVsbG8=",
    "!!set {first: null}", "2026-07-13", "2026-07-13T12:34:56Z",
])
def test_v2_prepared_output_still_rejects_non_json_safe_loader_values(yaml_value: str) -> None:
    document = {"format": "aeroagentsim.scenario/v2", "value": yaml.safe_load(yaml_value)}
    with pytest.raises(ValueError):
        document_digest(document)
    with pytest.raises(ValueError):
        PreparedScenario(document)


def test_legacy_yaml_digest_is_canonical_and_type_tagged() -> None:
    first = {"date": date(2026, 7, 13), "members": {"second", "first"}}
    reordered = {"members": {"first", "second"}, "date": date(2026, 7, 13)}
    digest = document_digest(first, legacy_yaml=True)
    assert digest == document_digest(reordered, legacy_yaml=True)
    assert digest != document_digest({**first, "date": "2026-07-13"}, legacy_yaml=True)
    assert digest != document_digest({**first, "date": ["date", "2026-07-13"]}, legacy_yaml=True)
    assert digest != document_digest({**first, "date": datetime(2026, 7, 13)}, legacy_yaml=True)
    # SafeLoader sets and pairs can contain non-string keys even though ordinary
    # scenario mappings require strings. Evidence must not narrow their values.
    assert document_digest({date(2026, 7, 13), b"hello", 3}, legacy_yaml=True)
    assert document_digest({3: date(2026, 7, 13)}, legacy_yaml=True)
    plain = {"nested": [None, True, 1, 1.5, "text"]}
    assert document_digest(plain) == "sha256:" + hashlib.sha256(
        b'{"nested":[null,true,1,1.5,"text"]}'
    ).hexdigest()
    # Legacy evidence is always domain-separated, even for ordinary finite JSON.
    assert document_digest(plain, legacy_yaml=True) != document_digest(plain)
    # Vary otherwise JSON-compatible node types within the tagged evidence.
    def tagged(value: Any) -> str:
        return document_digest({"date": date(2026, 7, 13), "value": value}, legacy_yaml=True)

    assert len({tagged(True), tagged(1), tagged(1.0), tagged("1")}) == 4
    assert tagged([1, 2]) != tagged((1, 2))
    assert tagged([1, 2]) != tagged([2, 1])
    assert tagged({3: b"hello", "date": date(2026, 7, 13)}) == tagged(
        {"date": date(2026, 7, 13), 3: b"hello"}
    )
    assert tagged(yaml.safe_load("!!set {.inf: null, .nan: null, first: null}")) == tagged(
        yaml.safe_load("!!set {first: null, .nan: null, .inf: null}")
    )
    assert tagged(yaml.safe_load("&loop {self: *loop, value: 1}")) == tagged(
        yaml.safe_load("&loop {value: 1, self: *loop}")
    )


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0, None])
def test_descriptor_rejects_unsupported_versions(version: Any) -> None:
    with pytest.raises(ValueError, match="version"):
        PreparerPlugin("fleet", fleet, "fleet/1", api_version=version)


def test_descriptors_are_frozen_and_reject_async_callables() -> None:
    assert type(PREPARATION_API_VERSION) is int and PREPARATION_API_VERSION == 1
    plugin = fleet_plugin()
    with pytest.raises(FrozenInstanceError):
        plugin.revision = "changed"  # type: ignore[misc]

    async def async_prepare(*args: Any) -> PreparedScenario:
        raise AssertionError("never called")

    class AsyncCallable:
        async def __call__(self, *args: Any) -> PreparedScenario:
            raise AssertionError("never called")

    for method in (None, async_prepare, AsyncCallable()):
        with pytest.raises(ValueError, match="synchronous"):
            PreparerPlugin("fleet", method, "fleet/1")
    for name in ("", "MixedCase", "with.dot", "-leading"):
        with pytest.raises(ValueError, match="name"):
            PreparerPlugin(name, fleet, "fleet/1")
    for revision in ("", " ", None):
        with pytest.raises(ValueError, match="revision"):
            PreparerPlugin("fleet", fleet, revision)


def test_discovery_is_sorted_and_installed_provider_replaces_whole_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(preparation_api, "BUILTINS", {"fleet": "must.not.import:plugin"})
    install(
        monkeypatch,
        entry("z-last", PreparerPlugin("z-last", fleet, "1"), calls),
        entry("fleet", fleet_plugin, calls),
        entry("a-first", lambda: PreparerPlugin("a-first", fleet, "1"), calls),
    )
    plugins = discover_preparers()
    assert [item.name for item in plugins] == ["a-first", "fleet", "z-last"]
    assert calls == ["a-first", "fleet", "z-last"]
    assert plugins[1].revision == "fleet/1"
    assert plugins[1].validate_config is validate_fleet_config


def test_duplicate_installed_names_fail_before_any_load_or_builtin_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(preparation_api, "BUILTINS", {"a-builtin": "must.not.import:plugin"})
    install(monkeypatch, entry("fleet", fleet_plugin, calls), entry("fleet", fleet_plugin, calls))
    with pytest.raises(ValueError, match="Duplicate.*fleet"):
        discover_preparers()
    assert calls == []


@pytest.mark.parametrize("name", ("Bad.Name", "", "MixedCase", "with space", "-leading"))
def test_malformed_installed_name_fails_before_any_load_or_factory(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    loads: list[str] = []
    factories: list[str] = []
    imports: list[str] = []

    def factory() -> PreparerPlugin:
        factories.append("factory")
        return PreparerPlugin("0-first", fleet, "1")

    def import_builtin(module: str) -> Any:
        imports.append(module)
        pytest.fail("malformed declarations must fail before builtin imports")

    monkeypatch.setattr(preparation_api, "BUILTINS", {"a-builtin": "must.not.import:plugin"})
    monkeypatch.setattr(preparation_api, "import_module", import_builtin)
    install(monkeypatch, entry("0-first", factory, loads), entry(name, factory, loads))
    with pytest.raises(ValueError, match="name must match"):
        discover_preparers()
    assert loads == []
    assert factories == []
    assert imports == []


@pytest.mark.parametrize("loaded,error,match", [
    (None, TypeError, "PreparerPlugin"),
    (lambda: None, TypeError, "PreparerPlugin"),
    (lambda required: None, TypeError, "zero-argument"),
    (PreparerPlugin("different", fleet, "1"), ValueError, "name.*match"),
])
def test_invalid_installed_provider_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, loaded: Any, error: type[Exception], match: str,
) -> None:
    monkeypatch.setattr(preparation_api, "BUILTINS", {"fleet": "must.not.import:plugin"})
    install(monkeypatch, entry("fleet", loaded))
    with pytest.raises(error, match=match):
        discover_preparers()


@pytest.mark.parametrize("fault", [
    "return", "seed", "format", "manifest", "mutation", "collision", "entity",
    "frame", "binding", "registry",
])
def test_preparer_output_is_admitted_fail_closed(monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    def invalid(doc: dict[str, Any], seed: int, resources: ReadOnlyResources) -> Any:
        if fault == "return":
            return doc
        result = copy.deepcopy(doc)
        selections: tuple[dict[str, Any], ...] = ()
        manifest = resource_manifest(resources)
        if fault == "seed":
            result["run"]["seed"] = seed + 1
        elif fault == "format":
            result["format"] = "aeroagentsim.scenario/v1"
        elif fault == "manifest":
            manifest = {}
        elif fault == "mutation":
            doc["id"] = "mutated-input"
        elif fault == "collision":
            selections = ({"family": "aeroagentsim.preparers", "name": "fleet"},)
        elif fault == "entity":
            result["entities"] = [{"id": "bad", "type": "not:Registered", "facts": {}}]
        elif fault == "frame":
            result["presentation"] = [{
                "typeId": "example:Unit", "positionField": "example.position",
                "frame": "ned", "visual": {"kind": "marker"},
            }]
        elif fault == "binding":
            result["bindings"]["rules"][0]["overwrite"] = True
        elif fault == "registry":
            result["registry"]["types"].append(copy.deepcopy(result["registry"]["types"][0]))
        return PreparedScenario(result, selections, manifest)

    install(monkeypatch, entry("fleet", PreparerPlugin(
        "fleet", invalid, "bad/1", validate_config=validate_fleet_config,
    )))
    with pytest.raises(ScenarioError):
        load_scenario(document(), final_seed=0)
