"""Generic KPI feed driver over committed evidence and public metric reducers."""

from __future__ import annotations

import json
from collections.abc import Iterator, KeysView, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.messages import Emit
from aerokernel.operations import AssertEdge, FactWrite, RetractFact
from aerokernel.registry import MemoryRegistry
from aerokernel.values import thaw

from aeroagentsim.metrics_api import (
    MetricAccumulator,
    MetricPlugin,
    discover_metric_plugins,
)
# Historical re-export only; definitions and arithmetic remain pack-owned.
from aeroagentsim.packs.lowalt.metrics import RESCUE_METRICS as RESCUE_METRICS
from aeroagentsim.runs import RecordedMessage


class _MetricCatalogue(Mapping[str, tuple[str, str]]):
    """Legacy metadata view, including external metrics with no measured samples."""

    def _snapshot(self) -> dict[str, tuple[str, str]]:
        result = {}
        for plugin in discover_metric_plugins():
            for reducer in plugin.reducers:
                for descriptor in reducer.descriptors:
                    if descriptor.id in result:
                        raise ValueError(f"Duplicate metric id: {descriptor.id!r}")
                    result[descriptor.id] = (descriptor.unit, descriptor.label)
        return result

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
        self.descriptors = self._metrics.descriptors

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
        registry = MemoryRegistry.from_data(json.loads((path / "runtime.registry.json").read_text()))
        scenario = json.loads((path / "scenario.json").read_text())
        return cls(registry, scenario.get("presentation", []))

    def cursor(self) -> dict[str, Any]:
        return self._metrics.cursor()

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        self._metrics.restore_cursor(cursor)

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
        destinations = {
            (row["message"], row["recipient"])
            for row in record.get("items", [])
            if row.get("kind") == "enqueue"
            and isinstance(row.get("message"), str)
            and isinstance(row.get("recipient"), str)
        }
        for ordinal, item in enumerate(record.get("items", [])):
            if "proposal" not in item:
                continue
            op = decode_record(item["proposal"])
            cursor = (record["index"], ordinal)
            if isinstance(op, Emit) and "message" in item:
                message = decode_record(item["message"])
                if message.id in self._metrics.messages:
                    continue
                payload = thaw(message.payload)
                route = op.target_or_topic
                direct = op.kind in {"command", "cancel"} or (message.id, route) in destinations
                self._metrics.consume(RecordedMessage(
                    cursor=cursor, message_id=message.id, kind=op.kind,
                    schema_id=message.schema_id, route=route,
                    topic=route if op.kind == "event" and not direct else None,
                    target=route if direct else None,
                    source_kind=message.source_kind, source=message.source,
                    origin=asdict(message.origin) if message.origin is not None else None,
                    at_ns=message.at.ns, microstep=message.at.microstep,
                    available_ns=message.available.ns,
                    available_microstep=message.available.microstep,
                    payload=payload, sequence=message.sequence,
                    source_stamp=asdict(message.source_stamp) if message.source_stamp is not None else None,
                ))
                continue
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
