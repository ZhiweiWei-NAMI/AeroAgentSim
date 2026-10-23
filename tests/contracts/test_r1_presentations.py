"""R1 presentation admission, external read projection and honest control readback.

These contracts use detached JSON/registry fixtures, with no live model provider,
worker or native radio backend. HTTP coverage only reads static completed runs.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, MessageDescriptor, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp

import aeroagentsim.presentation_api as presentation_api
from aeroagentsim.presentation_api import (
    PRESENTATION_FORMAT,
    ControlRegistry,
    Known,
    PresentationPlugin,
    Unavailable,
    discover_presentation_plugins,
    read_controls,
    resolve_poses,
    validate_presentation,
)
from aeroagentsim.presentation_controls import known
from aeroagentsim.services.narratives import NarrativeProjector

EVENT = "external.notice"
STRING = {"type": "string"}
VECTOR = {"type": "vector", "length": 3, "items": {"type": "number"}}


def _data(**sections: Any) -> dict[str, Any]:
    return {"format": PRESENTATION_FORMAT, **sections}


def _record_schema(members: dict[str, Any]) -> dict[str, Any]:
    return {"type": "record", "members": members, "required": [], "extra": False}


def _simple_recipe(name: str = "value") -> dict[str, Any]:
    return {
        "template": "Value: {" + name + "}", "template_zh": "值：{" + name + "}",
        "category": "mixed", "severity": "info",
    }


def _recipe() -> dict[str, Any]:
    return {
        "template": "{actor} reported {state}", "template_zh": "{actor}报告{state}",
        "category": "inspection", "severity": "notice", "entities": ["actor"],
        "position": "location",
        "selectors": {
            "actor": {"path": ["actor"], "label": True},
            "state": {"path": ["details", "state"], "enum": {
                "blocked": {"en": "blocked", "zh": "阻塞"},
            }},
            "location": {"path": ["where"]},
        },
    }


def _pose(**changes: Any) -> dict[str, Any]:
    return {
        "typeId": "external:Sensor", "positionField": "external.position",
        "orientationField": "external.orientation", "frame": "enu",
        "visual": {"kind": "marker", "color": "#336699"}, **changes,
    }


def _registry(
    *, position_schema: dict[str, Any] | None = None,
    orientation_schema: dict[str, Any] | None = None,
    position_type: str = "external:Sensor", unit: Any = "m", frame: Any = "enu",
) -> MemoryRegistry:
    return MemoryRegistry(
        (TypeDescriptor("external:Sensor"), TypeDescriptor("external:Other")),
        (
            FieldDescriptor("external.position", position_type, position_schema or VECTOR,
                            {"unit": unit, "frame": frame, "role": "state"}),
            FieldDescriptor("external.orientation", "external:Sensor", orientation_schema or {
                "type": "vector", "length": 4, "items": {"type": "number"},
            }, {"frame": "enu", "role": "state"}),
        ),
        (
            MessageDescriptor(EVENT, "event", _record_schema({
                "actor": STRING, "details": _record_schema({"state": STRING}),
                "where": VECTOR, "note": STRING, "amount": {"type": "number"},
            })),
            MessageDescriptor("external.raw", "event", _record_schema({
                "envelope": _record_schema({"reading": STRING}),
            })),
        ),
    )


def _entry_point(name: str, loaded: Any) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=lambda: loaded)


def _entry_points(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.presentations"
        return entries

    monkeypatch.setattr(presentation_api, "entry_points", query)
    presentation_api.clear_presentation_plugin_cache()


@pytest.fixture(autouse=True)
def _isolate_installed_providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _entry_points(monkeypatch)
    try:
        yield
    finally:
        presentation_api.clear_presentation_plugin_cache()


def _external_provider(monkeypatch: pytest.MonkeyPatch, plugin: PresentationPlugin) -> None:
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, _entry_point(plugin.name, plugin))


def _pinned_projection(tmp_path: Path) -> Path:
    (tmp_path / "runtime.registry.json").write_text(json.dumps(_registry().to_data()), encoding="utf-8")
    (tmp_path / "scenario.json").write_text(json.dumps({
        "id": "external-world", "entities": [{
            "id": "sensor-1", "type": "external:Sensor",
            "label": "Remote sensor", "label_zh": "远端传感器",
        }],
    }), encoding="utf-8")
    return tmp_path


def _encoded_event(schema: str, payload: dict[str, Any], sequence: int = 1) -> dict[str, Any]:
    message = Message(
        id=f"{schema}/{sequence}", kind="event", schema_id=schema,
        source_kind="partition", source="external-owner", sequence=sequence,
        at=Instant(sequence), available=Instant(sequence), payload=payload, causes=(),
    )
    encoded = encode({
        "index": sequence, "instant": Instant(sequence), "batches": [], "items": [{
            "kind": "operation", "partition": "external-owner",
            "proposal": Emit("event", schema, "feed", message.at, message.payload),
            "message": message,
        }],
    })
    assert isinstance(encoded, dict)
    return encoded


def _read_effective(document: Mapping[str, Any]) -> Known:
    value = document["parameters"]["effective"]
    return known(value, {"pointer": "/parameters/effective", "value": value})


# Effective controls are read evidence, not an alternate authoring execution path.


def test_apply_only_control_without_reader_never_uses_declared_default() -> None:
    declaration = {"id": "mode", "apply": "external.apply", "default": "invented"}
    registry = ControlRegistry(controls=(declaration,), readers={})
    result = read_controls({}, registry)["mode"]
    assert isinstance(result, Unavailable)
    assert result.reason
    assert declaration["default"] == "invented"


def test_pointers_preserve_null_zero_false_and_escaped_members() -> None:
    document = {"a/b": {"~key": [None, 0, False]}}
    registry = ControlRegistry(controls=tuple({
        "id": identifier, "pointer": f"/a~1b/~0key/{index}", "default": "not-evidence",
    } for index, identifier in enumerate(("null", "zero", "false"))))
    before = copy.deepcopy(document)
    result = read_controls(document, registry)
    for identifier, expected in (("null", None), ("zero", 0), ("false", False)):
        observation = result[identifier]
        assert isinstance(observation, Known)
        assert observation.value == expected and type(observation.value) is type(expected)
        assert len(observation.evidence_digest) == 64
    assert document == before
    assert read_controls(document, registry) == result


@pytest.mark.parametrize("pointer", ["value", "#/value", "/bad~", "/bad~2", None, 123])
def test_invalid_pointer_syntax_fails_registry_admission(pointer: Any) -> None:
    with pytest.raises(ValueError, match="pointer"):
        ControlRegistry(controls=({"id": "value", "pointer": pointer},))


@pytest.mark.parametrize("pointer", ["/missing", "/rows/2", "/rows/-1", "/rows/-", "/rows/01"])
def test_missing_or_non_read_array_indices_are_unavailable(pointer: str) -> None:
    registry = ControlRegistry(controls=({"id": "value", "pointer": pointer, "default": 99},))
    observation = read_controls({"rows": [0, False]}, registry)["value"]
    assert isinstance(observation, Unavailable)
    assert observation.reason


def test_control_registry_snapshots_declarations_and_adapter_input_is_read_only() -> None:
    controls = [{"id": "effective", "apply": "external.read", "options": [0, 1], "default": 1}]
    calls = []

    def reader(document: Mapping[str, Any]) -> Known:
        calls.append(document)
        assert isinstance(document, Mapping)
        with pytest.raises(TypeError):
            document["parameters"]["effective"] = 99
        with pytest.raises(TypeError):
            document["rows"][0] = 99
        return _read_effective(document)

    readers = {"external.read": reader}
    registry = ControlRegistry(controls=tuple(controls), readers=readers)
    controls[0]["apply"] = "changed"
    controls[0]["options"].append(2)
    readers.clear()
    document = {"parameters": {"effective": 0}, "rows": [False]}
    before = copy.deepcopy(document)
    result = read_controls(document, registry)
    assert result["effective"].value == 0
    assert registry.controls[0]["apply"] == "external.read"
    assert registry.controls[0]["options"] == (0, 1)
    assert len(calls) == 1 and document == before
    with pytest.raises(TypeError):
        registry.controls[0]["default"] = 9
    with pytest.raises(TypeError):
        registry.readers["other"] = reader
    for instance, attribute in ((registry, "controls"), (result["effective"], "value"),
                                (Unavailable("missing"), "reason")):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, attribute, None)


def test_known_hashes_canonical_evidence_and_does_not_coerce_json_values() -> None:
    first = known(False, {"z": False, "a": 1})
    second = known(False, {"a": 1, "z": False})
    assert first == second
    assert first.value is False
    assert first.evidence_digest == hashlib.sha256(b'{"a":1,"z":false}').hexdigest()
    assert known(False, {"a": 2, "z": False}).evidence_digest != first.evidence_digest


def test_async_control_readers_are_rejected_without_invocation() -> None:
    async def reader(document: Mapping[str, Any]) -> Known:
        raise AssertionError("async adapter must never run")

    with pytest.raises(TypeError, match="synchronous"):
        ControlRegistry(readers={"external.read": reader})


@pytest.mark.parametrize("observation", [None, {"status": "known", "value": 1}, Known(1, "not-a-digest"), Unavailable("")])
def test_malformed_reader_results_fail_instead_of_becoming_missing_evidence(observation: Any) -> None:
    registry = ControlRegistry(controls=({"id": "value", "apply": "external.read"},),
                               readers={"external.read": lambda document: observation})
    with pytest.raises((TypeError, ValueError), match="control reader"):
        read_controls({}, registry)


def test_live_objects_are_rejected_before_the_adapter_receives_input() -> None:
    calls = []

    def reader(document: Mapping[str, Any]) -> Known:
        calls.append(document)
        return known(1, {"source": "recorded"})

    registry = ControlRegistry(controls=({"id": "value", "apply": "external.read"},),
                               readers={"external.read": reader})
    with pytest.raises(TypeError, match="JSON"):
        read_controls({"live_object": object()}, registry)
    assert calls == []


def _rescue_document() -> dict[str, Any]:
    return {"run": {"seed": 7}, "engines": {
        "tasks": {"plugin": "lowalt_tasks", "config": {"rescue": {
            "participation": {"mode": "distance", "per_role": 1},
            "observation": "negative", "deadline_s": 180.0, "fleet_variation": "seeded",
            "radio": {"target": "mission-radio", "port": 19004, "event_topic": "rescue.radio"},
            "vehicles": {"U1": {"position": [10, 20, 130], "capabilities": ["inspect"]}},
        }}},
        "mission-radio": {"plugin": "ns3", "config": {"port": 23001, "event_topic": "rescue.radio"}},
        "rescue_transport": {"plugin": "langgraph", "config": {
            "factory": "aeroagentsim.packs.lowalt.rescue_transport:relay_graph",
            "options": {"target": "mission-radio", "event_topic": "rescue.radio"},
            "triggers": [
                {"schema": "rescue.transport.send", "topic": "rescue.transport"},
                {"schema": "adapters.ns3.delivery", "topic": "rescue.radio"},
                {"schema": "adapters.ns3.drop", "topic": "rescue.radio"},
            ],
            "grants": {
                "commands": [{"schema": "adapters.ns3.send", "target": "mission-radio"}],
                "events": [{"schema": "rescue.transport." + name, "topic": "rescue.results"}
                           for name in ("delivered", "failed", "hop")],
            },
        }},
    }}


def _rescue_registry() -> ControlRegistry:
    from aeroagentsim.packs.lowalt.presentation_controls import CONTROL_READERS

    return ControlRegistry(controls=tuple({
        "id": name, "apply": f"rescue.{name}", "default": "never-use",
    } for name in ("participation", "observation", "radio", "radio_port", "deadline_s", "fleet_variation")),
        readers=CONTROL_READERS)


def test_builtin_rescue_readers_use_expanded_pinned_sources_without_apply_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.packs.lowalt import rescue_hooks

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("readback must not replay an apply hook")

    for name in ("participation", "observation", "radio_port", "radio", "deadline", "fleet_variation"):
        monkeypatch.setattr(rescue_hooks, "_rescue_" + name, forbidden)
    document = _rescue_document()
    before = copy.deepcopy(document)
    result = read_controls(document, _rescue_registry())
    assert all(isinstance(observation, Known) for observation in result.values())
    assert {name: observation.value for name, observation in result.items()} == {
        "participation": "distance", "observation": "negative", "radio": "on",
        "radio_port": 23001, "deadline_s": 180.0, "fleet_variation": "seeded",
    }
    assert document == before


def test_rescue_radio_off_requires_an_explicit_owner_and_has_no_effective_port() -> None:
    document = _rescue_document()
    del document["engines"]["tasks"]["config"]["rescue"]["radio"]
    result = read_controls(document, _rescue_registry())
    assert isinstance(result["radio"], Known) and result["radio"].value == "off"
    assert isinstance(result["radio_port"], Unavailable)
    del document["engines"]["tasks"]["config"]["rescue"]
    result = read_controls(document, _rescue_registry())
    assert all(isinstance(observation, Unavailable) for observation in result.values())


@pytest.mark.parametrize("field", ["participation", "fleet_variation", "deadline_s", "observation"])
def test_rescue_missing_sources_never_become_runtime_or_template_defaults(field: str) -> None:
    document = _rescue_document()
    del document["engines"]["tasks"]["config"]["rescue"][field]
    assert isinstance(read_controls(document, _rescue_registry())[field], Unavailable)


def test_rescue_compound_sources_are_unavailable_when_ambiguous_or_disconnected() -> None:
    document = _rescue_document()
    document["engines"]["other-tasks"] = copy.deepcopy(document["engines"]["tasks"])
    assert all(isinstance(value, Unavailable) for value in read_controls(document, _rescue_registry()).values())
    document = _rescue_document()
    document["engines"]["mission-radio"]["plugin"] = "different-backend"
    result = read_controls(document, _rescue_registry())
    assert isinstance(result["radio"], Unavailable)
    assert isinstance(result["radio_port"], Unavailable)


@pytest.mark.parametrize("change", [
    "missing-relay", "rebound-options", "rebound-command", "missing-command",
    "wrong-send-topic", "missing-native-trigger", "wrong-native-topic",
    "missing-result-grant", "wrong-result-topic", "duplicate-relay",
    "duplicate-trigger", "duplicate-command", "unsupported-factory",
    "changed-native-topic", "missing-native-topic", "malformed-trigger", "malformed-binding",
])
def test_rescue_radio_and_port_are_unavailable_for_unproven_relay_chains(change: str) -> None:
    document = _rescue_document()
    engines = document["engines"]
    relay = engines["rescue_transport"]["config"]
    engines["other-radio"] = {"plugin": "ns3", "config": {"port": 24002, "event_topic": "other.radio"}}
    if change == "missing-relay":
        del engines["rescue_transport"]
    elif change == "rebound-options":
        relay["options"]["target"] = "other-radio"
    elif change == "rebound-command":
        relay["grants"]["commands"][0]["target"] = "other-radio"
    elif change == "missing-command":
        relay["grants"]["commands"] = []
    elif change == "wrong-send-topic":
        relay["triggers"][0]["topic"] = "other.transport"
    elif change == "missing-native-trigger":
        relay["triggers"].pop()
    elif change == "wrong-native-topic":
        relay["triggers"][1]["topic"] = "other.radio"
    elif change == "missing-result-grant":
        relay["grants"]["events"].pop()
    elif change == "wrong-result-topic":
        relay["grants"]["events"][0]["topic"] = "other.results"
    elif change == "duplicate-relay":
        engines["duplicate-relay"] = copy.deepcopy(engines["rescue_transport"])
    elif change == "duplicate-trigger":
        relay["triggers"].append(copy.deepcopy(relay["triggers"][0]))
    elif change == "duplicate-command":
        relay["grants"]["commands"].append(copy.deepcopy(relay["grants"]["commands"][0]))
    elif change == "unsupported-factory":
        relay["factory"] = "external.pack:uninterpreted_relay"
    elif change == "changed-native-topic":
        engines["mission-radio"]["config"]["event_topic"] = "other.radio"
    elif change == "missing-native-topic":
        del engines["mission-radio"]["config"]["event_topic"]
    elif change == "malformed-binding":
        relay["grants"]["commands"][0]["unsupported"] = True
    else:
        relay["triggers"].append("not-a-trigger")
    result = read_controls(document, _rescue_registry())
    for identifier in ("radio", "radio_port"):
        assert isinstance(result[identifier], Unavailable)
        assert result[identifier].reason


def test_rescue_radio_proves_the_actual_relay_binding_not_its_historical_engine_name() -> None:
    document = _rescue_document()
    registry = _rescue_registry()
    original = read_controls(document, registry)
    document["engines"]["renamed-relay"] = document["engines"].pop("rescue_transport")
    result = read_controls(document, registry)
    assert isinstance(result["radio"], Known) and result["radio"].value == "on"
    assert isinstance(result["radio_port"], Known) and result["radio_port"].value == 23001
    assert result["radio"].evidence_digest != original["radio"].evidence_digest
    assert result["radio_port"].evidence_digest != original["radio_port"].evidence_digest


def test_builtin_plugins_publish_pack_owned_control_readers() -> None:
    providers = {plugin.name: plugin for plugin in discover_presentation_plugins()}
    assert "lowalt.operator_mode" in providers["lowalt"].control_readers
    assert {"rescue." + name for name in (
        "participation", "observation", "radio_port", "radio", "deadline_s", "fleet_variation",
    )} <= set(providers["lowalt"].control_readers)
    assert {"traffic." + name for name in (
        "accident_time", "decision_mode", "physics_hz", "wind_east", "wind_north",
    )} <= set(providers["traffic_accident"].control_readers)


def _traffic_document() -> dict[str, Any]:
    return {"engines": {
        "road_motion": {"plugin": "traffic_road_motion", "config": {"step_ns": 1_000_000_000}},
        "air_motion": {"plugin": "kinematic", "config": {
            "step_ns": 1_000_000_000,
            "consumption_model": {"plugin": "wind", "config": {
                "entity": "wind.region", "field": "oo:digital_twin.wind.horizontalVelocity", "vector_index": 0,
            }},
        }},
        "weather": {"plugin": "environment", "config": {"profiles": {
            "wind.region": {"oo:digital_twin.wind.horizontalVelocity": {"mode": "calm", "value": [[0.0, 0.0]]}},
        }}},
    }}


def _traffic_registry() -> ControlRegistry:
    from aeroagentsim.packs.traffic_accident.presentation_controls import CONTROL_READERS

    return ControlRegistry(controls=tuple({
        "id": name, "apply": "traffic." + name, "default": "not-evidence",
    } for name in ("physics_hz", "wind_east", "wind_north")), readers=CONTROL_READERS)


@pytest.mark.parametrize("matrix", [False, True])
def test_gust_profile_baseline_is_not_a_known_constant_wind_control(matrix: bool) -> None:
    document = _traffic_document()
    profile = document["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    gust = [6.0, -4.0]
    if not matrix:
        del document["engines"]["air_motion"]["config"]["consumption_model"]["config"]["vector_index"]
        profile["value"] = [0.0, 0.0]
    profile.update(mode="gust", gusts=[{
        "start_ns": 0, "end_ns": 10_000_000_000, "value": [gust] if matrix else gust,
    }])
    before = copy.deepcopy(document)
    result = read_controls(document, _traffic_registry())
    for identifier in ("wind_east", "wind_north"):
        assert isinstance(result[identifier], Unavailable)
        assert "constant" in result[identifier].reason
    assert document == before


@pytest.mark.parametrize("mode,vector", [("calm", [0.0, 0.0]), ("constant", [6.0, -4.0])])
def test_supported_constant_wind_controls_keep_explicit_zero_and_signed_components(mode: str, vector: list[float]) -> None:
    document = _traffic_document()
    profile = document["engines"]["weather"]["config"]["profiles"]["wind.region"]["oo:digital_twin.wind.horizontalVelocity"]
    profile.update(mode=mode, value=[vector])
    result = read_controls(document, _traffic_registry())
    for identifier, expected in zip(("wind_east", "wind_north"), vector):
        assert isinstance(result[identifier], Known) and result[identifier].value == expected


@pytest.mark.parametrize("role,replacement", [
    ("road_motion", "custom_road"), ("air_motion", "custom_air"),
    ("road_motion", "kinematic"), ("air_motion", "traffic_road_motion"),
    ("road_motion", None), ("air_motion", None),
])
def test_physics_frequency_is_unavailable_for_unproven_motion_consumers(role: str, replacement: Any) -> None:
    document = _traffic_document()
    document["engines"][role]["plugin"] = replacement
    before = copy.deepcopy(document)
    # Equal, positive config members do not establish that replacement engines
    # consume those settings, even if another supported plugin name is reused.
    result = read_controls(document, _traffic_registry())["physics_hz"]
    assert isinstance(result, Unavailable) and "consumer" in result.reason
    assert document == before


def test_known_physics_frequency_binds_both_motion_consumer_identities_to_evidence() -> None:
    document = _traffic_document()
    result = read_controls(document, _traffic_registry())["physics_hz"]
    assert isinstance(result, Known) and result.value == 1.0
    evidence = {
        "reader": "traffic.physics_hz", "road_step_ns": 1_000_000_000, "air_step_ns": 1_000_000_000,
        "consumers": {"road_motion": "traffic_road_motion", "air_motion": "kinematic"},
    }
    assert result == known(1.0, evidence)
    for role in evidence["consumers"]:
        changed = copy.deepcopy(evidence)
        changed["consumers"][role] = "different-consumer"
        assert result.evidence_digest != known(1.0, changed).evidence_digest


# Provider discovery is strict, deterministic and whole-provider replacement.


def test_duplicate_installed_names_fail_before_any_provider_load(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = []

    def load() -> Any:
        loaded.append("called")
        raise AssertionError("duplicate detection must precede every load")

    monkeypatch.setattr(presentation_api, "BUILTINS", {"builtin": load})
    entries = (SimpleNamespace(name="duplicate", load=load), SimpleNamespace(name="duplicate", load=load))
    _entry_points(monkeypatch, *entries)
    with pytest.raises(ValueError, match="Duplicate.*entry point"):
        discover_presentation_plugins()
    assert loaded == []


@pytest.mark.parametrize("names", [None, (), ("a-first",)])
@pytest.mark.parametrize("invalid_source", ["installed", "builtin"])
def test_all_declared_names_are_admitted_before_any_load_even_when_unselected(
    monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...] | None, invalid_source: str,
) -> None:
    loaded = []

    def forbidden() -> Any:
        loaded.append("called")
        raise AssertionError("every declared name must be admitted before any load")

    builtins = {"a-first": forbidden}
    entries = [SimpleNamespace(name="selected", load=forbidden)]
    if invalid_source == "installed":
        entries.append(SimpleNamespace(name="Bad.Name", load=forbidden))
    else:
        builtins["Bad.Name"] = forbidden
    monkeypatch.setattr(presentation_api, "BUILTINS", builtins)
    _entry_points(monkeypatch, *entries)
    with pytest.raises(ValueError, match="name must match"):
        discover_presentation_plugins(names=names)
    assert loaded == []


@pytest.mark.parametrize("names", [None, ()])
def test_shared_discovery_rejects_duplicates_before_malformed_names(
    monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...] | None,
) -> None:
    from aeroagentsim.extensions.discovery import DuplicateEntryPointError

    def forbidden() -> Any:
        raise AssertionError("neither malformed nor duplicate declarations may load")

    monkeypatch.setattr(presentation_api, "BUILTINS", {"also.invalid": forbidden})
    _entry_points(monkeypatch, SimpleNamespace(name="Bad.Name", load=forbidden),
                  SimpleNamespace(name="Bad.Name", load=forbidden))
    with pytest.raises(DuplicateEntryPointError, match="Duplicate.*Bad.Name"):
        discover_presentation_plugins(names=names)


@pytest.mark.parametrize("names", [(), ("selected",)])
def test_selection_never_loads_unselected_installed_or_builtin_factories(
    monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...],
) -> None:
    loaded = []

    def forbidden() -> Any:
        raise AssertionError("unselected entries and builtin factories must remain unloaded")

    def selected_factory() -> PresentationPlugin:
        loaded.append("factory")
        return PresentationPlugin("selected", _data())

    def selected_load() -> Any:
        loaded.append("load")
        return selected_factory

    monkeypatch.setattr(presentation_api, "BUILTINS", {"unused-builtin": forbidden})
    _entry_points(monkeypatch, SimpleNamespace(name="selected", load=selected_load),
                  SimpleNamespace(name="unused-installed", load=forbidden))
    selected = discover_presentation_plugins(names=names)
    assert tuple(plugin.name for plugin in selected) == names
    assert loaded == (["load", "factory"] if names else [])


def test_installed_override_replaces_the_whole_builtin_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    old = PresentationPlugin("shared", _data(labels={"old": {"en": "Old", "zh": "旧"}}),
                             control_readers={"old.read": _read_effective})
    replacement = PresentationPlugin("shared", _data(labels={"new": {"en": "New", "zh": "新"}}))

    def builtin() -> PresentationPlugin:
        called.append("builtin")
        return old

    monkeypatch.setattr(presentation_api, "BUILTINS", {"shared": builtin})
    _entry_points(monkeypatch, _entry_point("shared", lambda: replacement))
    discovered = discover_presentation_plugins()
    assert discovered == (replacement,)
    assert called == []
    assert "old" not in discovered[0].data["labels"]
    assert discovered[0].control_readers == {}


def test_discovery_reuses_metadata_and_providers_until_explicit_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PresentationPlugin("external", _data(), revision="1")
    calls = {"queries": 0, "loads": 0}

    def load() -> PresentationPlugin:
        calls["loads"] += 1
        return provider

    def query(*, group: str) -> tuple[Any, ...]:
        assert group == presentation_api.PRESENTATION_ENTRY_POINT_GROUP
        calls["queries"] += 1
        return (SimpleNamespace(name="external", load=load),)

    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    monkeypatch.setattr(presentation_api, "entry_points", query)
    presentation_api.clear_presentation_plugin_cache()
    assert discover_presentation_plugins(names=()) == ()
    assert calls == {"queries": 1, "loads": 0}
    first, = discover_presentation_plugins(names=("external",))
    provider = replace(provider, revision="2")
    # Different selections share both admitted metadata and frozen providers.
    for names in (None, ("external",), ()):
        selected = discover_presentation_plugins(names=names)
        if selected:
            assert selected[0] is first and selected[0].revision == "1"
    assert calls == {"queries": 1, "loads": 1}
    with pytest.raises(TypeError):
        first.data["format"] = "changed"

    presentation_api.clear_presentation_plugin_cache()
    refreshed, = discover_presentation_plugins()
    assert refreshed.revision == "2" and first.revision == "1"
    assert calls == {"queries": 2, "loads": 2}


def test_discovery_returns_sorted_builtin_installed_union(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation_api, "BUILTINS", {"middle": lambda: PresentationPlugin("middle", _data())})
    _entry_points(monkeypatch, _entry_point("z-last", PresentationPlugin("z-last", _data())),
                  _entry_point("a-first", PresentationPlugin("a-first", _data())))
    assert [plugin.name for plugin in discover_presentation_plugins()] == ["a-first", "middle", "z-last"]


@pytest.mark.parametrize("version", [True, False, 0, 2, 1.0, "1", None])
def test_provider_version_is_literal_integer_one(version: Any) -> None:
    assert type(presentation_api.PRESENTATION_API_VERSION) is int
    assert presentation_api.PRESENTATION_API_VERSION == 1
    with pytest.raises(ValueError, match="API version"):
        PresentationPlugin("external", _data(), api_version=version)


def test_unknown_presentation_data_version_is_not_silently_reinterpreted() -> None:
    with pytest.raises(ValueError, match="format"):
        PresentationPlugin("external", {"format": "aeroagentsim.presentation/v2"})


@pytest.mark.parametrize("name", ["", "Uppercase", "with space", "a/b", True, None])
def test_provider_name_is_strict(name: Any) -> None:
    with pytest.raises(ValueError, match="name"):
        PresentationPlugin(name, _data())


@pytest.mark.parametrize("wrong", [None, object(), {"name": "external"}, lambda: object()])
def test_discovery_rejects_wrong_provider_types(monkeypatch: pytest.MonkeyPatch, wrong: Any) -> None:
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, _entry_point("external", wrong))
    with pytest.raises(TypeError, match="PresentationPlugin"):
        discover_presentation_plugins()


def test_discovery_rejects_entrypoint_descriptor_name_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, _entry_point("external", PresentationPlugin("different", _data())))
    with pytest.raises(ValueError, match="does not match"):
        discover_presentation_plugins()


class _EmptyNormalizer:
    id = "shared-normalizer"
    version = "1"
    input_schemas: tuple[str, ...] = ()
    fact_fields: tuple[str, ...] = ()
    output_schemas: Mapping[str, Any] = {}

    def initial(self) -> dict[str, Any]:
        return {}

    def consume(self, state: Any, record: Mapping[str, Any]) -> None:
        return None


@pytest.mark.parametrize("kind", ["narratives", "labels", "metrics", "poses", "control_readers", "normalizers"])
def test_contribution_id_conflicts_are_not_merged(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    data = _data()
    options: dict[str, Any] = {}
    if kind == "narratives":
        data[kind] = {EVENT: _simple_recipe()}
    elif kind == "labels":
        data[kind] = {"shared": {"en": "Shared", "zh": "共有"}}
    elif kind == "metrics":
        data[kind] = {"shared": {"unit": "s"}}
    elif kind == "poses":
        data[kind] = [_pose()]
    elif kind == "control_readers":
        options[kind] = {"shared.read": _read_effective}
    else:
        options[kind] = (_EmptyNormalizer(),)
    first = PresentationPlugin("first", data, **options)
    second = PresentationPlugin("second", data, **options)
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, _entry_point("first", first), _entry_point("second", second))
    with pytest.raises(ValueError, match="Conflicting presentation id"):
        discover_presentation_plugins()


# Frozen declarative data admits finite selectors, not browser code or expressions.


def test_header_validator_belongs_to_the_contract_and_has_no_interpreter_dependency() -> None:
    import aeroagentsim.presentations as interpreter

    assert interpreter.validate_header_hints is presentation_api.validate_header_hints
    assert presentation_api.validate_header_hints.__module__ == "aeroagentsim.presentation_api"
    contract = ast.parse(Path(presentation_api.__file__).read_text(encoding="utf-8"))
    runtime_modules = {"presentations", "presentation_resources",
                       "aeroagentsim.presentations", "aeroagentsim.presentation_resources"}
    for node in ast.walk(contract):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in runtime_modules
        elif isinstance(node, ast.Import):
            assert all(alias.name not in runtime_modules for alias in node.names)
    header = {
        "engine_gusts": [{"plugin": "environment", "fields": ["external.wind"]}],
        "lighting": [{"type_id": "external:Environment", "field_id": "external.light", "values": ["day"]}],
        "scenes": [{"when": {"city": "external-city"}, "resource": "legacy-huangpu/v1",
                    "visuals": {"external:Sensor": {"model": "sensor"}}}],
    }
    provider = PresentationPlugin("external", _data(header=header))
    presentation_api.validate_header_hints(provider.data["header"])
    presentation_api.validate_presentation_snapshot(presentation_api.presentation_snapshot((provider,)))


@pytest.mark.parametrize("header", [
    None, [], {"engine_gusts": "not-a-list"},
    {"lighting": [{"type_id": "external:Environment", "field_id": "external.light", "values": []}]},
    {"scenes": [{"when": {"city": "external"}, "resource": "unregistered", "visuals": {}}]},
])
def test_header_admission_still_rejects_malformed_data_after_validator_move(header: Any) -> None:
    with pytest.raises(ValueError, match="presentation.header"):
        presentation_api.validate_header_hints(header)
    with pytest.raises(ValueError, match="presentation.header"):
        PresentationPlugin("external", _data(header=header))


@pytest.mark.parametrize("legacy_variants", [False, "alternate", ["missing"], ["alternate", "alternate"], [None]])
def test_legacy_variant_policy_only_admits_unique_declared_variant_names(legacy_variants: Any) -> None:
    recipe = _simple_recipe()
    recipe.update(variants={"alternate": {"template": "Alternate {value}", "template_zh": "另一值：{value}"}},
                  legacy_variants=legacy_variants)
    with pytest.raises(ValueError, match="legacy_variants"):
        PresentationPlugin("external", _data(narratives={EVENT: recipe}))


def test_plugin_deeply_snapshots_and_freezes_data_and_readers() -> None:
    data = _data(narratives={EVENT: _recipe()})
    readers = {"external.read": _read_effective}
    plugin = PresentationPlugin("external", data, control_readers=readers)
    data["narratives"][EVENT]["selectors"]["state"]["path"].append("changed")
    readers.clear()
    assert plugin.data["narratives"][EVENT]["selectors"]["state"]["path"] == ("details", "state")
    assert plugin.control_readers["external.read"] is _read_effective
    with pytest.raises(FrozenInstanceError):
        plugin.name = "changed"
    with pytest.raises(TypeError):
        plugin.data["narratives"][EVENT]["template"] = "changed"
    with pytest.raises(TypeError):
        plugin.data["narratives"][EVENT]["selectors"]["state"]["path"][0] = "changed"


@pytest.mark.parametrize("path", [[], ["*"], [".."], [True], [-1], [4096], ["a"] * 9])
def test_selectors_reject_unbounded_or_nonliteral_paths(path: Any) -> None:
    recipe = _recipe()
    recipe["selectors"]["state"]["path"] = path
    with pytest.raises(ValueError, match="bounded literal field path"):
        PresentationPlugin("external", _data(narratives={EVENT: recipe}))


def test_bilingual_placeholders_must_match() -> None:
    recipe = _recipe()
    recipe["template_zh"] = "{actor}报告{different}"
    with pytest.raises(ValueError, match="bilingual placeholders"):
        PresentationPlugin("external", _data(narratives={EVENT: recipe}))


@pytest.mark.parametrize("placeholder", ["{actor.__class__}", "{actor[0]}", "{actor!r}", "{actor:03}", "{f()}"])
def test_templates_do_not_admit_attribute_index_conversion_or_expression_evaluation(placeholder: str) -> None:
    recipe = _simple_recipe()
    recipe.update(template=placeholder, template_zh=placeholder)
    with pytest.raises(ValueError, match="invalid placeholder"):
        PresentationPlugin("external", _data(narratives={EVENT: recipe}))


@pytest.mark.parametrize("key", ["html", "javascript", "renderer", "expr"])
@pytest.mark.parametrize("location", ["provider", "narrative", "selector"])
def test_executable_rendering_keys_are_not_a_schema_path(key: str, location: str) -> None:
    data = _data(narratives={EVENT: _recipe()})
    target = data if location == "provider" else data["narratives"][EVENT]
    if location == "selector":
        target = target["selectors"]["state"]
    target[key] = "arbitrary executable rendering is forbidden"
    with pytest.raises(ValueError, match="unknown"):
        PresentationPlugin("external", data)


@pytest.mark.parametrize("case", ["missing", "label-number", "enum-vector", "entity-number", "position-number", "vector-index"])
def test_referenced_message_fields_and_types_are_validated(case: str) -> None:
    recipe = _recipe()
    if case == "missing":
        recipe["selectors"]["state"]["path"] = ["details", "missing"]
    elif case == "label-number":
        recipe["selectors"]["actor"]["path"] = ["amount"]
    elif case == "enum-vector":
        recipe["selectors"]["state"]["path"] = ["where"]
    elif case == "entity-number":
        recipe["selectors"]["actor"] = {"path": ["amount"]}
    elif case == "position-number":
        recipe["selectors"]["location"]["path"] = ["amount"]
    else:
        recipe["selectors"]["state"] = {"path": ["where", 3]}
    with pytest.raises(ValueError, match="field path|label requires|enum requires|entity requires|position requires"):
        validate_presentation(_data(narratives={EVENT: recipe}), _registry())


def test_pose_validates_type_applicability_vector_shapes_frame_and_unit() -> None:
    poses, diagnostics = resolve_poses(_data(poses=[_pose()]), _registry())
    assert poses == [_pose()] and diagnostics == []


@pytest.mark.parametrize("changes", [
    {"position_schema": STRING},
    {"position_schema": {"type": "vector", "length": 2, "items": {"type": "number"}}},
    {"orientation_schema": VECTOR},
    {"position_type": "external:Other"},
    {"frame": {"convention": "ned"}},
    {"unit": {"symbol": "km"}},
])
def test_malformed_declared_pose_bindings_fail(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="vector required|frame mismatch|metre unit"):
        resolve_poses(_data(poses=[_pose()]), _registry(**changes))


def test_missing_optional_pose_uses_generic_labelled_diagnostic_not_fabricated_geometry() -> None:
    data = _data(poses=[_pose(optional=True)])
    poses, diagnostics = resolve_poses(data, MemoryRegistry(()))
    assert poses == [] and len(diagnostics) == 1
    assert "generic labelled" in diagnostics[0].lower()
    with pytest.raises(ValueError, match="unknown type or field"):
        resolve_poses(_data(poses=[_pose()]), MemoryRegistry(()))


@pytest.mark.parametrize("visual", [
    {"kind": "model", "asset": "https://example.invalid/remote.js"},
    {"kind": "javascript"}, {"kind": "marker", "html": "<script>bad()</script>"},
])
def test_poses_do_not_admit_remote_renderers_or_html(visual: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="visual|asset"):
        PresentationPlugin("external", _data(poses=[_pose(visual=visual)]))


class _NestedNormalizer:
    id = "external.flatten"
    version = "1"
    input_schemas = ("external.raw",)
    fact_fields: tuple[str, ...] = ()
    output_schemas = {"external.raw": _record_schema({"reading": STRING})}

    def initial(self) -> dict[str, Any]:
        return {}

    def consume(self, state: Any, record: Mapping[str, Any]) -> dict[str, Any]:
        with pytest.raises(TypeError):
            record["payload"]["envelope"]["reading"] = "changed"
        return {"payload": {"reading": record["payload"]["envelope"]["reading"]}}


@pytest.mark.parametrize("legacy", [False, True])
def test_only_explicit_legacy_variants_can_replace_authored_templates(legacy: bool) -> None:
    class VariantNormalizer(_NestedNormalizer):
        def consume(self, state: Any, record: Mapping[str, Any]) -> dict[str, Any]:
            return {"payload": {"reading": record["payload"]["envelope"]["reading"]}, "variant": "alternate"}

    recipe = _simple_recipe("reading")
    recipe["variants"] = {"alternate": {"template": "Alternate {reading}", "template_zh": "另一值：{reading}"}}
    if legacy:
        recipe["legacy_variants"] = ["alternate"]
    provider = PresentationPlugin("external", _data(narratives={"external.raw": recipe}),
                                  normalizers=(VariantNormalizer(),))
    override = {"template": "Authored {reading}", "template_zh": "作者值：{reading}",
                "category": "logistics", "severity": "critical"}
    projector = NarrativeProjector({"external.raw": override}, plugins=(provider,))
    row = projector.consume(_encoded_event("external.raw", {"envelope": {"reading": "measured"}}))[0]
    assert row["text"] == ("Alternate measured" if legacy else "Authored measured")
    assert row["text_zh"] == ("另一值：measured" if legacy else "作者值：measured")
    assert row["category"] == "logistics" and row["severity"] == "critical"


def test_normalized_selector_references_use_declared_output_schema() -> None:
    recipe = _simple_recipe("reading")
    plugin = PresentationPlugin("external", _data(narratives={"external.raw": recipe}),
                                normalizers=(_NestedNormalizer(),))
    assert plugin.normalizers[0].output_schemas["external.raw"]["members"]["reading"] == STRING

    class BadOutput(_NestedNormalizer):
        output_schemas = {"external.raw": _record_schema({"different": STRING})}

    with pytest.raises(ValueError, match="unknown referenced field path"):
        PresentationPlugin("external", _data(narratives={"external.raw": recipe}), normalizers=(BadOutput(),))


def test_normalizer_descriptors_are_detached_frozen_and_preserve_implementation() -> None:
    normalizer = _NestedNormalizer()
    normalizer.output_schemas = copy.deepcopy(normalizer.output_schemas)
    provider = PresentationPlugin("external", _data(narratives={"external.raw": _simple_recipe("reading")}),
                                  normalizers=(normalizer,))
    before = presentation_api.presentation_snapshot((provider,))
    frozen = provider.normalizers[0]
    assert frozen is not normalizer
    assert before["providers"][0]["normalizers"][0]["implementation"] == (
        f"{type(normalizer).__module__}:{type(normalizer).__qualname__}"
    )
    normalizer.id = "changed"
    normalizer.version = "2"
    normalizer.input_schemas = ()
    normalizer.fact_fields = ("changed.field",)
    normalizer.output_schemas["external.raw"]["members"].clear()
    assert presentation_api.presentation_snapshot((provider,)) == before
    assert frozen.output_schemas["external.raw"]["members"]["reading"] == STRING
    with pytest.raises(FrozenInstanceError):
        frozen.input_schemas = ()
    with pytest.raises(TypeError):
        frozen.output_schemas["external.raw"]["members"]["reading"]["type"] = "number"
    assert presentation_api.presentation_snapshot((replace(provider, revision="2"),))["providers"][0]["normalizers"] == (
        before["providers"][0]["normalizers"]
    )
    projector = NarrativeProjector(plugins=(provider,))
    assert projector.consume(_encoded_event("external.raw", {"envelope": {"reading": "measured"}}))[0]["text"] == "Value: measured"


@pytest.mark.parametrize("mutation_timing", ["construction", "first-launch"])
def test_replaced_normalizer_schemas_are_detached_before_plugin_and_launch_admission(
    mutation_timing: str,
) -> None:
    provider = PresentationPlugin("external", _data(narratives={"external.raw": _simple_recipe("reading")}),
                                  normalizers=(_NestedNormalizer(),))
    mutable_schemas = presentation_api.json_data(provider.normalizers[0].output_schemas)
    schema = mutable_schemas["external.raw"]
    schema["required"].append("reading")
    expected_schemas = copy.deepcopy(mutable_schemas)
    frozen = replace(provider.normalizers[0], output_schemas=mutable_schemas)

    def mutate_originals() -> None:
        mutable_schemas["injected"] = _record_schema({})
        schema["members"]["reading"]["type"] = "number"
        schema["members"]["extra"] = {"type": "boolean"}
        schema["required"].append("missing")

    if mutation_timing == "construction":
        mutate_originals()
    assert presentation_api.json_data(frozen.output_schemas) == expected_schemas
    derived = replace(provider, normalizers=(frozen,))
    assert derived.normalizers[0] is frozen
    first_launch = presentation_api.presentation_snapshot((derived,), registry=_registry(), document={})
    first_digest = presentation_api.presentation_plugin_digest((derived,))
    if mutation_timing == "first-launch":
        mutate_originals()

    assert presentation_api.json_data(frozen.output_schemas) == expected_schemas
    with pytest.raises(TypeError):
        frozen.output_schemas["injected"] = {}
    with pytest.raises(TypeError):
        frozen.output_schemas["external.raw"]["members"]["reading"]["type"] = "number"
    with pytest.raises(TypeError):
        frozen.output_schemas["external.raw"]["required"][0] = "missing"
    subsequent_launch = presentation_api.presentation_snapshot((derived,), registry=_registry(), document={})
    assert subsequent_launch == first_launch
    assert presentation_api.presentation_plugin_digest((derived,)) == first_digest
    presentation_api.validate_presentation_snapshot(subsequent_launch)
    assert presentation_api.verify_presentation_snapshot(first_launch, (derived,)) == (derived,)
    projector = NarrativeProjector(plugins=(derived,))
    row = projector.consume(_encoded_event("external.raw", {"envelope": {"reading": "measured"}}))[0]
    assert row["text"] == "Value: measured"


def test_replaced_normalizer_input_containers_are_detached_and_frozen() -> None:
    provider = PresentationPlugin("external", _data(narratives={"external.raw": _simple_recipe("reading")}),
                                  normalizers=(_NestedNormalizer(),))
    input_schemas = ["external.raw"]
    fact_fields = ["external.position"]
    frozen = replace(provider.normalizers[0], input_schemas=input_schemas, fact_fields=fact_fields)
    input_schemas.clear()
    fact_fields.append("external.orientation")
    assert frozen.input_schemas == ("external.raw",)
    assert frozen.fact_fields == ("external.position",)
    with pytest.raises(TypeError):
        frozen.input_schemas[0] = "changed"
    with pytest.raises(TypeError):
        frozen.fact_fields[0] = "changed"


# External providers reach the service through discovery, without host schema switches.


@pytest.mark.parametrize("state,en,zh", [("blocked", "blocked", "阻塞"), ("novel-kind", "novel-kind", "novel-kind")])
def test_external_entrypoint_narrates_nested_fields_in_english_and_chinese(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, en: str, zh: str,
) -> None:
    plugin = PresentationPlugin("external", _data(narratives={EVENT: _recipe()}))
    _external_provider(monkeypatch, plugin)
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path))
    payload = {"actor": "sensor-1", "details": {"state": state}, "where": [1.0, 2.0, 3.0]}
    before = copy.deepcopy(payload)
    record = _encoded_event(EVENT, payload)
    rows = projector.consume(record)
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == f"Remote sensor reported {en}"
    assert row["text_zh"] == f"远端传感器报告{zh}"
    assert row["entities"] == ["sensor-1"] and row["position"] == [1.0, 2.0, 3.0]
    assert row["category"] == "inspection" and row["schema"] == EVENT
    assert payload == before and projector.consume(record) == []


def test_external_nested_normalizer_is_read_only_and_narrates_its_validated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = PresentationPlugin("external", _data(narratives={"external.raw": _simple_recipe("reading")}),
                                normalizers=(_NestedNormalizer(),))
    _external_provider(monkeypatch, plugin)
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path))
    row = projector.consume(_encoded_event("external.raw", {"envelope": {"reading": "measured"}}))[0]
    assert row["text"] == "Value: measured" and row["text_zh"] == "值：measured"


def test_absent_selector_source_is_not_filled_with_an_invented_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _external_provider(monkeypatch, PresentationPlugin("external", _data(narratives={EVENT: _recipe()})))
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path))
    with pytest.raises(ValueError, match="missing|selector|placeholder"):
        projector.consume(_encoded_event(EVENT, {"actor": "sensor-1", "details": {}, "where": [1, 2, 3]}))


def test_unknown_domain_has_no_invented_builtin_narrative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path))
    assert projector.consume(_encoded_event("unknown.event", {"kind": "fire", "incident": "unrecorded"})) == []


def test_payload_script_html_and_expression_text_stays_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "must-not-exist"
    text = f"<script>bad()</script> __import__('pathlib').Path({str(marker)!r}).write_text('bad')"
    _external_provider(monkeypatch, PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")})))
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path))
    row = projector.consume(_encoded_event(EVENT, {"note": text}))[0]
    assert row["text"] == "Value: " + text and row["text_zh"] == "值：" + text
    assert not marker.exists()


# Additive HTTP integration keeps old template fields and old knobs semantics.


def _summary_templates(monkeypatch: pytest.MonkeyPatch, *, available: bool = True) -> None:
    import aeroagentsim.authoring.templates as templates_module

    declarations = [
        {"id": "hooked", "label": "Hooked", "type": "number", "apply": "external.read", "default": 101},
        {"id": "missing", "label": "Missing", "type": "number", "apply": "external.missing", "default": 202},
        {"id": "zero", "label": "Zero", "type": "number", "pointer": "/parameters/zero", "default": 9},
    ]

    class Templates:
        def list(self) -> list[dict[str, Any]]:
            return [{"id": "external", "title": "Current title", "knobs": copy.deepcopy(declarations)}] if available else []

    monkeypatch.setattr(templates_module, "TemplateRegistry", lambda root: Templates())
    for name in ("AEROAGENTSIM_STUDIO_ROOT", "AEROAGENTSIM_API_TOKEN", "AEROAGENTSIM_CONSOLE_URL"):
        monkeypatch.delenv(name, raising=False)


def _summary_run(root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path = root / "run-a"
    path.mkdir(parents=True)
    document = {"id": "external-world", "parameters": {"effective": 7, "zero": 0}}
    metadata = {"id": "run-a", "status": "completed", "template": {
        "id": "external", "title": "Historical title", "title_zh": "历史标题",
        "knobs": {"hooked": -1, "missing": -2, "zero": 3},
    }}
    (path / "scenario.json").write_text(json.dumps(document), encoding="utf-8")
    (path / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
    return path, document, metadata


@pytest.mark.parametrize("metadata_available", [True, False])
def test_run_summary_adds_effective_controls_without_changing_historical_or_legacy_knobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata_available: bool,
) -> None:
    from fastapi.testclient import TestClient

    from aeroagentsim.services.app import create_app

    _summary_templates(monkeypatch, available=metadata_available)
    _external_provider(monkeypatch, PresentationPlugin("external", _data(), control_readers={"external.read": _read_effective}))
    root = tmp_path / "runs"
    path, document, metadata = _summary_run(root)
    with TestClient(create_app(root), base_url="http://localhost") as client:
        response = client.get("/v1/runs/run-a", params={"include_kpis": False})
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary["template"] == metadata["template"]
        assert summary["title"] == "Historical title" and summary["title_zh"] == "历史标题"
        assert summary["knobs"] == ({"zero": 0} if metadata_available else {})
        if metadata_available:
            controls = summary["effective_controls"]
            assert controls["hooked"] == {"status": "known", "value": 7,
                                          "evidence_digest": _read_effective(document).evidence_digest}
            assert controls["missing"]["status"] == "unavailable" and controls["missing"]["reason"]
            assert controls["zero"]["status"] == "known" and controls["zero"]["value"] == 0
        else:
            assert summary["effective_controls"] == {}
        listing = client.get("/v1/runs", params={"include_kpis": False})
        assert listing.status_code == 200, listing.text
        assert listing.json()[0]["effective_controls"] == summary["effective_controls"]
    assert json.loads((path / "scenario.json").read_text(encoding="utf-8")) == document
    assert json.loads((path / "manifest.json").read_text(encoding="utf-8")) == metadata


def test_app_does_not_hide_malformed_presentation_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aeroagentsim.services.app import create_app

    _summary_templates(monkeypatch)
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    plugin = PresentationPlugin("duplicate", _data())
    _entry_points(monkeypatch, _entry_point("duplicate", plugin), _entry_point("duplicate", plugin))
    with pytest.raises(ValueError, match="Duplicate.*entry point"):
        create_app(tmp_path / "runs")


@pytest.mark.parametrize("installed_override", [False, True])
def test_legacy_chinese_label_uses_public_provider_data_not_the_pack_private_helper(
    monkeypatch: pytest.MonkeyPatch, installed_override: bool,
) -> None:
    from aeroagentsim.packs.lowalt import presentation
    from aeroagentsim.services import narratives

    provider = presentation.plugin()
    called = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("service compatibility exports must not call pack-private helpers")

    def builtin() -> PresentationPlugin:
        called.append("builtin")
        return provider

    monkeypatch.setattr(presentation, "_chinese_label", forbidden)
    monkeypatch.setattr(presentation_api, "BUILTINS", {"lowalt": builtin, "traffic_accident": forbidden})
    if installed_override:
        data = presentation_api.json_data(provider.data)
        data["labels"]["Customer"]["zh"] = "客户（外部定义）"
        data["labels"]["Authored vocabulary"] = {"en": "Authored vocabulary", "zh": "公开数据词汇"}
        provider = replace(provider, data=data)

        def installed() -> PresentationPlugin:
            called.append("installed")
            return provider

        _entry_points(monkeypatch, _entry_point("lowalt", installed))
    expected = {
        "Customer": "客户（外部定义）" if installed_override else "客户",
        "Drone P2": "无人机 P2", "Survey drone 12": "巡检无人机 12",
        "Incident drone Alpha": "事故响应无人机 Alpha", "Route uav Bg 3": "无人机路线 背景机 3",
        "Road vehicle reporter": "道路车辆 报告车", "Road task vehicle incident a": "道路车辆任务 事故 a",
        "Unknown authored place": "Unknown authored place", "Drone X": "Drone X",
        "Authored vocabulary": "公开数据词汇" if installed_override else "Authored vocabulary",
    }
    narratives._legacy_label_data.cache_clear()
    try:
        projector = NarrativeProjector(plugins=(provider,))
        for label, translated in expected.items():
            assert narratives._chinese_label(label) == translated
            assert projector.label(label, "zh") == translated
        assert called == (["installed"] if installed_override else ["builtin"])
    finally:
        narratives._legacy_label_data.cache_clear()


# Focused regressions for legacy copies and non-narrative provider changes.


@pytest.mark.parametrize("state,en,zh", [
    ("blocked", "Road traffic conflict detected", "道路交通冲突出现"),
    ("cleared", "Road traffic conflict cleared", "道路交通冲突解除"),
])
@pytest.mark.parametrize("override", ["copy", "severity", "category", "custom-template", "english-only"])
@pytest.mark.parametrize("source", ["scenario", "behaviour"])
@pytest.mark.parametrize("cause", ["occupancy", "incident", "accident-reason", "traffic-incident-reason"])
def test_legacy_road_overrides_preserve_main_evidence_dependent_wording_and_style(
    tmp_path: Path, state: str, en: str, zh: str, override: str, source: str, cause: str,
) -> None:
    from aeroagentsim.packs.traffic_accident.narratives import EVENT_NARRATIVES
    from aeroagentsim.packs.traffic_accident.presentation import plugin

    directory = _pinned_projection(tmp_path)
    registry = _registry()
    registry = MemoryRegistry(registry.types, registry.fields, registry.messages + tuple(
        MessageDescriptor("traffic.road." + name, "event", _record_schema({
            "actor": STRING, "incident": STRING, "reason": STRING,
        })) for name in ("blocked", "cleared")
    ))
    (directory / "runtime.registry.json").write_text(json.dumps(registry.to_data()), encoding="utf-8")
    schema = "traffic.road." + state
    declarations = copy.deepcopy(EVENT_NARRATIVES)
    declaration = declarations[schema]
    if override == "severity":
        declaration["severity"] = "critical"
    elif override == "category":
        declaration["category"] = "logistics"
    elif override == "custom-template":
        declaration.update(template="Authored road event for {actor}", template_zh="作者道路事件：{actor}")
    elif override == "english-only":
        declaration.pop("template_zh")
    scenario_path = directory / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    if source == "scenario":
        scenario["event_narratives"] = declarations
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    else:
        (directory / "behaviour.ir.json").write_text(json.dumps({
            "packages": [{"document": {"event_narratives": declarations}}],
        }), encoding="utf-8")
    projector = NarrativeProjector.from_directory(directory, plugins=(plugin(),))
    actor = EntityRef("external-run", "epoch-0", "sensor-1", 0, "external:Sensor")
    assert projector.consume_decoded({"items": [{"proposal": FactWrite(
        (actor, "external.position"), [1.0, 2.0, 3.0],
        Stamp("canonical", 0, 1, "canonical"), Interval(Instant(0), None),
    )}]}) == []
    payload = {"actor": "sensor-1"}
    if cause == "incident":
        payload["incident"] = "incident-1"
    elif cause in {"accident-reason", "traffic-incident-reason"}:
        payload["reason"] = "accident" if cause == "accident-reason" else "traffic incident"
    row = projector.consume(_encoded_event(schema, payload))[0]

    # Golden viewer-feed parity with origin/main: cause controls prose/position,
    # while the authored declaration retains category, severity and entity links.
    expected = {
        "message_id": schema + "/1", "schema": schema, "at_ns": "1",
        "category": declaration["category"], "severity": declaration["severity"],
        "entities": ["incident-1", "sensor-1"] if cause == "incident" else ["sensor-1"],
    }
    if cause == "occupancy":
        expected.update(text=en, text_zh=zh)
        assert "accident" not in row["text"].lower() and "事故" not in row["text_zh"]
    else:
        expected["text"] = declaration["template"].format(actor="Remote sensor")
        if "template_zh" in declaration:
            expected["text_zh"] = declaration["template_zh"].format(actor="远端传感器")
    if cause != "incident":
        expected["position"] = [1.0, 2.0, 3.0]
    assert row == expected


def test_feed_signature_fingerprints_provider_and_authored_data_without_a_projector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.services.feed_index import _signature

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("signatures must not initialize a narrative projector")

    monkeypatch.setattr(NarrativeProjector, "__init__", forbidden)
    directory = _pinned_projection(tmp_path)
    provider = PresentationPlugin("external", _data(labels={"Sensor": {"en": "Sensor", "zh": "传感器"}}))
    baseline = _signature(directory, plugins=(provider,))
    with monkeypatch.context() as warm:
        def forbidden_digest(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("unchanged legacy provider fingerprints must be cached too")

        warm.setattr(presentation_api, "presentation_digest", forbidden_digest)
        assert _signature(directory, plugins=(provider,)) == baseline
    assert _signature(directory, plugins=(replace(provider, revision="2"),)) != baseline
    changed = replace(provider, data=_data(labels={"Sensor": {"en": "Sensor", "zh": "新标签"}}))
    assert _signature(directory, plugins=(changed,)) != baseline
    scenario_path = directory / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["event_narratives"] = {EVENT: _simple_recipe("note")}
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    authored = _signature(directory, plugins=(provider,))
    assert authored != baseline
    (directory / "behaviour.ir.json").write_text(json.dumps({
        "packages": [{"document": {"event_narratives": {"external.raw": _simple_recipe("reading")}}}],
    }), encoding="utf-8")
    assert _signature(directory, plugins=(provider,)) != authored


def test_feed_signature_hashes_equal_length_label_edits_without_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.services.feed_index import _signature

    directory = _pinned_projection(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("signature invalidation must not initialize a narrative projector")

    monkeypatch.setattr(NarrativeProjector, "__init__", forbidden)
    baseline = _signature(directory, plugins=())
    path = directory / "scenario.json"
    stat = path.stat()
    raw = path.read_bytes()
    edited = raw.replace(b"Remote sensor", b"Nearby sensor")
    assert edited != raw and len(edited) == len(raw)
    path.write_bytes(edited)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    assert _signature(directory, plugins=()) != baseline


def test_narrative_projectors_share_frozen_recipes_but_not_override_tables() -> None:
    provider = PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")}))
    first = NarrativeProjector(plugins=(provider,))
    second = NarrativeProjector(plugins=(provider,))
    recipe = provider.data["narratives"][EVENT]
    assert first._declarations[EVENT] is recipe
    assert second._declarations[EVENT] is recipe
    first._override({EVENT: presentation_api.json_data(recipe)})
    assert first._declarations[EVENT] is not recipe
    assert first._declarations[EVENT]["_legacy_bindings"] is True
    assert "_legacy_bindings" not in recipe
    assert second._declarations[EVENT] is recipe


@pytest.mark.parametrize("change", ["revision", "labels"])
def test_provider_without_narratives_invalidates_serialized_cache_and_cursor(change: str) -> None:
    original_data = _data(labels={"Remote sensor": {"en": "Remote sensor", "zh": "远端传感器"}})
    changed_data = copy.deepcopy(original_data)
    if change == "labels":
        changed_data["labels"]["Remote sensor"]["zh"] = "修订传感器"
    original = PresentationPlugin("external", original_data, revision="1")
    changed = PresentationPlugin("external", changed_data, revision="2" if change == "revision" else "1")
    assert "narratives" not in original.data and "narratives" not in changed.data
    first = NarrativeProjector(plugins=(original,))
    second = NarrativeProjector(plugins=(changed,))

    def fingerprint(projector: NarrativeProjector) -> str:
        # Retain compatibility for callers fingerprinting the private mapping;
        # the feed index now fingerprints provider DATA and source files directly.
        encoded = json.dumps(projector._declarations, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    assert fingerprint(first) != fingerprint(second)
    assert first.contract_digest != second.contract_digest
    cursor = json.loads(json.dumps(first.cursor()))
    NarrativeProjector(plugins=(original,)).restore_cursor(cursor)
    with pytest.raises(ValueError, match="presentation.*changed|rebuild"):
        second.restore_cursor(cursor)


def test_callable_instance_control_reader_can_be_snapshotted_and_projected(tmp_path: Path) -> None:
    class Reader:
        def __call__(self, document: Mapping[str, Any]) -> Known:
            return _read_effective(document)

    reader = Reader()
    provider = PresentationPlugin("external", _data(), control_readers={"external.read": reader})
    snapshot = presentation_api.presentation_snapshot((provider,))
    implementation = snapshot["providers"][0]["control_readers"]["external.read"]
    assert implementation == f"{type(reader).__module__}:{type(reader).__qualname__}"
    assert json.loads(json.dumps(snapshot))["digest"] == snapshot["digest"]
    registry = ControlRegistry(controls=({"id": "value", "apply": "external.read"},),
                               readers=provider.control_readers)
    result = read_controls({"parameters": {"effective": 7}}, registry)
    assert isinstance(result["value"], Known) and result["value"].value == 7
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path), plugins=(provider,))
    assert isinstance(projector.contract_digest, str) and projector.contract_digest


def test_async_call_method_is_rejected_by_plugin_and_control_registry_without_invocation() -> None:
    calls = []

    class AsyncReader:
        async def __call__(self, document: Mapping[str, Any]) -> Known:
            calls.append(document)
            return known(1, {"source": "must-not-run"})

    reader = AsyncReader()
    with pytest.raises(ValueError, match="synchronous"):
        PresentationPlugin("external", _data(), control_readers={"external.read": reader})
    with pytest.raises(TypeError, match="synchronous"):
        ControlRegistry(readers={"external.read": reader})
    assert calls == []


def test_optional_missing_orientation_does_not_hide_malformed_existing_position() -> None:
    registry = _registry(position_schema=STRING)
    registry = MemoryRegistry(registry.types, tuple(
        field for field in registry.fields if field.id != "external.orientation"
    ), registry.messages)
    with pytest.raises(ValueError, match="positionField.*3-vector"):
        resolve_poses(_data(poses=[_pose(optional=True)]), registry)


def test_raw_task_destination_matching_builtin_vocabulary_stays_literal_without_authored_label(
    tmp_path: Path,
) -> None:
    from aeroagentsim.packs.lowalt.presentation import plugin

    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path), plugins=(plugin(),))
    row = projector.consume(_encoded_event("lowalt.task.created", {
        "task": "task-1", "kind": "parcel", "priority": 1,
        "origin": "depot-1", "destination": "Customer",
    }))[0]
    assert row["text"] == "New parcel order for Customer"
    assert row["text_zh"] == "新增前往Customer的包裹配送任务"
    assert "客户" not in row["text_zh"]


def test_numeric_enum_lookup_uses_exact_recorded_json_number_without_display_rounding(tmp_path: Path) -> None:
    recipe = _simple_recipe()
    recipe["selectors"] = {"value": {"path": ["amount"], "enum": {
        "1.25": {"en": "precise", "zh": "精确"},
        "1.2": {"en": "rounded", "zh": "已舍入"},
    }}}
    provider = PresentationPlugin("external", _data(narratives={EVENT: recipe}))
    projector = NarrativeProjector.from_directory(_pinned_projection(tmp_path), plugins=(provider,))
    row = projector.consume(_encoded_event(EVENT, {"amount": 1.25}))[0]
    assert row["text"] == "Value: precise" and row["text_zh"] == "值：精确"
    assert "rounded" not in row["text"] and "舍入" not in row["text_zh"]


@pytest.mark.parametrize("authored", [False, True])
def test_header_pose_contributions_are_additive_and_missing_optional_poses_diagnose(
    tmp_path: Path, authored: bool,
) -> None:
    from aeroagentsim.presentations import apply_header_presentations

    contributed = _pose()
    missing = {
        "typeId": "external:Unavailable", "positionField": "external.absent",
        "frame": "enu", "visual": {"kind": "label"}, "optional": True,
    }
    metrics = {"external.delay": {"label": {"en": "Delay", "zh": "延迟"}, "unit": "s", "decimals": 2}}
    provider = PresentationPlugin("external", _data(poses=[contributed, missing], metrics=metrics))
    authored_pose = _pose(visual={"kind": "label", "color": "#ffff00"})
    result = {"presentation": [authored_pose] if authored else [], "engines": [], "types": [], "fields": []}
    original = copy.deepcopy(result)
    apply_header_presentations(result, {"id": "external-world", "engines": {}}, tmp_path,
                               plugins=(provider,), registry=_registry())
    assert result["presentation"] == (original["presentation"] if authored else [contributed])
    for key in ("engines", "types", "fields"):
        assert result[key] == original[key]
    hints = result["presentationHints"]
    assert set(hints) == {"format", "digest", "poses", "metrics", "diagnostics"}
    assert hints["format"] == PRESENTATION_FORMAT
    assert hints["digest"] == presentation_api.presentation_snapshot((provider,))["digest"]
    assert hints["poses"] == [contributed] and hints["metrics"] == metrics
    assert len(hints["diagnostics"]) == 1 and "generic labelled" in hints["diagnostics"][0].lower()


# Family snapshots pin display meaning without activating later installations.


def _write_presentation_pin(
    directory: Path, plugins: tuple[PresentationPlugin, ...], *,
    controls: tuple[Mapping[str, Any], ...] | None = None,
) -> dict[str, Any]:
    snapshot = presentation_api.presentation_snapshot(plugins, controls=controls)
    (directory / "presentation.snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return snapshot


def test_discovery_attaches_frozen_observed_source_without_mutating_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = PresentationPlugin("external", _data())
    entry = SimpleNamespace(
        name="external", value="external_pack.presentation:plugin",
        dist=SimpleNamespace(name="external-pack", version="1.0"), load=lambda: original,
    )
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, entry)
    discovered = discover_presentation_plugins()[0]
    assert discovered == original and original.source == {}
    assert discovered.source == {
        "kind": "installed", "entry_point": "external_pack.presentation:plugin",
        "distribution": ("external-pack", "1.0"),
    }
    with pytest.raises(TypeError):
        discovered.source["kind"] = "builtin"
    with pytest.raises(TypeError):
        discovered.source["distribution"][0] = "changed"
    snapshot = presentation_api.presentation_snapshot((discovered,))
    assert snapshot["providers"][0]["source"]["distribution"] == ["external-pack", "1.0"]


def test_launch_snapshot_admits_run_bindings_without_recompiling_normalized_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = PresentationPlugin("external", _data(poses=[_pose()]), normalizers=(_EmptyNormalizer(),))
    registry = _registry()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("normalized schema admission belongs to provider construction")

    monkeypatch.setattr(presentation_api, "_normalized_registry", forbidden)
    snapshot = presentation_api.presentation_snapshot((provider,), registry=registry, document={})
    assert snapshot["providers"][0]["name"] == "external"
    missing_field = PresentationPlugin("missing", _data(poses=[_pose(positionField="external.missing")]))
    with pytest.raises(ValueError, match="unknown type or field"):
        presentation_api.presentation_snapshot((missing_field,), registry=registry, document={})
    wgs84_pose = {key: value for key, value in _pose(frame="wgs84").items() if key != "orientationField"}
    wgs84 = PresentationPlugin("wgs84", _data(poses=[wgs84_pose]))
    with pytest.raises(ValueError, match="explicit origin required"):
        presentation_api.presentation_snapshot((wgs84,), registry=_registry(frame="wgs84"), document={})


def test_presentation_snapshot_has_independent_version_and_detached_control_declarations() -> None:
    provider = PresentationPlugin("external", _data(), control_readers={"external.read": _read_effective})
    controls = ({"id": "actual", "pointer": "/parameters/effective", "default": 99},)
    snapshot = presentation_api.presentation_snapshot((provider,), controls=controls)
    assert presentation_api.PRESENTATION_SNAPSHOT_FORMAT == "aeroagentsim.presentation-snapshot/v1"
    assert snapshot["format"] == presentation_api.PRESENTATION_SNAPSHOT_FORMAT
    assert snapshot["providers"][0]["source"] == {}
    assert snapshot["controls"] == list(controls)
    presentation_api.validate_presentation_snapshot(snapshot)
    controls[0]["pointer"] = "/changed"
    assert snapshot["controls"][0]["pointer"] == "/parameters/effective"
    assert presentation_api.verify_presentation_snapshot(snapshot, (provider,)) == (provider,)


@pytest.mark.parametrize("tampering", ["digest", "data", "source", "controls"])
def test_presentation_snapshot_detects_digest_tampering(tampering: str) -> None:
    provider = PresentationPlugin("external", _data(labels={"Sensor": {"en": "Sensor", "zh": "传感器"}}))
    snapshot = presentation_api.presentation_snapshot((provider,), controls=({"id": "x", "pointer": "/x"},))
    if tampering == "digest":
        snapshot["digest"] = "sha256:" + "0" * 64
    elif tampering == "data":
        snapshot["providers"][0]["data"]["labels"]["Sensor"]["zh"] = "篡改"
    elif tampering == "source":
        snapshot["providers"][0]["source"]["entry_point"] = "changed.module:plugin"
    else:
        snapshot["controls"][0]["pointer"] = "/changed"
    with pytest.raises(ValueError, match="digest"):
        presentation_api.validate_presentation_snapshot(snapshot)
    with pytest.raises(ValueError, match="digest"):
        presentation_api.verify_presentation_snapshot(snapshot, (provider,))


def test_presentation_snapshot_missing_provider_is_not_replaced_with_an_unrelated_installation() -> None:
    selected = PresentationPlugin("selected", _data())
    snapshot = presentation_api.presentation_snapshot((selected,))
    extra = PresentationPlugin("extra", _data())
    with pytest.raises(ValueError, match="missing.*selected"):
        presentation_api.verify_presentation_snapshot(snapshot, (extra,))


@pytest.mark.parametrize("changed_field", ["revision", "data", "source"])
def test_presentation_snapshot_rejects_changed_provider_meaning_or_observed_source(changed_field: str) -> None:
    source = {"kind": "installed", "entry_point": "external_pack.presentation:plugin",
              "distribution": ["external-pack", "1.0"]}
    original = PresentationPlugin("external", _data(labels={"Sensor": {"en": "Sensor", "zh": "传感器"}}),
                                  revision="1", source=source)
    snapshot = presentation_api.presentation_snapshot((original,))
    if changed_field == "revision":
        changed = replace(original, revision="2")
    elif changed_field == "data":
        changed = replace(original, data=_data(labels={"Sensor": {"en": "Sensor", "zh": "新含义"}}))
    else:
        changed_source = copy.deepcopy(source)
        changed_source["distribution"][1] = "2.0"
        changed = replace(original, source=changed_source)
        # source is observed metadata excluded from dataclass equality; snapshot
        # verification must still compare it, not just descriptor equality.
        assert changed == original
    presentation_api.validate_presentation_snapshot(snapshot)
    with pytest.raises(ValueError, match="source/revision/DATA changed|unavailable"):
        presentation_api.verify_presentation_snapshot(snapshot, (changed,))


def test_presentation_snapshot_selects_only_pinned_names_in_the_pinned_order() -> None:
    first = PresentationPlugin("first", _data())
    second = PresentationPlugin("second", _data())
    extra = PresentationPlugin("extra", _data(narratives={"extra.event": _simple_recipe("note")}))
    snapshot = presentation_api.presentation_snapshot((second, first))
    selected = presentation_api.verify_presentation_snapshot(snapshot, (extra, first, second))
    assert [plugin.name for plugin in selected] == ["second", "first"]
    assert all(plugin.name != "extra" for plugin in selected)


def test_legacy_directory_without_presentation_snapshot_still_discovers_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data())
    _external_provider(monkeypatch, provider)
    assert read_presentation_snapshot(tmp_path) is None
    selected, snapshot = presentation_selection(tmp_path)
    assert [plugin.name for plugin in selected] == ["external"] and snapshot is None


@pytest.mark.parametrize("explicit_available", [False, True])
def test_from_directory_uses_pinned_selection_and_does_not_activate_extra_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_available: bool,
) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")}))
    extra = PresentationPlugin("extra", _data(narratives={"extra.event": _simple_recipe("note")}))
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, _entry_point("extra", extra), _entry_point("external", provider))
    available = (provider, extra) if explicit_available else discover_presentation_plugins()
    pinned = next(plugin for plugin in available if plugin.name == "external")
    directory = _pinned_projection(tmp_path)
    snapshot = _write_presentation_pin(directory, (pinned,))
    assert presentation_api.json_data(read_presentation_snapshot(directory)) == snapshot
    selected, read_snapshot = presentation_selection(directory, plugins=available if explicit_available else None)
    assert [plugin.name for plugin in selected] == ["external"]
    assert presentation_api.json_data(read_snapshot) == snapshot
    projector = NarrativeProjector.from_directory(directory, plugins=available if explicit_available else None)
    baseline = NarrativeProjector.from_directory(directory, plugins=(pinned,))
    assert projector.contract_digest == baseline.contract_digest
    row = projector.consume(_encoded_event(EVENT, {"note": "pinned meaning"}))[0]
    assert row["text"] == "Value: pinned meaning" and row["text_zh"] == "值：pinned meaning"
    assert projector.consume(_encoded_event("extra.event", {"note": "must not activate"}, sequence=2)) == []


def test_pinned_from_directory_never_loads_an_extra_installed_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")}))
    entry = _entry_point("external", provider)
    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    _entry_points(monkeypatch, entry)
    observed = discover_presentation_plugins()
    directory = _pinned_projection(tmp_path)
    _write_presentation_pin(directory, observed)
    loaded = []

    def forbidden_load() -> Any:
        loaded.append("extra")
        raise AssertionError("an unselected installed provider must not load for a pinned run")

    _entry_points(monkeypatch, entry, SimpleNamespace(name="extra", load=forbidden_load))
    projector = NarrativeProjector.from_directory(directory)
    assert projector.consume(_encoded_event(EVENT, {"note": "selected"}))[0]["text"] == "Value: selected"
    assert loaded == []


def test_from_directory_rejects_changed_pinned_provider_instead_of_using_current_meaning(tmp_path: Path) -> None:
    provider = PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")}), revision="1")
    directory = _pinned_projection(tmp_path)
    _write_presentation_pin(directory, (provider,))
    with pytest.raises(ValueError, match="changed|unavailable"):
        NarrativeProjector.from_directory(directory, plugins=(replace(provider, revision="2"),))


def test_pinned_snapshot_reads_share_immutable_validation_and_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data(narratives={EVENT: _simple_recipe("note")}))
    snapshot = _write_presentation_pin(tmp_path, (provider,))
    calls = {"validate": 0, "verify": 0}
    original_validate = presentation_api.validate_presentation_snapshot
    original_verify = presentation_api.verify_presentation_snapshot

    def validate(*args: Any, **kwargs: Any) -> Any:
        calls["validate"] += 1
        return original_validate(*args, **kwargs)

    def verify(*args: Any, **kwargs: Any) -> Any:
        calls["verify"] += 1
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(presentation_api, "validate_presentation_snapshot", validate)
    monkeypatch.setattr(presentation_api, "verify_presentation_snapshot", verify)
    frozen = read_presentation_snapshot(tmp_path)
    assert presentation_api.json_data(frozen) == snapshot
    assert calls == {"validate": 1, "verify": 0}
    with pytest.raises(TypeError):
        frozen["digest"] = "changed"
    with pytest.raises(TypeError):
        frozen["providers"][0]["data"]["narratives"][EVENT]["template"] = "changed"
    with pytest.raises(TypeError):
        frozen["providers"][0] = {}
    for _ in range(3):
        selected, cached = presentation_selection(tmp_path, (provider,))
        assert selected == (provider,) and cached is frozen
        assert read_presentation_snapshot(tmp_path) is frozen
    assert calls == {"validate": 1, "verify": 1}


def test_snapshot_identity_change_reverifies_even_when_bytes_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.presentations import presentation_selection

    provider = PresentationPlugin("external", _data())
    _write_presentation_pin(tmp_path, (provider,))
    calls = []
    original = presentation_api.verify_presentation_snapshot

    def verify(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0]["digest"])
        return original(*args, **kwargs)

    monkeypatch.setattr(presentation_api, "verify_presentation_snapshot", verify)
    _, first = presentation_selection(tmp_path, (provider,))
    path = tmp_path / "presentation.snapshot.json"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    _, second = presentation_selection(tmp_path, (provider,))
    assert second is not first
    assert calls == [first["digest"], first["digest"]]
    presentation_selection(tmp_path, (provider,))
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["data", "restored_mtime", "manifest", "missing", "provider"])
def test_warm_snapshot_cache_detects_tampering_and_changed_files(
    tmp_path: Path, change: str,
) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data(labels={"Sensor": {"en": "Sensor", "zh": "传感器"}}))
    snapshot = _write_presentation_pin(tmp_path, (provider,))
    path = tmp_path / "presentation.snapshot.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"presentation_snapshot_digest": snapshot["digest"]}), encoding="utf-8")
    presentation_selection(tmp_path, (provider,))
    stat = path.stat()
    if change == "manifest":
        manifest.write_text(json.dumps({"presentation_snapshot_digest": "sha256:" + "0" * 64}), encoding="utf-8")
        reason = "digest differs from run manifest"
    elif change == "missing":
        path.unlink()
        reason = "recorded snapshot is missing"
    else:
        if change in {"data", "restored_mtime"}:
            snapshot["providers"][0]["data"]["labels"]["Sensor"]["en"] = "Broken"
            reason = "snapshot digest mismatch"
        else:
            snapshot["providers"][0]["revision"] = "2"
            snapshot["digest"] = presentation_api.presentation_digest({
                key: value for key, value in snapshot.items() if key != "digest"
            })
            manifest.write_text(json.dumps({"presentation_snapshot_digest": snapshot["digest"]}), encoding="utf-8")
            reason = "source/revision/DATA changed"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        assert path.stat().st_size == stat.st_size  # Same-size writes cannot reuse the cache.
        mtime = stat.st_mtime_ns if change == "restored_mtime" else stat.st_mtime_ns + 1_000_000_000
        os.utime(path, ns=(stat.st_atime_ns, mtime))
    for _ in range(2):  # A failed fill must not turn into a successful cache hit.
        with pytest.raises(ValueError, match=reason):
            presentation_selection(tmp_path, (provider,))
        if change != "provider":
            with pytest.raises(ValueError, match=reason):
                read_presentation_snapshot(tmp_path)


def test_warm_snapshot_cache_does_not_hide_explicit_provider_source_drift(tmp_path: Path) -> None:
    from aeroagentsim.presentations import presentation_selection

    provider = PresentationPlugin("external", _data(), source={
        "kind": "installed", "entry_point": "external:plugin", "distribution": ["external", "1"],
    })
    _write_presentation_pin(tmp_path, (provider,))
    presentation_selection(tmp_path, (provider,))
    changed = replace(provider, source={**provider.source, "distribution": ["external", "2"]})
    assert changed == provider  # Source is deliberately excluded from dataclass equality.
    with pytest.raises(ValueError, match="source/revision/DATA changed"):
        presentation_selection(tmp_path, (changed,))


def test_discovery_reset_invalidates_a_warm_pinned_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.presentations import presentation_selection

    provider = PresentationPlugin("external", _data())
    _external_provider(monkeypatch, provider)
    _write_presentation_pin(tmp_path, discover_presentation_plugins())
    presentation_selection(tmp_path)
    _external_provider(monkeypatch, replace(provider, revision="2"))
    with pytest.raises(ValueError, match="source/revision/DATA changed"):
        presentation_selection(tmp_path)


def test_concurrent_pinned_reads_verify_the_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from aeroagentsim.presentations import presentation_selection

    provider = PresentationPlugin("external", _data())
    _write_presentation_pin(tmp_path, (provider,))
    calls = []
    original = presentation_api.verify_presentation_snapshot

    def verify(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0]["digest"])
        return original(*args, **kwargs)

    monkeypatch.setattr(presentation_api, "verify_presentation_snapshot", verify)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: presentation_selection(tmp_path, (provider,)), range(8)))
    assert len(calls) == 1
    assert all(result[1] is results[0][1] for result in results)


def test_pinned_snapshot_cache_is_bounded_and_evicted_files_are_reverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aeroagentsim.presentations as presentations

    provider = PresentationPlugin("external", _data())
    monkeypatch.setattr(presentations, "_SNAPSHOT_CACHE_SIZE", 2)
    calls = []
    original = presentation_api.verify_presentation_snapshot

    def verify(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0]["digest"])
        return original(*args, **kwargs)

    monkeypatch.setattr(presentation_api, "verify_presentation_snapshot", verify)
    paths = [tmp_path / str(index) for index in range(3)]
    for path in paths:
        path.mkdir()
        _write_presentation_pin(path, (provider,))
        presentations.presentation_selection(path, (provider,))
    assert len(presentations._SNAPSHOTS) == 2 and len(calls) == 3
    presentations.presentation_selection(paths[0], (provider,))
    assert len(presentations._SNAPSHOTS) == 2 and len(calls) == 4


def test_malformed_snapshot_file_is_not_treated_as_an_unpinned_legacy_directory(tmp_path: Path) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data())
    snapshot = _write_presentation_pin(tmp_path, (provider,))
    snapshot["digest"] = "sha256:" + "0" * 64
    (tmp_path / "presentation.snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        read_presentation_snapshot(tmp_path)
    with pytest.raises(ValueError, match="digest"):
        presentation_selection(tmp_path, plugins=(provider,))


def test_manifest_presentation_marker_requires_a_snapshot_sidecar(tmp_path: Path) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    metadata = {"id": "run-a", "presentation_snapshot_digest": "sha256:" + "1" * 64}
    (tmp_path / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert not (tmp_path / "presentation.snapshot.json").exists()
    with pytest.raises(ValueError, match="recorded snapshot is missing"):
        read_presentation_snapshot(tmp_path)
    with pytest.raises(ValueError, match="recorded snapshot is missing"):
        presentation_selection(tmp_path, plugins=())


def test_manifest_presentation_marker_must_match_the_valid_snapshot_digest(tmp_path: Path) -> None:
    from aeroagentsim.presentations import presentation_selection, read_presentation_snapshot

    provider = PresentationPlugin("external", _data())
    snapshot = _write_presentation_pin(tmp_path, (provider,))
    presentation_api.validate_presentation_snapshot(snapshot)
    other_digest = "sha256:" + "0" * 64
    assert other_digest != snapshot["digest"]
    metadata = {"id": "run-a", "presentation_snapshot_digest": other_digest}
    (tmp_path / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="digest differs from run manifest"):
        read_presentation_snapshot(tmp_path)
    with pytest.raises(ValueError, match="digest differs from run manifest"):
        presentation_selection(tmp_path, plugins=(provider,))


@pytest.mark.parametrize("include_kpis", [False, True])
def test_run_summary_keeps_pinned_control_declarations_and_diagnoses_changed_reader_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_kpis: bool,
) -> None:
    from fastapi.testclient import TestClient

    import aeroagentsim.services.feed_index as feed_index
    from aeroagentsim.services.app import create_app

    def forbidden_projection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider drift must be diagnosed before any projection or cached result lookup")

    monkeypatch.setattr(feed_index, "results_state", forbidden_projection)
    monkeypatch.setattr(feed_index, "kpi_result", forbidden_projection)
    _summary_templates(monkeypatch)
    provider = PresentationPlugin("external", _data(), revision="1", control_readers={"external.read": _read_effective})
    _external_provider(monkeypatch, provider)
    observed = discover_presentation_plugins()
    root = tmp_path / "runs"
    path, _, metadata = _summary_run(root)
    controls = (
        {"id": "pinned_hook", "apply": "external.read", "default": -9},
        {"id": "pinned_pointer", "pointer": "/parameters/effective", "default": -8},
    )
    _write_presentation_pin(path, observed, controls=controls)
    _external_provider(monkeypatch, replace(provider, revision="2"))
    with TestClient(create_app(root), base_url="http://localhost") as client:
        response = client.get("/v1/runs/run-a", params={"include_kpis": include_kpis})
        assert response.status_code == 200, response.text
        summary = response.json()
    effective = summary["effective_controls"]
    assert set(effective) == {"pinned_hook", "pinned_pointer"}
    assert effective["pinned_hook"]["status"] == "unavailable" and effective["pinned_hook"]["reason"]
    assert effective["pinned_pointer"]["status"] == "known" and effective["pinned_pointer"]["value"] == 7
    assert summary["knobs"] == {"zero": 0} and summary["template"] == metadata["template"]
    assert summary["results_final"] is False and summary["results_cursor"] is None
    assert summary["results_unavailable"] == effective["pinned_hook"]["reason"]
    assert "kpis" not in summary


def test_pinned_summary_startup_and_get_do_not_load_unrelated_installed_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from aeroagentsim.services.app import create_app

    _summary_templates(monkeypatch)
    provider = PresentationPlugin("external", _data(), control_readers={"external.read": _read_effective})
    _external_provider(monkeypatch, provider)
    observed = discover_presentation_plugins()
    root = tmp_path / "runs"
    path, _, metadata = _summary_run(root)
    snapshot = _write_presentation_pin(path, observed, controls=(
        {"id": "pinned_hook", "apply": "external.read"},
    ))
    metadata["presentation_snapshot_digest"] = snapshot["digest"]
    (path / "manifest.json").write_text(json.dumps(metadata), encoding="utf-8")
    loaded = []

    def selected_load() -> PresentationPlugin:
        loaded.append("external")
        return provider

    def forbidden_load() -> Any:
        loaded.append("unrelated")
        raise AssertionError("unrelated installed factory must not load during startup or pinned GET")

    _entry_points(monkeypatch, SimpleNamespace(name="external", load=selected_load),
                  SimpleNamespace(name="unrelated", load=forbidden_load))
    app = create_app(root)
    assert loaded == []
    with TestClient(app, base_url="http://localhost") as client:
        assert loaded == []
        response = client.get("/v1/runs/run-a", params={"include_kpis": False})
        assert response.status_code == 200, response.text
    assert loaded == ["external"]
    control = response.json()["effective_controls"]["pinned_hook"]
    assert control["status"] == "known" and control["value"] == 7


@pytest.mark.parametrize("malformation,reason", [
    ("source-list", "source"), ("source-release", "source distribution"),
    ("normalizers-map", "normalizer metadata list"), ("normalizer-schema", "normalized payload schema"),
    ("readers-list", "control reader metadata"), ("reader-value", "control reader metadata"),
    ("duplicate-id", "conflicting contributed id"),
])
def test_digest_consistent_snapshot_still_rejects_malformed_nested_rows(malformation: str, reason: str) -> None:
    provider = PresentationPlugin("external", _data(narratives={"external.raw": _simple_recipe("reading")}),
                                  normalizers=(_NestedNormalizer(),), control_readers={"external.read": _read_effective})
    snapshot = presentation_api.presentation_snapshot((provider,))
    row = snapshot["providers"][0]
    if malformation == "source-list":
        row["source"] = []
    elif malformation == "source-release":
        row["source"] = {"kind": "installed", "entry_point": "external_pack:plugin", "distribution": ["only-name"]}
    elif malformation == "normalizers-map":
        row["normalizers"] = {}
    elif malformation == "normalizer-schema":
        row["normalizers"][0]["output_schemas"]["external.raw"]["members"]["reading"] = {"type": "expression"}
    elif malformation == "readers-list":
        row["control_readers"] = []
    elif malformation == "reader-value":
        row["control_readers"]["external.read"] = 42
    else:
        duplicate = copy.deepcopy(row)
        duplicate["name"] = "second"
        snapshot["providers"].append(duplicate)
    snapshot["digest"] = presentation_api.presentation_digest({
        key: value for key, value in snapshot.items() if key != "digest"
    })
    with pytest.raises(ValueError, match=reason):
        presentation_api.validate_presentation_snapshot(snapshot)
