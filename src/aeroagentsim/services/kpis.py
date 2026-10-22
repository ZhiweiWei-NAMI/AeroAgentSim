"""Generic KPI feed driver over committed evidence and public metric reducers."""

from __future__ import annotations

import json
from collections.abc import Iterator, KeysView, Mapping
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.operations import AssertEdge, FactWrite, RetractFact
from aerokernel.registry import MemoryRegistry
from aerokernel.values import thaw

from aeroagentsim.metrics_api import (
    MetricAccumulator,
    MetricDescriptor,
    MetricPlugin,
    discover_metric_plugins,
)
# Historical re-export only; definitions and arithmetic remain pack-owned.
from aeroagentsim.packs.lowalt.metrics import RESCUE_METRICS as RESCUE_METRICS
from aeroagentsim.runs import RecordedMessage, iter_record_messages


# Presentation compatibility, not discovery order. Keep the original main KPI
# slots even when an installed provider replaces their implementation.
_LEGACY_KPI_ORDER = (
    "tasks_created", "tasks_assigned", "tasks_completed", "tasks_failed",
    "completion_rate", "mean_task_latency_s", "p95_task_latency_s",
    "medical_mean_task_latency_s", "medical_p95_task_latency_s", "on_time_rate",
    "energy_used_kj", "distance_flown_km", "spraying_distance_m", "battery_swaps",
    "inspection_observations", "patrol_visits", "separation_conflicts",
    "geofence_violations", "weather_holds", "accident_reported", "award_committed",
    "captures_stored", "uploads_accepted", "incidents_occurred", "incidents_noticed",
    "incidents_photographed", "incident_response_s", "handoffs_completed",
    "rescue_success", "rescue_time_to_confirmation_s", "rescue_time_to_delivery_s",
    "rescue_plans", "rescue_bids", "rescue_candidates", "rescue_radio_packets",
    "rescue_radio_delivered", "rescue_radio_bytes", "rescue_bus_messages",
    "rescue_bus_bytes", "rescue_model_calls",
)


def _ordered_descriptors(plugins: tuple[MetricPlugin, ...]) -> dict[str, MetricDescriptor]:
    descriptors: dict[str, MetricDescriptor] = {}
    owners: dict[str, str] = {}
    for plugin in plugins:
        for reducer in plugin.reducers:
            for descriptor in reducer.descriptors:
                if descriptor.id in descriptors:
                    raise ValueError(f"Duplicate metric id: {descriptor.id!r}")
                descriptors[descriptor.id] = descriptor
                owners[descriptor.id] = plugin.name
    legacy = [name for name in _LEGACY_KPI_ORDER if name in descriptors]
    additional = sorted(descriptors.keys() - set(legacy), key=lambda name: (owners[name], name))
    return {name: descriptors[name] for name in (*legacy, *additional)}


class _MetricCatalogue(Mapping[str, tuple[str, str]]):
    """Legacy metadata view, including external metrics with no measured samples."""

    def _snapshot(self) -> dict[str, tuple[str, str]]:
        return {
            name: (descriptor.unit, descriptor.label)
            for name, descriptor in _ordered_descriptors(discover_metric_plugins()).items()
        }

    def __getitem__(self, key: str) -> tuple[str, str]:
        return self._snapshot()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._snapshot())

    def keys(self) -> KeysView[str]:
        return self._snapshot().keys()


METRICS = _MetricCatalogue()


