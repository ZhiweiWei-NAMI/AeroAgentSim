"""Metric plugin contracts and deterministic folds over synthetic recorded evidence.

No simulation, native backend, provider or installed contribution is needed. The
service adapter is exercised separately from the public accumulator/discovery API.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.messages import Emit, Message
from aerokernel.registry import MemoryRegistry
from aerokernel.time import Instant
from aerokernel.values import freeze

import aeroagentsim.metrics_api as metrics_api
from aeroagentsim.metrics_api import (
    METRICS_API_VERSION,
    MetricAccumulator,
    MetricDescriptor,
    MetricPlugin,
    discover_metric_plugins,
)
from aeroagentsim.runs import RecordedMessage
from aeroagentsim.services.kpis import KPIProjector

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


def entry_points(monkeypatch: pytest.MonkeyPatch, *entries: Any) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.metrics"
        return entries

    monkeypatch.setattr(metrics_api, "entry_points", query)


@pytest.fixture(autouse=True)
def isolate_installed_contributions(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_points(monkeypatch)


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


def test_custom_schema_is_exposed_through_direct_service_adapter() -> None:
    projector = KPIProjector(MemoryRegistry(()), plugins=(plugin(),))
    assert projector.descriptors[TOTAL.id] == TOTAL
    assert projector.snapshot()[TOTAL.id] == {"value": None, "unit": "count", "label": TOTAL.label}
    projector.consume(recorded("zero", 0))
    assert projector.snapshot()[TOTAL.id]["value"] == 0
    projector.consume(recorded("sample", 7))
    projector.consume(recorded("sample", 999))
    assert projector.snapshot()[TOTAL.id] == {"value": 7, "unit": "count", "label": TOTAL.label}


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
    assert projector.snapshot() == {TOTAL.id: {"value": 6, "unit": "count", "label": TOTAL.label}}
    from aeroagentsim.services.kpis import METRICS

    # Comparison enumerates this catalogue even when all recorded samples are None.
    assert TOTAL.id in METRICS.keys()
    assert METRICS[TOTAL.id] == (TOTAL.unit, TOTAL.label)


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    from aeroagentsim.services.feed_index import NarrativeProjector, _signature

    reducer = CheckpointSumReducer()
    contribution = plugin(reducer=reducer)
    dist = SimpleNamespace(name="external-dist", version="1.0")
    entry = SimpleNamespace(name="external", value="external.metrics:plugin", dist=dist, load=lambda: contribution)
    monkeypatch.setattr(metrics_api, "BUILTINS", {})
    entry_points(monkeypatch, entry)
    declarations = {"custom.event": {"template": "one"}}
    monkeypatch.setattr(NarrativeProjector, "from_directory", lambda path: SimpleNamespace(_declarations=declarations))
    (tmp_path / "scenario.json").write_text("{}")
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
    declarations["custom.event"]["template"] = "two"
    assert _signature(tmp_path) != third
