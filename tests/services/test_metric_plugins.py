"""Metric plugin contracts and deterministic folds over synthetic recorded evidence.

No simulation, native backend, provider or installed contribution is needed. The
service adapter is exercised separately from the public accumulator/discovery API.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel.binding import BindingManifest
from aerokernel.codec import encode
from aerokernel.engine import Partition
from aerokernel.ids import ItemRef
from aerokernel.messages import CommandRequest, Emit, Message
from aerokernel.registry import MemoryRegistry, MessageDescriptor
from aerokernel.time import Instant, Stamp
from aerokernel.values import ResourceBudget, canonical_json, freeze

import aeroagentsim.metrics_api as metrics_api
from aeroagentsim.metrics_api import (
    METRICS_API_VERSION,
    MetricAccumulator,
    MetricDescriptor,
    MetricPlugin,
    discover_metric_plugins,
)
from aeroagentsim.runs import RecordedMessage, open_run
from aeroagentsim.services.kpis import METRICS, KPIProjector, project_run

SCHEMA = "external.samples.observed"
TOTAL = MetricDescriptor(
    id="external.total",
    label="Recorded sample total",
    label_zh="已记录样本总量",
    unit="count",
    direction="higher",
    aggregation="Sum the values of matching recorded event messages, once per message ID.",
    missing_data_policy="None until a matching event; a recorded zero is measured zero.",
)
ORDER = replace(
    TOTAL,
    id="external.order",
    label="Ordered sample fold",
    label_zh="按顺序累计的样本",
    direction=None,
    aggregation="Fold each value as previous * 10 + value, in journal/item order.",
)


COMMAND_SCHEMA = "external.commands.submit"
COMMAND_COUNT = replace(
    TOTAL, id="external.commands", label="Admitted external commands",
    aggregation="Count canonical command admissions/publications once per message ID.",
    missing_data_policy="None until an external command is recorded.",
)


# Frozen from f692c72f:src/aeroagentsim/services/kpis.py, not from the new
# catalogue/discovery code: mapping equality alone cannot catch order regressions.
BASELINE_KPI_IDS = (
    "tasks_created",
    "tasks_assigned",
    "tasks_completed",
    "tasks_failed",
    "completion_rate",
    "mean_task_latency_s",
    "p95_task_latency_s",
    "medical_mean_task_latency_s",
    "medical_p95_task_latency_s",
    "on_time_rate",
    "energy_used_kj",
    "distance_flown_km",
    "spraying_distance_m",
    "battery_swaps",
    "inspection_observations",
    "patrol_visits",
    "separation_conflicts",
    "geofence_violations",
    "weather_holds",
    "accident_reported",
    "award_committed",
    "captures_stored",
    "uploads_accepted",
    "incidents_occurred",
    "incidents_noticed",
    "incidents_photographed",
    "incident_response_s",
    "handoffs_completed",
    "rescue_success",
    "rescue_time_to_confirmation_s",
    "rescue_time_to_delivery_s",
    "rescue_plans",
    "rescue_bids",
    "rescue_candidates",
    "rescue_radio_packets",
    "rescue_radio_delivered",
    "rescue_radio_bytes",
    "rescue_bus_messages",
    "rescue_bus_bytes",
    "rescue_model_calls",
)


class SumReducer:
    """A third-party reducer using only the documented three required methods."""

    descriptors = (TOTAL,)
    input_schemas = ("external.samples.",)

    def initial(self) -> list[int | float]:
        return []

    def consume(self, state: list[int | float], message: RecordedMessage) -> None:
        assert isinstance(message, RecordedMessage)
        if message.kind == "event":
            state.append(message.payload["value"])

    def snapshot(self, state: list[int | float]) -> dict[str, Any]:
        return {TOTAL.id: sum(state) if state else None}


class CommandCountReducer(SumReducer):
    descriptors = (COMMAND_COUNT,)
    input_schemas = ("external.commands.",)

    def consume(self, state: list[int | float], message: RecordedMessage) -> None:
        if message.kind == "command":
            state.append(1)

    def snapshot(self, state: list[int | float]) -> dict[str, Any]:
        return {COMMAND_COUNT.id: len(state) if state else None}


class OrderReducer(SumReducer):
    descriptors = (ORDER,)

    def snapshot(self, state: list[int | float]) -> dict[str, Any]:
        if not state:
            return {ORDER.id: None}
        value = 0
        for sample in state:
            value = value * 10 + sample
        return {ORDER.id: value}


class CheckpointMixin:
    version = "samples-v1"

    def checkpoint(self, state: list[int | float]) -> list[int | float]:
        return list(state)

    def restore(self, checkpoint: list[int | float]) -> list[int | float]:
        return list(checkpoint)


class CheckpointSumReducer(CheckpointMixin, SumReducer):
    pass


class CheckpointOrderReducer(CheckpointMixin, OrderReducer):
    pass


def plugin(name: str = "external", reducer: Any = None) -> MetricPlugin:
    return MetricPlugin(name, (SumReducer() if reducer is None else reducer,))


def recorded(
    identifier: str,
    value: int | float,
    *,
    schema: str = SCHEMA,
    kind: str = "event",
    cursor: tuple[int, int] = (1, 0),
    at_ns: int = 10,
) -> RecordedMessage:
    return RecordedMessage(
        cursor=cursor,
        message_id=identifier,
        kind=kind,
        schema_id=schema,
        route="external.samples",
        topic="external.samples" if kind == "event" else None,
        target="external.samples" if kind == "command" else None,
        source_kind="partition",
        source="external-producer",
        at_ns=at_ns,
        microstep=0,
        available_ns=at_ns + 100,
        available_microstep=0,
        payload={"value": value},
        sequence=cursor[1],
    )


def emitted(message: RecordedMessage, item_kind: str | None = "operation") -> dict[str, Any]:
    at = Instant(message.at_ns, message.microstep)
    wire = Message(
        message.message_id,
        message.kind,
        message.schema_id,
        message.source_kind,
        message.source,
        message.sequence,
        at,
        Instant(message.available_ns, message.available_microstep),
        freeze(message.payload),
        (),
        source_stamp=Stamp(**message.source_stamp) if message.source_stamp is not None else None,
        origin=ItemRef(**message.origin) if message.origin is not None else None,
    )
    item = {
        "partition": message.source,
        "proposal": encode(Emit(message.kind, message.schema_id, message.route, at, message.payload)),
        "message": encode(wire),
    }
    if item_kind is not None:
        item["kind"] = item_kind
    return item


def frame(index: int, items: list[dict[str, Any]], at_ns: int = 110) -> dict[str, Any]:
    return {"kind": "commit", "index": index, "instant": encode(Instant(at_ns)), "items": items}


def admission(message: RecordedMessage, kind: str = "reservation") -> dict[str, Any]:
    return {
        "kind": kind, "message_id": message.message_id,
        "request": encode(CommandRequest(
            message.schema_id, message.target, Instant(message.at_ns, message.microstep), message.payload,
        )),
        "origin": encode(ItemRef(*message.cursor)),
        "sequence": message.sequence,
        "source_stamp": encode(Stamp(**message.source_stamp)) if message.source_stamp is not None else None,
    }


def write_reader_run(
    directory: Path, registry: MemoryRegistry, records: list[dict[str, Any]], *, cut_index: int,
) -> dict[str, Any]:
    """Write synthetic recorded evidence/ACKs, without executing a kernel or engines."""
    scenario = {"id": "metrics-reader", "registry": {}, "engines": {}}
    header = {
        "type": "header", "format": "aerokernel.journal", "major": 1, "minor": 2,
        "index": 0, "instant": encode(Instant(0)), "time_origin_ns": 0,
        "run_id": "metrics-reader", "epoch": "0", "registry": registry.to_data(),
        "registry_digest": registry.digest,
        "manifest": BindingManifest(
            "metrics-reader", "0", ingress_source="external-host", control_source="external-control",
        ).to_data(),
        "partitions": [encode(Partition("consumer", "consumer"))],
        "mappings": [], "budget": asdict(ResourceBudget()), "max_microsteps": 1024,
        "durability": "memory", "root_seed": 0, "configuration": {"scenario": scenario},
        "ingress_policy": None, "engine_profiles": {},
        "serializer": {"encoder": "stdlib-json/v1"}, "rng": {},
        "resolved_bindings": [], "engine_versions": {},
    }
    frames = [header, *({"type": "transaction", **record} for record in records)]
    lines = [canonical_json(record) for record in frames]
    (directory / "journal.jsonl").write_bytes(b"".join(lines))
    offset, index = 0, []
    for record, line in zip(frames, lines, strict=True):
        index.append({"index": record["index"], "offset": offset, "length": len(line)})
        offset += len(line)
    (directory / "index.json").write_text(json.dumps(index))
    (directory / "committed-prefix.json").write_text(json.dumps({
        "index": cut_index, "bytes": sum(len(line) for line in lines[:cut_index + 1]),
    }))
    (directory / "scenario.json").write_text(json.dumps(scenario))
    (directory / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (directory / "manifest.json").write_text(json.dumps({
        "id": directory.name, "kernel_run_id": "metrics-reader", "epoch": "0", "status": "running",
    }))
    return header


def entry_points(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.metrics"
        return entries

    monkeypatch.setattr(metrics_api, "entry_points", query)


@pytest.fixture(autouse=True)
def isolate_installed_contributions(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_points(monkeypatch)


def test_builtin_kpi_and_catalogue_keys_keep_main_order() -> None:
    projector = KPIProjector(MemoryRegistry(()))
    assert tuple(projector.snapshot()) == BASELINE_KPI_IDS
    assert tuple(projector.descriptors) == BASELINE_KPI_IDS
    assert tuple(METRICS) == tuple(METRICS.keys()) == BASELINE_KPI_IDS
    # Also pin main's empty-population/unknown distinction independently of units.
    empty_counts = {
        "tasks_created", "tasks_assigned", "tasks_completed", "tasks_failed",
        "separation_conflicts", "geofence_violations", "weather_holds",
        "accident_reported", "award_committed", "captures_stored", "uploads_accepted",
        "incidents_occurred", "incidents_noticed", "incidents_photographed", "handoffs_completed",
    }
    assert {name: row["value"] for name, row in projector.snapshot().items()} == {
        name: 0 if name in empty_counts else None for name in BASELINE_KPI_IDS
    }


def test_additional_kpis_sort_by_plugin_then_id_not_discovery_or_descriptor_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExtraReducer(SumReducer):
        def __init__(self, *ids: str) -> None:
            self.descriptors = tuple(replace(TOTAL, id=name) for name in ids)

        def snapshot(self, state: Any) -> dict[str, Any]:
            return {item.id: None for item in self.descriptors}

    alpha = MetricPlugin("alpha", (ExtraReducer("z.extra", "m.extra"),))
    zulu = MetricPlugin("zulu", (ExtraReducer("a.extra"),))
    entry_points(monkeypatch, SimpleNamespace(name="zulu", load=lambda: zulu),
                 SimpleNamespace(name="alpha", load=lambda: alpha))
    selected = discover_metric_plugins()
    expected = (*BASELINE_KPI_IDS, "m.extra", "z.extra", "a.extra")
    for selection in (selected, tuple(reversed(selected))):
        projector = KPIProjector(MemoryRegistry(()), plugins=selection)
        assert tuple(projector.snapshot()) == tuple(projector.descriptors) == expected
    assert tuple(METRICS) == tuple(METRICS.keys()) == expected


def test_public_version_descriptors_and_plugin_are_frozen() -> None:
    assert type(METRICS_API_VERSION) is int and METRICS_API_VERSION == 1
    contribution = plugin()
    assert contribution.api_version == 1
    assert contribution.reducers[0].descriptors == (TOTAL,)
    assert TOTAL.direction == "higher" and ORDER.direction is None
    for field, value in (("id", "changed"), ("label_zh", "变化"), ("unit", "m")):
        with pytest.raises(FrozenInstanceError):
            setattr(TOTAL, field, value)
    with pytest.raises(FrozenInstanceError):
        setattr(contribution, "name", "changed")


@pytest.mark.parametrize("field", ["id", "label", "label_zh", "unit", "aggregation", "missing_data_policy"])
@pytest.mark.parametrize("value", ["", " \t", None, 1])
def test_descriptor_requires_nonempty_semantic_metadata(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match=field):
        replace(TOTAL, **{field: value})


@pytest.mark.parametrize("direction", ["up", "min", "", True, 1])
def test_descriptor_rejects_unknown_direction(direction: Any) -> None:
    with pytest.raises(ValueError, match="direction"):
        replace(TOTAL, direction=direction)


@pytest.mark.parametrize("name", ["", "MixedCase", "-leading", "contains.dot", "two words", None, 1])
def test_plugin_name_is_validated(name: Any) -> None:
    with pytest.raises(ValueError, match="name"):
        plugin(name)


@pytest.mark.parametrize("version", [0, 2, True, False, 1.0, "1", None])
def test_plugin_api_rejects_boolean_float_and_unsupported_versions(version: Any) -> None:
    with pytest.raises(ValueError, match="supported version"):
        MetricPlugin("external", (SumReducer(),), api_version=version)


@pytest.mark.parametrize("changes", [
    {"descriptors": ()},
    {"descriptors": [TOTAL]},
    {"descriptors": (object(),)},
    {"input_schemas": ()},
    {"input_schemas": [SCHEMA]},
    {"input_schemas": ("",)},
    {"input_schemas": (None,)},
    {"initial": None},
    {"consume": None},
    {"snapshot": None},
    {"checkpoint": lambda state: state},
    {"restore": lambda state: state},
    {"version": ""},
    {"version": 1},
])
def test_reducer_shape_and_checkpoint_pair_are_validated(changes: dict[str, Any]) -> None:
    original = SumReducer()
    attributes = {
        name: getattr(original, name)
        for name in ("descriptors", "input_schemas", "initial", "consume", "snapshot")
    }
    attributes.update(changes)
    with pytest.raises(ValueError, match="metric reducer"):
        plugin(reducer=SimpleNamespace(**attributes))


def test_reducer_collection_must_be_a_tuple() -> None:
    with pytest.raises(ValueError, match="reducers must be a tuple"):
        MetricPlugin("external", [SumReducer()])


@pytest.mark.parametrize("inside_one_reducer", [False, True])
def test_duplicate_metric_ids_within_plugin_fail(inside_one_reducer: bool) -> None:
    first = SumReducer()
    if inside_one_reducer:
        first.descriptors = (TOTAL, TOTAL)
        reducers = (first,)
    else:
        reducers = (first, SumReducer())
    with pytest.raises(ValueError, match="Duplicate metric id.*external.total"):
        MetricPlugin("external", reducers)


def test_cross_plugin_metric_collisions_and_repeated_plugin_names_fail() -> None:
    with pytest.raises(ValueError, match="Duplicate metric id.*external.total"):
        MetricAccumulator((plugin("first"), plugin("second")))
    with pytest.raises(ValueError, match="Duplicate metric plugin name.*external"):
        MetricAccumulator((plugin(), plugin(reducer=OrderReducer())))
    with pytest.raises(TypeError, match="MetricPlugin"):
        MetricAccumulator((object(),))


def test_explicit_empty_plugins_do_not_discover_contributions(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> Any:
        pytest.fail("Explicit plugin selection triggered discovery")

    monkeypatch.setattr(metrics_api, "discover_metric_plugins", forbidden)
    accumulator = MetricAccumulator(())
    accumulator.consume(recorded("ignored", 99))
    assert accumulator.descriptors == {} and accumulator.snapshot() == {}


def test_discovery_is_sorted_and_installed_names_override_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    alpha, replacement, zulu = plugin("alpha"), plugin("middle"), plugin("zulu")
    loads: list[str] = []

    def shadowed() -> Any:
        pytest.fail("Shadowed builtin factory was called")

    def installed(name: str, value: Any) -> SimpleNamespace:
        def load() -> Any:
            loads.append(name)
            return value
        return SimpleNamespace(name=name, load=load)

    monkeypatch.setattr(metrics_api, "BUILTINS", {"middle": shadowed, "alpha": lambda: alpha})
    entry_points(monkeypatch, installed("zulu", lambda: zulu), installed("middle", replacement))
    discovered = discover_metric_plugins()
    assert isinstance(discovered, tuple)
    assert discovered == (alpha, replacement, zulu)
    assert loads == ["middle", "zulu"]


def test_duplicate_installed_names_fail_before_any_factory_or_entry_load(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []

    def load() -> MetricPlugin:
        loaded.append("loaded")
        return plugin()

    monkeypatch.setattr(metrics_api, "BUILTINS", {"aaa": load})
    entry_points(
        monkeypatch,
        SimpleNamespace(name="a-first", load=load),
        SimpleNamespace(name="external", load=load),
        SimpleNamespace(name="external", load=load),
    )
    with pytest.raises(ValueError, match="Duplicate aeroagentsim.metrics entry point name.*external"):
        discover_metric_plugins()
    assert loaded == []


@pytest.mark.parametrize("loaded", [object(), lambda: object(), lambda required: required])
def test_bad_installed_contribution_never_falls_back_to_builtin(
    monkeypatch: pytest.MonkeyPatch, loaded: Any,
) -> None:
    def fallback() -> Any:
        pytest.fail("Invalid installed entry point fell back to builtin")

    monkeypatch.setattr(metrics_api, "BUILTINS", {"external": fallback})
    entry_points(monkeypatch, SimpleNamespace(name="external", load=lambda: loaded))
    with pytest.raises(TypeError, match="MetricPlugin|zero-argument"):
        discover_metric_plugins()


@pytest.mark.parametrize("factory", [False, True])
def test_installed_entry_point_and_plugin_names_must_match(
    monkeypatch: pytest.MonkeyPatch, factory: bool,
) -> None:
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    wrong = plugin("different")
    loaded = (lambda: wrong) if factory else wrong
    entry_points(monkeypatch, SimpleNamespace(name="external", load=lambda: loaded))
    with pytest.raises(ValueError, match="plugin name.*different.*does not match"):
        discover_metric_plugins()


def test_unmeasured_is_none_and_observed_zero_is_not_missing() -> None:
    accumulator = MetricAccumulator((plugin(),))
    assert accumulator.descriptors[TOTAL.id] == TOTAL
    assert accumulator.snapshot() == {TOTAL.id: None}
    accumulator.feed([
        RecordedMessage(cursor=(1, 0), message_id="cancel", kind="cancel"),
        recorded("other", 100, schema="unrelated.samples.observed"),
        recorded("request", 100, kind="command"),
    ])
    assert accumulator.snapshot() == {TOTAL.id: None}
    accumulator.consume(recorded("zero", 0, schema="external.samples.zero"))
    assert accumulator.snapshot() == {TOTAL.id: 0}
    accumulator.consume(recorded("zero", 999))
    assert accumulator.snapshot() == {TOTAL.id: 0}
    accumulator.feed([recorded("first", 2), recorded("second", 2)])
    assert accumulator.snapshot() == {TOTAL.id: 4}


def _row(value: object) -> dict[str, object]:
    """A KPI row: the value plus the additive descriptor fields."""
    return {
        "value": value, "unit": "count", "label": TOTAL.label, "label_zh": TOTAL.label_zh,
        "direction": TOTAL.direction, "aggregation": TOTAL.aggregation,
        "missing_data_policy": TOTAL.missing_data_policy,
    }


def test_custom_schema_is_exposed_through_direct_service_adapter() -> None:
    projector = KPIProjector(MemoryRegistry(()), plugins=(plugin(),))
    assert projector.descriptors[TOTAL.id] == TOTAL
    assert projector.snapshot()[TOTAL.id] == _row(None)
    projector.consume(recorded("zero", 0))
    assert projector.snapshot()[TOTAL.id]["value"] == 0
    projector.consume(recorded("sample", 7))
    projector.consume(recorded("sample", 999))
    assert projector.snapshot()[TOTAL.id] == _row(7)


@pytest.mark.parametrize("item_kind", ["operation", None])
def test_raw_commits_and_direct_messages_share_external_reduction(item_kind: str | None) -> None:
    direct = KPIProjector(MemoryRegistry(()), plugins=(plugin(),))
    raw = KPIProjector(MemoryRegistry(()), plugins=(plugin(),))
    messages = [
        recorded("first", 2, cursor=(1, 0)),
        recorded("second", 2, cursor=(1, 1)),
        recorded("request", 50, kind="command", cursor=(2, 0)),
        recorded("other", 50, schema="unrelated.samples.observed", cursor=(2, 1)),
        recorded("third", 3, schema="external.samples.more", cursor=(2, 2)),
    ]
    before = deepcopy(messages)
    for message in messages:
        direct.consume(message)
    first = emitted(messages[0], item_kind)
    raw.consume(frame(1, [
        first,
        emitted(messages[1], item_kind),
        first,
        {"kind": "delivery", "message": first["message"]},
    ]))
    raw.consume(frame(2, [emitted(message, item_kind) for message in messages[2:]]))
    assert direct.snapshot()[TOTAL.id] == raw.snapshot()[TOTAL.id]
    assert raw.snapshot()[TOTAL.id]["value"] == 7
    assert messages == before


def test_external_raw_measurements_remain_lossless_python_integers() -> None:
    large = 2**63 + 7
    projector = KPIProjector(MemoryRegistry(()), plugins=(plugin(),))
    message = recorded("large", large, at_ns=large + 1)
    projector.consume(frame(1, [emitted(message)], at_ns=large + 101))
    value = projector.snapshot()[TOTAL.id]["value"]
    assert type(value) is int and value == large


def reducers(checkpointed: bool) -> tuple[Any, ...]:
    return ((CheckpointSumReducer(), CheckpointOrderReducer()) if checkpointed
            else (SumReducer(), OrderReducer()))


@pytest.mark.parametrize("checkpointed", [False, True], ids=["history-fallback", "custom-checkpoint"])
def test_chunking_and_every_checkpoint_split_preserve_order_and_dedup(checkpointed: bool) -> None:
    selection = (MetricPlugin("external", reducers(checkpointed)),)
    messages = [
        recorded("first", 1, cursor=(1, 0)),
        recorded("unrelated", 90, schema="unrelated.samples.observed", cursor=(1, 1)),
        recorded("second", 4, cursor=(2, 0)),
        recorded("first", 999, cursor=(2, 1)),
        recorded("command", 7, kind="command", cursor=(3, 0)),
        RecordedMessage(cursor=(3, 1), message_id="cancel", kind="cancel"),
        recorded("third", 2, cursor=(4, 0)),
    ]
    whole = MetricAccumulator(selection)
    whole.feed(messages)
    assert whole.snapshot() == {TOTAL.id: 7, ORDER.id: 142}
    expected_cursor = json.loads(json.dumps(whole.cursor()))
    checkpoint_field = "state" if checkpointed else "messages"
    assert all(checkpoint_field in row for row in expected_cursor["reducers"])

    chunked = MetricAccumulator(selection)
    for start in range(0, len(messages), 2):
        chunked.feed(messages[start:start + 2])
    assert chunked.snapshot() == whole.snapshot()
    assert json.loads(json.dumps(chunked.cursor())) == expected_cursor

    for split in range(len(messages) + 1):
        prefix = MetricAccumulator(selection)
        prefix.feed(messages[:split])
        checkpoint = json.loads(json.dumps(prefix.cursor()))
        checkpoint_before = deepcopy(checkpoint)
        resumed = MetricAccumulator(selection)
        resumed.restore_cursor(checkpoint)
        assert checkpoint == checkpoint_before
        assert resumed.snapshot() == prefix.snapshot()
        resumed.feed(messages[split:])
        resumed.feed([messages[0], messages[-1]])
        assert resumed.snapshot() == whole.snapshot(), split
        assert json.loads(json.dumps(resumed.cursor())) == expected_cursor, split


@pytest.mark.parametrize("checkpointed", [False, True], ids=["history-fallback", "custom-checkpoint"])
def test_checkpoint_and_consumed_payload_are_detached(checkpointed: bool) -> None:
    reducer = CheckpointSumReducer() if checkpointed else SumReducer()
    selection = (plugin(reducer=reducer),)
    original = recorded("one", 3)
    accumulator = MetricAccumulator(selection)
    accumulator.consume(original)
    original.payload["value"] = 99
    checkpoint = json.loads(json.dumps(accumulator.cursor()))
    restored = MetricAccumulator(selection)
    restored.restore_cursor(checkpoint)
    if checkpointed:
        checkpoint["reducers"][0]["state"][0] = 999
    else:
        checkpoint["reducers"][0]["messages"][0]["payload"]["value"] = 999
    assert accumulator.snapshot() == restored.snapshot() == {TOTAL.id: 3}


@pytest.mark.parametrize("version", [0, 2, True, False, 1.0, "1", None])
def test_cursor_version_requires_exact_supported_integer(version: Any) -> None:
    accumulator = MetricAccumulator((plugin(),))
    checkpoint = accumulator.cursor()
    checkpoint["version"] = version
    with pytest.raises(ValueError, match="version"):
        accumulator.restore_cursor(checkpoint)


def test_changed_reducer_revision_requires_rebuild() -> None:
    original = MetricAccumulator((plugin(reducer=CheckpointSumReducer()),))
    original.consume(recorded("one", 3))
    changed = CheckpointSumReducer()
    changed.version = "samples-v2"
    resumed = MetricAccumulator((plugin(reducer=changed),))
    with pytest.raises(ValueError, match="selection/version changed"):
        resumed.restore_cursor(json.loads(json.dumps(original.cursor())))
    assert resumed.snapshot() == {TOTAL.id: None}


class InvalidSnapshotReducer(SumReducer):
    def __init__(self, result: Any) -> None:
        self.result = result

    def snapshot(self, state: Any) -> Any:
        return self.result


@pytest.mark.parametrize("result", [None, [], {}, {"other": 1}, {TOTAL.id: 1, "extra": 2}])
def test_snapshot_requires_exactly_the_declared_metric_ids(result: Any) -> None:
    accumulator = MetricAccumulator((plugin(reducer=InvalidSnapshotReducer(result)),))
    with pytest.raises(ValueError, match="exactly.*declared metric ids"):
        accumulator.snapshot()


@pytest.mark.parametrize("value", [True, False, "0", [], {}, float("nan"), float("inf"), -float("inf")])
def test_snapshot_rejects_boolean_nonnumeric_and_nonfinite_measurements(value: Any) -> None:
    accumulator = MetricAccumulator((plugin(reducer=InvalidSnapshotReducer({TOTAL.id: value})),))
    with pytest.raises(ValueError, match="finite number or None"):
        accumulator.snapshot()


@pytest.mark.parametrize("value", [None, 0, -1, 1.5, 2**63 + 7])
def test_snapshot_accepts_null_and_finite_numbers_without_coercion(value: Any) -> None:
    accumulator = MetricAccumulator((plugin(reducer=InvalidSnapshotReducer({TOTAL.id: value})),))
    actual = accumulator.snapshot()[TOTAL.id]
    assert type(actual) is type(value) and actual == value


def test_installed_custom_schema_enters_default_kpi_host(monkeypatch: pytest.MonkeyPatch) -> None:
    contribution = plugin()
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    entry_points(monkeypatch, SimpleNamespace(name="external", load=lambda: contribution))
    projector = KPIProjector(MemoryRegistry(()))
    projector.consume(frame(1, [emitted(recorded("custom", 6))]))
    assert projector.snapshot() == {TOTAL.id: _row(6)}
    from aeroagentsim.services.kpis import METRICS

    # Comparison enumerates this catalogue even when all recorded samples are None.
    assert TOTAL.id in METRICS.keys()
    assert METRICS[TOTAL.id] == (TOTAL.unit, TOTAL.label)


@pytest.mark.parametrize("admission_kind,same_record_publication", [
    ("reservation", False), ("offline_request", False),
    ("reservation", True), ("offline_request", True), (None, True),
])
def test_installed_command_reducer_matches_reader_and_ack_bounded_service_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    admission_kind: str | None, same_record_publication: bool,
) -> None:
    contribution = plugin(reducer=CommandCountReducer())
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    entry_points(monkeypatch, SimpleNamespace(name="external", load=lambda: contribution))
    registry = MemoryRegistry((), messages=(MessageDescriptor(
        COMMAND_SCHEMA, "command", {"type": "record", "members": {"value": {"type": "integer"}},
                                    "required": ["value"], "extra": False},
    ),))
    stamp = Stamp("external-clock", 17, 2, "external-map")
    message = replace(
        recorded("external-command", 2**63 + 7, schema=COMMAND_SCHEMA, kind="command", cursor=(1, 0)),
        route="consumer", target="consumer", source_kind="ingress", source="external-host",
        origin=asdict(ItemRef(1, 0)), source_stamp=asdict(stamp),
    )
    published = {"kind": "ingress_publish", "message": emitted(message)["message"]}
    enqueue = {"kind": "enqueue", "message": message.message_id, "recipient": "consumer"}
    first_items = [admission(message, admission_kind)] if admission_kind is not None else []
    if same_record_publication:
        first_items.extend([published, enqueue])
    records = [
        frame(1, first_items),
        frame(2, [published, enqueue, {"kind": "delivery", "message": published["message"]}]),
        frame(3, [admission(replace(message, message_id="unacknowledged", cursor=(3, 0)))]),
    ]
    write_reader_run(tmp_path, registry, records, cut_index=1)
    with open_run(tmp_path) as run:
        first = run.snapshot()
        originals = list(first.iter_messages())
        assert len(originals) == 1
        original = originals[0]
        assert original.cursor == (1, 1 if same_record_publication and admission_kind is not None else 0)
        assert original.available_ns == (message.available_ns if same_record_publication else None)
        assert original.origin == message.origin and original.source_stamp == asdict(stamp)
        assert original.source == "external-host" and type(original.payload["value"]) is int
        accumulator = MetricAccumulator((contribution,))
        accumulator.feed(originals)
        expected = accumulator.snapshot()[COMMAND_COUNT.id]
        assert expected == 1
        assert project_run(tmp_path, include_series=False)["final"][COMMAND_COUNT.id]["value"] == expected

        # index.json already covers record 3. Only the explicit ACK may advance
        # either reader or live/service results, and publication cannot enrich
        # or count an earlier admission again after checkpoint restoration.
        index = json.loads((tmp_path / "index.json").read_text())
        (tmp_path / "committed-prefix.json").write_text(json.dumps({
            "index": 2, "bytes": index[2]["offset"] + index[2]["length"],
        }))
        later = run.snapshot()
        assert list(later.iter_messages()) == originals
        assert list(later.iter_messages(start=2)) == []
        service = project_run(tmp_path)
        assert service["final"][COMMAND_COUNT.id]["value"] == expected
        assert [row["commitIndex"] for row in service["series"]] == [1, 2]
        direct = KPIProjector.from_directory(tmp_path)
        direct.consume(records[0])
        resumed = KPIProjector.from_cursor(registry, json.loads(json.dumps(direct.cursor())))
        resumed.consume(records[1])
        assert resumed.snapshot()[COMMAND_COUNT.id]["value"] == expected
        assert resumed.cursor()["reducers"][0]["messages"] == accumulator.cursor()["reducers"][0]["messages"]


def test_host_interleaves_admissions_emissions_and_physical_evidence_in_item_order() -> None:
    from aerokernel.ids import EntityRef
    from aerokernel.operations import AssertEdge, FactWrite, RetractFact
    from aerokernel.registry import FieldDescriptor, TypeDescriptor
    from aerokernel.relations import Edge
    from aerokernel.time import Interval

    class EvidenceOrderReducer(OrderReducer):
        input_schemas = ("external.", "aeroagentsim.metrics.")

        def consume(self, state: list[int | float], message: RecordedMessage) -> None:
            state.append(
                4 if message.schema_id == "aeroagentsim.metrics.edge/v1" else
                6 if message.schema_id == "aeroagentsim.metrics.retract/v1" else message.payload["value"]
            )

    registry = MemoryRegistry((TypeDescriptor("Sample"),), (
        FieldDescriptor("value", "Sample", {"type": "integer"}),
    ))
    ref = EntityRef("metrics-reader", "0", "sample", 0, "Sample")
    at, stamp = Instant(10), Stamp("canonical", 10, 1, "canonical")
    valid = Interval(at, None)
    edge_op = AssertEdge("edge", "sample-link", ref, ref, valid, stamp)
    edge = Edge("edge", "sample-link", ref, ref, valid, stamp, at, "producer", ItemRef(1, 3))
    command = replace(recorded("admitted", 1, schema=COMMAND_SCHEMA, kind="command"),
                      route="consumer", target="consumer", source_kind="ingress")
    projector = KPIProjector(registry, plugins=(plugin(reducer=EvidenceOrderReducer()),))
    projector.consume(frame(1, [
        admission(command),
        {"proposal": encode(FactWrite((ref, "value"), 2, stamp, valid))},
        emitted(recorded("emitted", 3)),
        {"proposal": encode(edge_op), "edge": encode(edge)},
        admission(replace(command, message_id="offline", payload={"value": 5}, cursor=(1, 4)), "offline_request"),
        {"proposal": encode(RetractFact((ref, "value"), valid, "unavailable"))},
    ]))
    assert projector.snapshot()[ORDER.id]["value"] == 123456
    history = projector.cursor()["reducers"][0]["messages"]
    assert [row["cursor"] for row in history] == [[1, ordinal] for ordinal in range(6)]
    assert [row["kind"] for row in history] == ["command", "evidence", "event", "evidence", "command", "evidence"]


def test_direct_event_target_is_not_mislabelled_as_a_topic() -> None:
    class TargetReducer(SumReducer):
        def consume(self, state: list[int | float], message: RecordedMessage) -> None:
            assert message.target == "consumer" and message.topic is None
            super().consume(state, message)

    projector = KPIProjector(MemoryRegistry(()), plugins=(plugin(reducer=TargetReducer()),))
    message = replace(recorded("direct", 8), route="consumer", topic=None, target="consumer")
    projector.consume(frame(1, [
        emitted(message), {"kind": "enqueue", "message": "direct", "recipient": "consumer"},
    ]))
    assert projector.snapshot()[TOTAL.id]["value"] == 8


def test_scalar_payloads_are_not_restricted_by_the_generic_host() -> None:
    class ScalarReducer(SumReducer):
        def consume(self, state: list[int | float], message: RecordedMessage) -> None:
            state.append(message.payload)

    projector = KPIProjector(MemoryRegistry(()), plugins=(plugin(reducer=ScalarReducer()),))
    projector.consume(frame(1, [emitted(replace(recorded("scalar", 0), payload=7))]))
    assert projector.snapshot()[TOTAL.id]["value"] == 7


def test_structurally_equivalent_envelope_is_accepted() -> None:
    accumulator = MetricAccumulator((plugin(),))
    accumulator.consume(SimpleNamespace(
        cursor=(1, 0), message_id="duck", kind="event", schema_id=SCHEMA, payload={"value": 5},
    ))
    assert accumulator.snapshot() == {TOTAL.id: 5}


def test_failed_fold_cannot_be_retried_or_checkpointed_until_restore() -> None:
    class FailingReducer(SumReducer):
        def consume(self, state: list[int | float], message: RecordedMessage) -> None:
            super().consume(state, message)
            if message.payload["value"] < 0:
                raise ValueError("invalid negative evidence")

    accumulator = MetricAccumulator((plugin(reducer=FailingReducer()),))
    accumulator.consume(recorded("good", 3))
    checkpoint = accumulator.cursor()
    with pytest.raises(ValueError, match="negative evidence"):
        accumulator.consume(recorded("bad", -1))
    assert accumulator.snapshot() == {TOTAL.id: 2}  # Diagnostic only, never published.
    with pytest.raises(ValueError, match="failed"):
        accumulator.consume(recorded("bad", -1))
    with pytest.raises(ValueError, match="failed"):
        accumulator.cursor()
    accumulator.restore_cursor(checkpoint)
    accumulator.consume(recorded("next", 4))
    assert accumulator.snapshot() == {TOTAL.id: 7}


@pytest.mark.parametrize("bad", [{"set"}, float("nan"), object()])
def test_checkpoints_must_be_finite_json_data(bad: Any) -> None:
    class BadCheckpoint(CheckpointSumReducer):
        def checkpoint(self, state: Any) -> Any:
            return bad

    accumulator = MetricAccumulator((plugin(reducer=BadCheckpoint()),))
    with pytest.raises(ValueError, match="finite JSON"):
        accumulator.cursor()


def domain_message(schema: str, payload: dict[str, Any], at_s: int, identifier: str) -> RecordedMessage:
    return replace(recorded(identifier, 0), schema_id=schema, payload=payload,
                   at_ns=at_s * 10**9, available_ns=at_s * 10**9)


def test_builtin_mixed_task_population_is_pooled_across_checkpoint_chunks() -> None:
    messages = [
        domain_message("lowalt.task.created", {"task": "a", "kind": "medical"}, 0, "create-a"),
        domain_message("lowalt.task.created", {"task": "b", "kind": "parcel"}, 0, "create-b"),
        domain_message("traffic.report.sent", {"incident": "a"}, 0, "report"),
        domain_message("lowalt.task.completed", {"task": "a", "latency_s": 10}, 10, "done-a"),
        domain_message("lowalt.task.completed", {"task": "b", "latency_s": 30}, 30, "done-b"),
        domain_message("traffic.capture.accepted", {"incident": "a"}, 50, "upload"),
    ]
    selection = metrics_api.builtin_metric_plugins()
    whole = MetricAccumulator(selection)
    whole.feed(messages)
    values = whole.snapshot()
    assert values["tasks_created"] == values["tasks_completed"] == 3
    assert values["mean_task_latency_s"] == 30
    assert values["p95_task_latency_s"] == pytest.approx(48)
    assert values["medical_mean_task_latency_s"] == 10
    assert values["completion_rate"] == 1
    for split in range(len(messages) + 1):
        prefix = MetricAccumulator(selection)
        prefix.feed(messages[:split])
        resumed = MetricAccumulator(selection)
        resumed.restore_cursor(json.loads(json.dumps(prefix.cursor())))
        resumed.feed(messages[split:])
        resumed.feed(messages)
        assert resumed.snapshot() == values


def test_builtin_emission_population_does_not_expand_with_ingress_evidence() -> None:
    projector = KPIProjector(MemoryRegistry(()))
    before = json.dumps(projector.snapshot(), separators=(",", ":"))
    # Even event-shaped ingress envelopes must not enter the legacy population;
    # malformed builtin payloads demonstrate that exclusion happens before decode.
    for ordinal, schema in enumerate((
        "lowalt.task.created", "lowalt.incident.occurred", "lowalt.energy.swap_finished",
        "packs.logistics.transition", "traffic.report.sent", "rescue.event", "adapters.ns3.send",
    )):
        projector.consume(replace(
            recorded(str(ordinal), 99, schema=schema), source_kind="ingress",
        ))
    rescue_command = replace(
        recorded("ingress-rescue", 1, schema="rescue.bid", kind="command"),
        route="consumer", target="consumer", source_kind="ingress",
        payload={"data_json": '{"roles": ["inspect"]}'},
    )
    projector.consume(frame(1, [admission(rescue_command)]))
    projector.consume(frame(2, [{"kind": "ingress_publish", "message": emitted(rescue_command)["message"]}]))
    assert json.dumps(projector.snapshot(), separators=(",", ":")) == before
    # Matching partition commands still count, as on main; no global event filter.
    projector.consume(replace(rescue_command, message_id="partition-rescue", source_kind="partition"))
    assert projector.snapshot()["rescue_bids"]["value"] == 1
    assert projector.snapshot()["rescue_candidates"]["value"] == 1
    assert projector.snapshot()["rescue_bus_messages"]["value"] == 1


def test_legacy_rescue_report_preserves_empty_counts_and_whole_provider_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aeroagentsim.packs.metrics import compute
    from aeroagentsim.services.kpis import RESCUE_METRICS

    kernel = SimpleNamespace(configuration=freeze({"scenario": {"id": "rescue", "engines": {
        "tasks": {"plugin": "lowalt_tasks", "config": {"rescue": {}}},
    }}}), records=[])
    result = compute(kernel)
    for name, (unit, _) in RESCUE_METRICS.items():
        assert result[name] == (0 if unit in {"count", "B"} else None)
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    replacement = MetricPlugin("lowalt", (), api_version=1)
    entry_points(monkeypatch, SimpleNamespace(name="lowalt", load=lambda: replacement))
    assert compute(kernel) == {"run_id": "rescue", "duration_s": 0.0}


def test_report_reducers_receive_reader_originals_not_lossy_viewer_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from aeroagentsim.packs.metrics import compute

    large = 2**63 + 7
    stamp = asdict(Stamp("external-clock", 19, 2, "external-map"))
    pending = replace(
        recorded("admission", 9, schema=COMMAND_SCHEMA, kind="command", cursor=(1, 0)),
        source_kind="ingress", source="external-host", route="consumer", target="consumer",
        origin=asdict(ItemRef(1, 0)), source_stamp=stamp,
    )
    sample = replace(
        recorded("partition-sample", large, cursor=(1, 1)),
        route="consumer", topic=None, target="consumer",
        origin=asdict(ItemRef(1, 1)), source_stamp=stamp,
    )
    graph = replace(
        recorded("graph", 0, schema="aas.langgraph.record", cursor=(1, 3)),
        payload={"decision_id": "rescue_center/1", "phase": "model_call",
                 "data_json": '{"identity": "call", "request": {}}'},
        origin=asdict(ItemRef(1, 3)), source_stamp=stamp,
    )
    records = [
        frame(1, [
            admission(pending), emitted(sample),
            {"kind": "delivery", "message": emitted(replace(sample, message_id="delivery-copy"))["message"]},
            emitted(graph),
            {"kind": "enqueue", "message": sample.message_id, "recipient": "consumer"},
        ]),
        frame(2, [
            {"kind": "ingress_publish", "message": emitted(pending)["message"]},
            {"kind": "enqueue", "message": pending.message_id, "recipient": "consumer"},
        ]),
    ]
    registry = MemoryRegistry(())
    header = write_reader_run(tmp_path, registry, records, cut_index=2)
    with open_run(tmp_path) as run:
        originals = list(run.snapshot().iter_messages())
    assert [message.message_id for message in originals] == [pending.message_id, sample.message_id, graph.message_id]
    assert [message.cursor for message in originals] == [(1, 0), (1, 1), (1, 3)]
    assert originals[0].available_ns is None  # Later publication must not enrich.
    assert originals[1] == sample and originals[2] == graph
    assert type(originals[1].payload["value"]) is int
    expected = {message.message_id: message for message in originals}

    class CanonicalReportReducer(SumReducer):
        descriptors = (
            replace(TOTAL, id="rescue_radio_bytes", unit="B"),
            replace(TOTAL, id="rescue_radio_packets"),
        )
        input_schemas = ("external.", "aas.")

        def consume(self, state: list[int | float], message: RecordedMessage) -> None:
            # Includes source kind, route/target/topic, origin, original cursor,
            # availability, sequence, source stamp and lossless integer payloads.
            assert message == expected[message.message_id]
            if message.source_kind == "partition":
                state.append(message.payload.get("value", 0))

        def snapshot(self, state: list[int | float]) -> dict[str, Any]:
            return {"rescue_radio_bytes": sum(state) if state else None,
                    "rescue_radio_packets": len(state) if state else None}

    replacement = MetricPlugin("lowalt", (CanonicalReportReducer(),))
    entry_points(monkeypatch, SimpleNamespace(name="lowalt", load=lambda: replacement))
    accumulator = MetricAccumulator((replacement,))
    accumulator.feed(originals)
    projector = KPIProjector(registry, plugins=(replacement,))
    projector.consume(header)
    for record in records:
        projector.consume(record)
    kernel = SimpleNamespace(header=header, records=records, configuration=freeze({"scenario": {
        "id": "rescue", "engines": {
            "tasks": {"plugin": "lowalt_tasks", "config": {"rescue": {}}},
            # Force the historical viewer projection too: its console aliases,
            # delivery copies and tagged integers must not reach this reducer.
            "inspection": {"plugin": "inspection", "config": {
                "event_schema": "packs.inspection.observation", "targets": [
                    {"id": "waypoint", "kind": "waypoint", "point": [0, 0]},
                ],
            }},
        },
    }}))
    result = compute(kernel)
    for name, value in accumulator.snapshot().items():
        assert result[name] == projector.snapshot()[name]["value"] == value
    assert result["rescue_radio_bytes"] == large and type(result["rescue_radio_bytes"]) is int
    assert result["rescue_radio_packets"] == 2
    assert result["inspection"]["observations"] == 0


@pytest.mark.parametrize("metric_id,unit", [("rescue_radio_packets", "count"), ("rescue_radio_bytes", "B")])
@pytest.mark.parametrize("with_evidence", [False, True])
def test_report_preserves_installed_rescue_reducers_unknown_counters(
    monkeypatch: pytest.MonkeyPatch, metric_id: str, unit: str, with_evidence: bool,
) -> None:
    from aeroagentsim.packs.lowalt.metrics import RescueReducer
    from aeroagentsim.packs.metrics import compute

    # A builtin subclass with its own policy is still an installed replacement,
    # not the exact shipped reducer whose report mode supplies empty zeros.
    class UnknownCounter(RescueReducer):
        descriptors = (replace(TOTAL, id=metric_id, unit=unit,
                               missing_data_policy="None when eligible radio telemetry is absent."),)
        input_schemas = ("rescue.", "adapters.ns3.")

        def consume(self, state: Any, message: RecordedMessage) -> None:
            pass

        def snapshot(self, state: Any) -> dict[str, Any]:
            return {metric_id: None}

    replacement = MetricPlugin("lowalt", (UnknownCounter(),))
    entry_points(monkeypatch, SimpleNamespace(name="lowalt", load=lambda: replacement))
    records = [frame(1, [emitted(domain_message(
        "rescue.event", {"data_json": '{"kind": "noticed", "event": "R7"}'}, 0, "notice",
    ))])] if with_evidence else []
    kernel = SimpleNamespace(configuration=freeze({"scenario": {"id": "rescue", "engines": {
        "tasks": {"plugin": "lowalt_tasks", "config": {"rescue": {}}},
    }}}), records=records)
    assert compute(kernel)[metric_id] is None


def test_rescue_outcomes_keep_last_first_per_incident_semantics() -> None:
    accumulator = MetricAccumulator(metrics_api.builtin_metric_plugins())
    assert accumulator.snapshot()["rescue_success"] is None
    for index, (incident, success, confirmation, delivery) in enumerate([
        ("first", True, 12, 15), ("first", False, None, None), ("second", False, None, None),
    ]):
        accumulator.consume(domain_message("rescue.event", {"data_json": json.dumps({
            "kind": "outcome", "event": incident, "success": success,
            "time_to_confirmation_s": confirmation, "time_to_delivery_s": delivery,
        })}, index, str(index)))
        if index == 1:
            assert accumulator.snapshot()["rescue_success"] == 1
            assert accumulator.snapshot()["rescue_time_to_delivery_s"] == 15
    values = accumulator.snapshot()
    assert values["rescue_success"] == 0
    assert values["rescue_time_to_confirmation_s"] is None
    assert values["rescue_time_to_delivery_s"] is None
    assert values["rescue_bus_messages"] == 3


def test_real_asserted_traffic_edge_survives_json_checkpoint() -> None:
    from aerokernel.ids import EntityRef, ItemRef
    from aerokernel.operations import AssertEdge
    from aerokernel.relations import Edge
    from aerokernel.time import Interval, Stamp

    reg = MemoryRegistry(())
    projector = KPIProjector(reg)
    capture = EntityRef("run", "0", "capture", 0, "Capture")
    incident = EntityRef("run", "0", "second", 0, "Incident")
    for name in ("first", "second"):
        ref = EntityRef("run", "0", name, 0, "Incident")
        projector.consume(domain_message("traffic.report.sent", {"incident": {"$ref": ref.to_data()}}, 2, name))
    at = Instant(3 * 10**9)
    acquired = Stamp(clock_id="canonical", mapping_id="canonical", numerator=at.ns, denominator=1)
    assertion = AssertEdge("edge", "traffic.capture-incident", capture, incident, Interval(at, None), acquired)
    edge = Edge("edge", assertion.relation_id, capture, incident, assertion.valid, acquired, at, "traffic", ItemRef(3, 0))
    projector.consume(frame(3, [{"proposal": encode(assertion), "edge": encode(edge)}], at.ns))
    restored = KPIProjector.from_cursor(reg, json.loads(json.dumps(projector.cursor())))
    assert restored.capture_incidents == {"capture@0": "second@0"}
    restored.consume(domain_message("traffic.capture.accepted", {"capture": {"$ref": capture.to_data()}}, 12, "upload"))
    assert restored.snapshot()["tasks_completed"]["value"] == 1
    assert restored.snapshot()["mean_task_latency_s"]["value"] == 10


def test_physical_retraction_and_generations_preserve_measured_baselines() -> None:
    from aerokernel.ids import EntityRef
    from aerokernel.operations import FactWrite, RetractFact
    from aerokernel.registry import FieldDescriptor, TypeDescriptor
    from aerokernel.time import Interval, Stamp

    reg = MemoryRegistry((TypeDescriptor("oo:Aircraft"),), (
        FieldDescriptor("energy", "oo:Aircraft", {"type": "number"}, {"unit": "kJ"}),
        FieldDescriptor("position", "oo:Aircraft", {"type": "vector", "length": 3, "items": {"type": "number"}}, {"unit": "m", "frame": "enu"}),
    ))
    projector = KPIProjector(reg)
    ref = EntityRef("run", "0", "uav", 0, "oo:Aircraft")

    def fact(field: str, value: Any, at_s: int, entity: EntityRef = ref) -> dict[str, Any]:
        at = Instant(at_s * 10**9)
        return {"proposal": encode(FactWrite((entity, field), value, Stamp(clock_id="canonical", mapping_id="canonical", numerator=at.ns, denominator=1), Interval(at, None)))}

    projector.consume(frame(1, [fact("energy", 10, 0), fact("position", [0, 0, 0], 0)], at_ns=0))
    assert projector.snapshot()["energy_used_kj"]["value"] == 0
    projector.consume(frame(2, [fact("energy", 8, 1), fact("position", [3, 4, 0], 1)], at_ns=10**9))
    projector = KPIProjector.from_cursor(reg, json.loads(json.dumps(projector.cursor())))
    projector.consume(frame(3, [{"proposal": encode(RetractFact((ref, "energy"), Interval(Instant(2 * 10**9), None), "unavailable"))}], at_ns=2 * 10**9))
    projector.consume(frame(4, [fact("energy", 1, 3), fact("energy", 0.5, 4),
                                fact("position", [1000, 0, 0], 4, replace(ref, generation=1))], at_ns=4 * 10**9))
    assert projector.snapshot()["energy_used_kj"]["value"] == 2.5
    assert projector.snapshot()["distance_flown_km"]["value"] == 0.005


def test_feed_fingerprint_tracks_plugin_release_revision_and_narratives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, request: pytest.FixtureRequest,
) -> None:
    import aeroagentsim.presentation_api as presentation_api
    from aeroagentsim.services.feed_index import _signature

    reducer = CheckpointSumReducer()
    contribution = plugin(reducer=reducer)
    dist = SimpleNamespace(name="external-dist", version="1.0")
    entry = SimpleNamespace(name="external", value="external.metrics:plugin", dist=dist, load=lambda: contribution)
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    entry_points(monkeypatch, entry)
    declarations = {"custom.event": {
        "template": "one", "template_zh": "一", "category": "mixed", "severity": "info",
    }}
    presentation_data = {"format": presentation_api.PRESENTATION_FORMAT, "narratives": declarations}
    presentation = presentation_api.PresentationPlugin("custom", presentation_data)
    presentation_entry = SimpleNamespace(
        name="custom", value="custom.presentations:plugin", load=lambda: presentation,
    )

    def presentation_entries(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.presentations"
        return (presentation_entry,)

    monkeypatch.setattr(presentation_api, "BUILTINS", {})
    monkeypatch.setattr(presentation_api, "entry_points", presentation_entries)
    presentation_api.clear_presentation_plugin_cache()
    request.addfinalizer(presentation_api.clear_presentation_plugin_cache)
    authored = {"event_narratives": {"authored.event": {
        "template": "one", "category": "mixed", "severity": "info",
    }}}
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(authored))
    (tmp_path / "runtime.registry.json").write_text(json.dumps(MemoryRegistry(()).to_data()))
    (tmp_path / "journal.jsonl").write_text("first\n")
    first = _signature(tmp_path)
    assert first == json.loads(json.dumps(first))
    assert "external-dist" in json.dumps(first)
    # Appending evidence is incremental work, not a contribution change.
    with (tmp_path / "journal.jsonl").open("a") as stream:
        stream.write("next\n")
    assert _signature(tmp_path) == first
    dist.version = "2.0"
    second = _signature(tmp_path)
    assert second != first
    reducer.version = "samples-v2"
    third = _signature(tmp_path)
    assert third != second
    # Provider DATA is immutable: replace the descriptor without changing its
    # revision/source, and simulate discovery in a fresh process.
    declarations["custom.event"]["template"] = "two"
    presentation = replace(presentation, data=presentation_data)
    presentation_api.clear_presentation_plugin_cache()
    fourth = _signature(tmp_path)
    assert fourth != third

    # Authored overrides must invalidate even when the file's stat tuple agrees.
    before = scenario_path.stat()
    authored["event_narratives"]["authored.event"]["template"] = "two"
    scenario_path.write_text(json.dumps(authored))
    os.utime(scenario_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = scenario_path.stat()
    assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
        before.st_size, before.st_mtime_ns, before.st_ino,
    )
    assert _signature(tmp_path) != fourth