class KPIProjector:
    def __init__(
        self, registry: MemoryRegistry, presentation: list[dict[str, Any]] | None = None,
        *, plugins: tuple[MetricPlugin, ...] | None = None,
    ) -> None:
        self.registry = registry
        self.fields = {field.id: field for field in registry.fields}
        self.position_bindings = {item["typeId"]: item["positionField"] for item in presentation or []}
        self._presentation = [{"typeId": kind, "positionField": field}
                              for kind, field in self.position_bindings.items()]
        self._types: dict[str, list[str]] = {}
        self._metrics = MetricAccumulator(plugins)
        self.descriptors = MappingProxyType(_ordered_descriptors(self._metrics.plugins))
        self._message_context: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        # Read-only compatibility for the old internal projector attributes.
        # Reducers, not the host, supply and interpret these views.
        metrics = self.__dict__.get("_metrics")
        if metrics is not None:
            if name == "messages":
                return set(metrics.messages)
            attributes = metrics.legacy_attributes()
            if name in attributes:
                return attributes[name]
        raise AttributeError(name)

    @classmethod
    def from_directory(cls, path: Path) -> KPIProjector:
        from .storage import RunStorage

        registry = MemoryRegistry.from_data(json.loads((path / "runtime.registry.json").read_text()))
        scenario = json.loads((path / "scenario.json").read_text())
        projector = cls(registry, scenario.get("presentation", []))
        storage = RunStorage(path)
        storage.refresh_committed()
        if storage.entries:
            for header in storage.records(0, 1):
                projector.consume(header)
        return projector

    def cursor(self) -> dict[str, Any]:
        return {**self._metrics.cursor(), "message_context": deepcopy(self._message_context)}

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        self._metrics.restore_cursor(cursor)
        self._message_context = deepcopy(cursor.get("message_context"))

    @classmethod
    def from_cursor(
        cls, registry: MemoryRegistry, cursor: dict[str, Any],
        presentation: list[dict[str, Any]] | None = None,
        *, plugins: tuple[MetricPlugin, ...] | None = None,
    ) -> KPIProjector:
        projector = cls(registry, presentation, plugins=plugins)
        projector.restore_cursor(cursor)
        return projector

    def _ancestry(self, type_id: str) -> list[str]:
        if type_id not in self._types:
            self._types[type_id] = [kind.id for kind in self.registry.types
                                    if self.registry.is_a(type_id, kind.id)]
        return self._types[type_id]

    def consume(self, raw_record: dict[str, Any] | RecordedMessage) -> dict[str, Any]:
        """Consume an original message or one already-committed journal record.

        The feed index owns ACK bounds. Fact/retraction/edge evidence is adapted
        into detached envelopes; no domain schemas or arithmetic live here.
        """
        if isinstance(raw_record, RecordedMessage):
            self._metrics.consume(raw_record)
            return self.snapshot()
        record = expand_record(raw_record)
        if record.get("type") == "header":
            manifest = record.get("manifest", {})
            self._message_context = {
                "partitions": [row["fields"]["id"] for row in record.get("partitions", [])],
                "ingress_source": manifest.get("ingress_source"),
                "control_source": manifest.get("control_source"),
            }
        context = self._message_context
        if context is None:
            # Bare historical diagnostic frames have no pinned header. Retain
            # their direct-event routing using only recorded enqueue recipients.
            context = {"partitions": [
                row["recipient"] for row in record.get("items", [])
                if row.get("kind") == "enqueue" and isinstance(row.get("recipient"), str)
            ]}
        originals = {
            message.cursor[1]: message
            for message in iter_record_messages(
                record, partitions=frozenset(context["partitions"]),
                ingress_source=context.get("ingress_source"),
                control_source=context.get("control_source"),
            )
        }
        for ordinal, item in enumerate(record.get("items", [])):
            if ordinal in originals:
                self._metrics.consume(originals[ordinal])
            if "proposal" not in item:
                continue
            op = decode_record(item["proposal"])
            cursor = (record["index"], ordinal)
            if isinstance(op, (FactWrite, RetractFact)):
                ref, field = op.key
                payload = {"entity": ref.to_data(), "field_id": field}
                if isinstance(op, FactWrite):
                    descriptor = self.fields[field]
                    payload.update(value=thaw(op.value), schema=thaw(descriptor.schema),
                                   metadata=thaw(descriptor.metadata), types=self._ancestry(ref.type_id),
                                   presentation=self._presentation)
                    schema = "aeroagentsim.metrics.fact/v1"
                else:
                    schema = "aeroagentsim.metrics.retract/v1"
            elif isinstance(op, AssertEdge) and "edge" in item:
                edge = decode_record(item["edge"])
                payload = {"relation_id": edge.relation_id, "source": edge.source.to_data(),
                           "target": edge.target.to_data()}
                schema = "aeroagentsim.metrics.edge/v1"
            else:
                continue
            at = decode_record(record["instant"])
            # Legacy physical baselines are folded in order, not message-deduped.
            # These synthetic evidence IDs are never original journal messages.
            self._metrics.consume(RecordedMessage(
                cursor=cursor, message_id=f"metric-evidence/{cursor[0]}/{cursor[1]}",
                kind="evidence", schema_id=schema, at_ns=at.ns, microstep=at.microstep,
                payload=payload,
            ), deduplicate=False)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        values = self._metrics.snapshot()
        return {
            name: {"value": values[name], "unit": descriptor.unit, "label": descriptor.label}
            for name, descriptor in self.descriptors.items()
        }


def project_run(path: Path, *, include_series: bool = True) -> dict[str, Any]:
    """Read committed KPIs through the same persistent index used by the feed."""
    from .feed_index import kpi_result

    return kpi_result(path, include_series=include_series)


def metrics(run: Path | str) -> dict[str, Any]:
    """CLI metric routing: service runs share live/compare KPIs.

    Bare journal files retain the historical pack-report facade and its complete
    replay-integrity check; they lack the sidecars required by the service index.
    """
    path = Path(run)
    if path.is_dir() and (path / "runtime.registry.json").exists():
        return project_run(path, include_series=False)
    from aeroagentsim.packs.metrics import metrics as legacy_metrics

    return legacy_metrics(path)
