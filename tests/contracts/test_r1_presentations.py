"""R1 presentation admission, external read projection and honest control readback.

These contracts use detached JSON/registry fixtures, with no live model provider,
worker or native radio backend. HTTP coverage only reads static completed runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.messages import Emit, Message
from aerokernel.registry import FieldDescriptor, MemoryRegistry, MessageDescriptor, TypeDescriptor
from aerokernel.time import Instant

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


@pytest.fixture(autouse=True)
def _isolate_installed_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    _entry_points(monkeypatch)


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


def test_normalized_selector_references_use_declared_output_schema() -> None:
    recipe = _simple_recipe("reading")
    plugin = PresentationPlugin("external", _data(narratives={"external.raw": recipe}),
                                normalizers=(_NestedNormalizer(),))
    assert plugin.normalizers[0].output_schemas["external.raw"]["members"]["reading"] == STRING

    class BadOutput(_NestedNormalizer):
        output_schemas = {"external.raw": _record_schema({"different": STRING})}

    with pytest.raises(ValueError, match="unknown referenced field path"):
        PresentationPlugin("external", _data(narratives={"external.raw": recipe}), normalizers=(BadOutput(),))


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


# Focused regressions for legacy copies and non-narrative provider changes.


@pytest.mark.parametrize("state,en,zh", [
    ("blocked", "Road traffic conflict detected", "道路交通冲突出现"),
    ("cleared", "Road traffic conflict cleared", "道路交通冲突解除"),
])
def test_copied_legacy_traffic_declarations_do_not_suppress_neutral_road_cause(
    tmp_path: Path, state: str, en: str, zh: str,
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
    scenario_path = directory / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario["event_narratives"] = copy.deepcopy(EVENT_NARRATIVES)
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    projector = NarrativeProjector.from_directory(directory, plugins=(plugin(),))
    row = projector.consume(_encoded_event("traffic.road." + state, {"actor": "sensor-1"}))[0]
    assert row["text"] == en and row["text_zh"] == zh
    assert "accident" not in row["text"].lower() and "事故" not in row["text_zh"]
    assert row["entities"] == ["sensor-1"] and "position" not in row


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
        # Match the existing feed cache's declaration serialization, without
        # constructing its database, journals, KPIs or signature dependencies.
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
    assert read_presentation_snapshot(directory) == snapshot
    selected, read_snapshot = presentation_selection(directory, plugins=available if explicit_available else None)
    assert [plugin.name for plugin in selected] == ["external"] and read_snapshot == snapshot
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
