"""Public v1 contracts, discovery and deterministic message reduction for metrics.

Plugins are trusted Python, not a sandbox. Reducers may mutate only their own
state: no I/O, providers, runtime writes, wall clock or unseeded randomness.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import MISSING, asdict, dataclass, fields
from importlib import import_module
from importlib.metadata import PackageNotFoundError, entry_points, version
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import Any, Literal, Protocol

from aeroagentsim.extensions.discovery import (
    discover,
    validate_api_version,
    validate_name,
)
from aeroagentsim.extensions.records import Contribution
from aeroagentsim.runs import RecordedMessage

METRICS_API_VERSION = 1
MetricValue = int | float | None


@dataclass(frozen=True)
class MetricDescriptor:
    """Meaning of one stable metric; a changed meaning requires a new metric ID.

    ``aggregation`` states the population/denominator and accumulation rule.
    ``missing_data_policy`` distinguishes unmeasured None from an observed zero.
    """

    id: str
    label: str
    label_zh: str
    unit: str
    direction: Literal["higher", "lower"] | None
    aggregation: str
    missing_data_policy: str

    def __post_init__(self) -> None:
        for name in ("id", "label", "label_zh", "unit", "aggregation", "missing_data_policy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"metric descriptor {name}: expected nonempty text")
        if self.direction not in ("higher", "lower", None):
            raise ValueError("metric descriptor direction: expected higher, lower or None")


class Reducer(Protocol):
    """A pure, ordered fold over RecordedMessage (or structurally equal) inputs.

    Consume mutates ``state`` and returns None; snapshot must not mutate it.
    Missing measurements are None, never invented zero/success. Legacy builtin
    population counters explicitly retain their documented empty-count semantics.
    Inputs are detached JSON payloads; do not mutate them or retain live objects.
    """

    descriptors: tuple[MetricDescriptor, ...]
    input_schemas: tuple[str, ...]

    def initial(self) -> Any: ...
    def consume(self, state: Any, message: RecordedMessage) -> None: ...
    def snapshot(self, state: Any) -> Mapping[str, MetricValue]: ...


class CheckpointReducer(Reducer, Protocol):
    """Optional optimization; otherwise the accumulator checkpoints input history.

    Checkpoints must be detached JSON data. Bump ``version`` for changes to the
    implementation/checkpoint interpretation; the host rejects mismatched cursors.
    """

    version: str

    def checkpoint(self, state: Any) -> Any: ...
    def restore(self, checkpoint: Any) -> Any: ...


@dataclass(frozen=True)
class MetricPlugin:
    name: str
    reducers: tuple[Reducer, ...]
    api_version: int = 1

    def __post_init__(self) -> None:
        validate_name(self.name, label="metric plugin")
        validate_api_version(
            self.api_version, METRICS_API_VERSION, label=f"metric plugin {self.name!r}"
        )
        if not isinstance(self.reducers, tuple):
            raise ValueError(f"metric plugin {self.name!r}: reducers must be a tuple")
        ids: set[str] = set()
        for reducer in self.reducers:
            descriptors = getattr(reducer, "descriptors", None)
            schemas = getattr(reducer, "input_schemas", None)
            if not isinstance(descriptors, tuple) or not descriptors or any(
                not isinstance(item, MetricDescriptor) for item in descriptors
            ):
                raise ValueError("metric reducer descriptors must be a nonempty tuple of MetricDescriptor")
            if not isinstance(schemas, tuple) or not schemas or any(
                not isinstance(schema, str) or not schema for schema in schemas
            ):
                raise ValueError("metric reducer input_schemas must be nonempty schema-id prefixes")
            for method in ("initial", "consume", "snapshot"):
                implementation = getattr(reducer, method, None)
                if not callable(implementation) or iscoroutinefunction(implementation):
                    raise ValueError(f"metric reducer {method} must be a synchronous callable")
            for descriptor in descriptors:
                if descriptor.id in ids:
                    raise ValueError(f"Duplicate metric id: {descriptor.id!r}")
                ids.add(descriptor.id)
            checkpoint = callable(getattr(reducer, "checkpoint", None))
            restore = callable(getattr(reducer, "restore", None))
            if checkpoint != restore:
                raise ValueError("metric reducer checkpoint and restore must be provided together")
            if checkpoint and (
                not hasattr(reducer, "version")
                or iscoroutinefunction(getattr(reducer, "checkpoint"))
                or iscoroutinefunction(getattr(reducer, "restore"))
            ):
                raise ValueError("metric reducer checkpoints require a version and synchronous methods")
            revision = getattr(reducer, "version", "1")
            if not isinstance(revision, str) or not revision:
                raise ValueError("metric reducer version must be nonempty text")


def _builtin(module: str) -> Any:
    # Lazy imports keep the public API independent of any builtin domain.
    return lambda: import_module(module).plugin()


BUILTINS = MappingProxyType({
    "inspection": _builtin("aeroagentsim.packs.inspection_metrics"),
    "logistics": _builtin("aeroagentsim.packs.logistics_metrics"),
    "lowalt": _builtin("aeroagentsim.packs.lowalt.metrics"),
    "tasks": _builtin("aeroagentsim.packs.task_metrics"),
    "traffic_accident": _builtin("aeroagentsim.packs.traffic_accident.metrics"),
})


def _installed() -> dict[str, Contribution]:
    discovery = discover(
        "aeroagentsim.metrics", {},
        entries=entry_points(group="aeroagentsim.metrics"), load=False,
    )
    discovery.require_unique()
    return {entry.name: entry for entry in discovery.contributions}


def _discover(entries: Iterable[Any]) -> tuple[MetricPlugin, ...]:
    return discover(
        "aeroagentsim.metrics", BUILTINS,
        expected_type=MetricPlugin, api_version=METRICS_API_VERSION,
        name_of=lambda plugin: plugin.name, factory_label="metric plugin",
        entries=entries,
    ).providers


def builtin_metric_plugins() -> tuple[MetricPlugin, ...]:
    """The shipped legacy catalogue, without loading installed contributions."""
    return _discover(())


def discover_metric_plugins() -> tuple[MetricPlugin, ...]:
    """Sorted builtin/installed union; duplicate installed names fail before load."""
    return _discover(entry_points(group="aeroagentsim.metrics"))


def _reducer_identity(reducer: Reducer) -> dict[str, Any]:
    return {
        "implementation": f"{type(reducer).__module__}:{type(reducer).__qualname__}",
        "version": getattr(reducer, "version", "1"),
        "input_schemas": list(reducer.input_schemas),
        "descriptors": [asdict(item) for item in reducer.descriptors],
    }


def metric_plugin_fingerprint(plugins: tuple[MetricPlugin, ...]) -> list[Any]:
    """JSON-native contribution identities, not a list of today's pack files.

    Distribution releases and explicit reducer revisions must change when their
    implementation changes. This is cache invalidation, not a pinned run lock.
    """
    installed = _installed()
    result = []
    for plugin in plugins:
        entry = installed.get(plugin.name)
        distribution = (
            [entry.distribution, entry.version]
            if entry is not None and (
                entry.distribution is not None or entry.version is not None
            )
            else None
        )
        if entry is None and plugin.name in BUILTINS:
            # Source checkouts also have explicit builtin reducer versions.
            try:
                distribution = ["aeroagentsim", version("aeroagentsim")]
            except PackageNotFoundError:
                distribution = ["aeroagentsim", "source"]
        result.append({
            "name": plugin.name,
            "api_version": plugin.api_version,
            "entry_point": entry.entry_point if entry is not None else None,
            "distribution": distribution,
            "reducers": [_reducer_identity(reducer) for reducer in plugin.reducers],
        })
    return result


def _merge_legacy(target: dict[str, Any], extra: Mapping[str, Any]) -> None:
    """Pack-owned compatibility views; no business fields are interpreted here."""
    for key, value in extra.items():
        if key in target:
            if not isinstance(value, dict) or not isinstance(target[key], dict):
                raise ValueError(f"Conflicting metric compatibility field: {key}")
            if target[key].keys() & value.keys():
                raise ValueError(f"Conflicting metric compatibility mapping: {key}")
            target[key].update(deepcopy(value))
        else:
            target[key] = deepcopy(value)


class MetricAccumulator:
    """Generic schema-prefix dispatcher with message-ID deduplication.

    Each reducer owns disjoint metric IDs. Initial snapshots remain visible even
    before matching evidence, so an external unmeasured metric is present as None.
    Schema matching activates consumption, never the mere presence of a provider.
    """

    def __init__(self, plugins: tuple[MetricPlugin, ...] | None = None) -> None:
        self.plugins = discover_metric_plugins() if plugins is None else plugins
        self._contributions = metric_plugin_fingerprint(self.plugins) if plugins is None else None
        descriptors: dict[str, MetricDescriptor] = {}
        self._reducers: list[Reducer] = []
        self._identities = []
        names: set[str] = set()
        for plugin in self.plugins:
            if not isinstance(plugin, MetricPlugin):
                raise TypeError("Expected a MetricPlugin")
            if plugin.name in names:
                raise ValueError(f"Duplicate metric plugin name: {plugin.name!r}")
            names.add(plugin.name)
            for index, reducer in enumerate(plugin.reducers):
                for descriptor in reducer.descriptors:
                    if descriptor.id in descriptors:
                        raise ValueError(f"Duplicate metric id: {descriptor.id!r}")
                    descriptors[descriptor.id] = descriptor
                self._reducers.append(reducer)
                self._identities.append([plugin.name, plugin.api_version, index, _reducer_identity(reducer)])
        self.descriptors: Mapping[str, MetricDescriptor] = MappingProxyType(descriptors)
        self._output_ids = [frozenset(item.id for item in reducer.descriptors) for reducer in self._reducers]
        self._schemas = [tuple(reducer.input_schemas) for reducer in self._reducers]
        self._states = [reducer.initial() for reducer in self._reducers]
        self._history: list[list[dict[str, Any]]] = [[] for _ in self._reducers]
        self.messages: set[str] = set()
        self._failed = False

    def consume(self, message: RecordedMessage, *, deduplicate: bool = True) -> None:
        if self._failed:
            raise ValueError("Metric accumulator failed; restore a checkpoint or rebuild before consuming")
        if not isinstance(message, RecordedMessage):
            # Accept structurally equivalent envelopes without exposing kernel or
            # service types to plugins. Required fields have no inferred default.
            data = {}
            for field in fields(RecordedMessage):
                if field.init:
                    if hasattr(message, field.name):
                        data[field.name] = getattr(message, field.name)
                    elif field.default is MISSING:
                        raise ValueError(f"Metric message requires {field.name}")
            message = RecordedMessage(**data)
        if not isinstance(message.message_id, str) or not message.message_id:
            raise ValueError("Metric message requires a nonempty message_id")
        if deduplicate and message.message_id in self.messages:
            return
        try:
            for index, (reducer, state) in enumerate(zip(self._reducers, self._states)):
                if message.schema_id is None or not message.schema_id.startswith(self._schemas[index]):
                    continue
                if reducer.consume(state, deepcopy(message)) is not None:
                    raise ValueError("Metric reducer consume must mutate state and return None")
                if not callable(getattr(reducer, "checkpoint", None)):
                    self._history[index].append(asdict(deepcopy(message)))
        except Exception:
            # Diagnostic snapshots remain possible, but a partially advanced fold
            # must never be checkpointed or silently retried as successful evidence.
            self._failed = True
            raise
        if deduplicate:
            self.messages.add(message.message_id)

    def feed(self, messages: Iterable[RecordedMessage]) -> None:
        for message in messages:
            self.consume(message)

    def snapshot(self) -> dict[str, MetricValue]:
        values = {}
        for index, (reducer, state) in enumerate(zip(self._reducers, self._states)):
            snapshot = reducer.snapshot(state)
            expected = self._output_ids[index]
            if not isinstance(snapshot, Mapping) or snapshot.keys() != expected:
                raise ValueError("Metric snapshot must contain exactly its declared metric ids")
            for name, value in snapshot.items():
                if value is not None and (
                    type(value) not in (int, float)
                    or (type(value) is float and not math.isfinite(value))
                ):
                    raise ValueError(f"Metric {name!r}: expected a finite number or None")
                values[name] = value
        return values

    def cursor(self) -> dict[str, Any]:
        if self._failed:
            raise ValueError("Metric accumulator failed; cannot checkpoint partial reduction")
        result: dict[str, Any] = {}
        checkpoints = []
        for index, (reducer, state) in enumerate(zip(self._reducers, self._states)):
            checkpoint = getattr(reducer, "checkpoint", None)
            checkpoints.append(
                {"state": deepcopy(checkpoint(state))} if callable(checkpoint)
                else {"messages": deepcopy(self._history[index])}
            )
            legacy = getattr(reducer, "legacy_cursor", None)
            if callable(legacy):
                _merge_legacy(result, legacy(state))
        result.update(version=1, reducers=checkpoints, selection=deepcopy(self._identities),
                      contributions=deepcopy(self._contributions), messages=sorted(self.messages))
        try:
            return json.loads(json.dumps(result, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("Metric checkpoint must be finite JSON data") from exc

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        if not isinstance(cursor, dict) or type(cursor.get("version")) is not int or cursor["version"] != 1:
            raise ValueError("KPI cursor: unsupported checkpoint version")
        if (cursor.get("selection") != self._identities
                or cursor.get("contributions") != self._contributions
                or len(cursor.get("reducers", [])) != len(self._reducers)):
            raise ValueError("KPI cursor: reducer selection/version changed; rebuild from committed evidence")
        messages = cursor.get("messages")
        if not isinstance(messages, list) or any(not isinstance(item, str) or not item for item in messages):
            raise ValueError("KPI cursor: invalid message identities")
        states, histories = [], []
        for reducer, checkpoint in zip(self._reducers, cursor["reducers"]):
            restore = getattr(reducer, "restore", None)
            if callable(restore):
                state, history = restore(deepcopy(checkpoint["state"])), []
            else:
                state, history = reducer.initial(), deepcopy(checkpoint["messages"])
                for data in history:
                    data = dict(data)
                    data.pop("api_version", None)
                    data["cursor"] = tuple(data["cursor"])
                    if reducer.consume(state, RecordedMessage(**data)) is not None:
                        raise ValueError("Metric reducer consume must mutate state and return None")
            states.append(state)
            histories.append(history)
        self._states, self._history = states, histories
        self.messages = set(messages)
        self._failed = False

    def legacy_attributes(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for reducer, state in zip(self._reducers, self._states):
            legacy = getattr(reducer, "legacy_attributes", None)
            if callable(legacy):
                _merge_legacy(result, legacy(state))
        return result


__all__ = [
    "METRICS_API_VERSION", "MetricDescriptor", "MetricPlugin", "MetricValue",
    "Reducer", "CheckpointReducer", "MetricAccumulator", "discover_metric_plugins",
]
