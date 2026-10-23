"""Additive KPI v1 descriptor metadata, without changing measurements or legacy rows."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel.binding import BindingManifest
from aerokernel.codec import encode
from aerokernel.registry import MemoryRegistry
from aerokernel.time import Instant
from aerokernel.values import ResourceBudget, canonical_json, freeze

import aeroagentsim.metrics_api as metrics_api
from aeroagentsim.metrics_api import MetricAccumulator, MetricDescriptor, MetricPlugin
from aeroagentsim.runs import RecordedMessage
from aeroagentsim.services.kpis import KPIProjector, metrics, project_run

FIELDS = ("label_zh", "direction", "aggregation", "missing_data_policy")
MOISTURE = MetricDescriptor(
    id="orchard.moisture",
    label="Observed soil moisture",
    label_zh="已观测土壤湿度",
    unit="%",
    direction="higher",
    aggregation="Latest moisture sample from orchard.sample events.",
    missing_data_policy="None before a sample; a recorded zero is measured zero.",
)


class SampleReducer:
    input_schemas = ("orchard.sample",)

    def __init__(self, *descriptors: MetricDescriptor) -> None:
        self.descriptors = descriptors

    def initial(self) -> list[int]:
        return []

    def consume(self, state: list[int], message: RecordedMessage) -> None:
        if message.kind == "event":
            state.append(message.payload["value"])

    def snapshot(self, state: list[int]) -> dict[str, int | None]:
        return {descriptor.id: state[-1] if state else None for descriptor in self.descriptors}


def installed(monkeypatch: pytest.MonkeyPatch, *plugins: MetricPlugin) -> None:
    """Exercise installed entry-point discovery without installing a distribution."""
    entries = tuple(SimpleNamespace(
        name=plugin.name, value=f"{plugin.name}.metrics:plugin",
        dist=SimpleNamespace(name=f"fake-{plugin.name}", version="1.0"),
        load=lambda plugin=plugin: plugin,
    ) for plugin in plugins)

    def query(*, group: str) -> tuple[Any, ...]:
        assert group == "aeroagentsim.metrics"
        return entries

    monkeypatch.setattr(metrics_api, "entry_points", query)


@pytest.fixture(autouse=True)
def isolate_installed_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch)


def expected_row(descriptor: MetricDescriptor, value: int | float | None) -> dict[str, Any]:
    metadata = asdict(descriptor)
    metadata.pop("id")
    return {"value": value, **metadata}


def sample(index: int, value: int) -> RecordedMessage:
    return RecordedMessage(
        cursor=(index, 0), message_id=f"sample/{index}", kind="event",
        schema_id="orchard.sample", at_ns=index, microstep=0, payload={"value": value},
    )


def test_builtin_rows_add_descriptors_without_changing_existing_fields() -> None:
    plugins = metrics_api.builtin_metric_plugins()
    values = MetricAccumulator(plugins).snapshot()
    projector = KPIProjector(MemoryRegistry(()), plugins=plugins)
    rows = projector.snapshot()
    assert tuple(rows) == tuple(projector.descriptors)
    for name, descriptor in projector.descriptors.items():
        assert rows[name] == expected_row(descriptor, values[name])
        assert tuple(rows[name]) == ("value", "unit", "label", *FIELDS)
        assert {key: rows[name][key] for key in ("value", "unit", "label")} == {
            "value": values[name], "unit": descriptor.unit, "label": descriptor.label,
        }
    assert rows["tasks_completed"]["value"] == 0
    # Do not reuse the browser's historical 'higher is better' guess for this ID.
    assert rows["tasks_completed"]["direction"] is None
    assert rows["mean_task_latency_s"]["value"] is None
    assert rows["mean_task_latency_s"]["direction"] == "lower"


@pytest.mark.parametrize("direction", ["higher", "lower", None])
def test_installed_metric_rows_keep_metadata_and_null_distinct_from_zero(
    monkeypatch: pytest.MonkeyPatch, direction: str | None,
) -> None:
    descriptor = replace(MOISTURE, direction=direction)
    installed(monkeypatch, MetricPlugin("orchard", (SampleReducer(descriptor),)))
    projector = KPIProjector(MemoryRegistry(()))
    assert "tasks_created" in projector.snapshot()  # Installed and builtin union.
    assert projector.snapshot()[descriptor.id] == expected_row(descriptor, None)
    assert projector.consume(sample(1, 0))[descriptor.id] == expected_row(descriptor, 0)
    assert projector.consume(sample(2, 7))[descriptor.id] == expected_row(descriptor, 7)
    assert json.loads(json.dumps(projector.snapshot()))[descriptor.id]["direction"] == direction


def test_installed_replacement_supplies_metadata_even_for_a_builtin_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = replace(MOISTURE, id="tasks_completed", direction=None)
    installed(monkeypatch, MetricPlugin("tasks", (SampleReducer(descriptor),)))
    projector = KPIProjector(MemoryRegistry(()))
    assert projector.snapshot()[descriptor.id] == expected_row(descriptor, None)


def test_snapshot_bytes_are_stable_across_discovery_order_and_checkpoint_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha = MetricPlugin("alpha", (SampleReducer(
        replace(MOISTURE, id="z.sample", direction=None),
        replace(MOISTURE, id="m.sample", direction="lower"),
    ),))
    zulu = MetricPlugin("zulu", (SampleReducer(replace(MOISTURE, id="a.sample")),))
    payloads = []
    for entries in ((zulu, alpha), (alpha, zulu)):
        installed(monkeypatch, *entries)
        projector = KPIProjector(MemoryRegistry(()))
        projector.consume(sample(1, 0))
        resumed = KPIProjector.from_cursor(MemoryRegistry(()), projector.cursor())
        for candidate in (projector, resumed):
            candidate.consume(sample(2, 7))
            before = candidate.cursor()
            rows = candidate.snapshot()
            assert list(rows)[-3:] == ["m.sample", "z.sample", "a.sample"]
            payloads.append(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode())
            assert candidate.snapshot() == rows
            assert candidate.cursor() == before
    assert all(payload == payloads[0] for payload in payloads)


def test_legacy_rows_are_passed_through_without_descriptor_enrichment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import aeroagentsim.packs.metrics as legacy
    import aeroagentsim.services.feed_index as feed_index

    # The public facade must not enrich descriptorless rows by metric ID.
    rows = {"tasks_completed": {"value": 0, "unit": "count", "label": "Old completed"},
            "old.distance": {"value": None, "unit": "m", "label": "Old distance"}}
    payload = {"contract": "aeroagentsim.kpi/v1", "final": rows, "series": []}
    before = canonical_json(payload)
    monkeypatch.setattr(feed_index, "kpi_result", lambda path, *, include_series: payload)
    assert project_run(tmp_path, include_series=False) is payload
    assert canonical_json(payload) == before
    assert all(not set(FIELDS).intersection(row) for row in rows.values())

    # The separate legacy pack report retains its own shape and missing-value policy.
    kernel = SimpleNamespace(records=[], configuration=freeze({"scenario": {
        "id": "legacy", "engines": {"delivery": {"plugin": "logistics", "config": {
            "event_schema": "legacy.delivery", "fleet": ["drone-1"],
        }}},
    }}))
    report = legacy.compute(kernel)
    assert report == {"run_id": "legacy", "duration_s": 0.0, "delivery": {
        "pack": "logistics", "released": 0, "delivered": 0, "accepted": 0, "rejected": 0,
        "on_time_rate": None, "mean_delivery_time_s": None, "energy_per_parcel_j": None,
        "utilization": None,
    }}
    monkeypatch.setattr(legacy, "metrics", lambda path: report)
    assert metrics(tmp_path / "old.jsonl") is report


def write_empty_run(directory: Path) -> None:
    """Synthetic acknowledged journal, not a simulation or replay-success claim."""
    directory.mkdir()
    registry = MemoryRegistry(())
    scenario = {"id": "descriptor-test", "registry": {}, "engines": {}}
    header = {
        "type": "header", "format": "aerokernel.journal", "major": 1, "minor": 2,
        "index": 0, "instant": encode(Instant(0)), "time_origin_ns": 0,
        "run_id": "descriptor-test", "epoch": "0", "registry": registry.to_data(),
        "registry_digest": registry.digest,
        "manifest": BindingManifest("descriptor-test", "0").to_data(),
        "partitions": [], "mappings": [], "budget": asdict(ResourceBudget()),
        "max_microsteps": 1024, "durability": "memory", "root_seed": 0,
        "configuration": {"scenario": scenario}, "ingress_policy": None,
        "engine_profiles": {}, "serializer": {"encoder": "stdlib-json/v1"}, "rng": {},
        "resolved_bindings": [], "engine_versions": {},
    }
    records = [header, {"type": "transaction", "kind": "commit", "index": 1,
                        "instant": encode(Instant(10)), "items": []}]
    lines = [canonical_json(record) for record in records]
    data = b"".join(lines)
    (directory / "journal.jsonl").write_bytes(data)
    (directory / "index.json").write_text(json.dumps([
        {"index": 0, "offset": 0, "length": len(lines[0])},
        {"index": 1, "offset": len(lines[0]), "length": len(lines[1])},
    ]))
    (directory / "committed-prefix.json").write_text(json.dumps({"index": 1, "bytes": len(data)}))
    (directory / "scenario.json").write_text(json.dumps(scenario))
    (directory / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (directory / "manifest.json").write_text(json.dumps({
        "id": directory.name, "kernel_run_id": "descriptor-test", "epoch": "0",
        "status": "completed", "final_cursor": 2,
    }))


def test_summary_only_payload_bytes_do_not_depend_on_run_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    installed(monkeypatch, MetricPlugin("orchard", (SampleReducer(MOISTURE),)))
    payloads = []
    for name in ("first", "second"):
        directory = tmp_path / name
        write_empty_run(directory)
        result = project_run(directory, include_series=False)
        assert result["contract"] == "aeroagentsim.kpi/v1"
        assert result["series"] == [] and result["results_final"] is True
        assert "projection_provenance" not in result
        assert result["final"][MOISTURE.id] == expected_row(MOISTURE, None)
        payloads.append(canonical_json(result))
        assert canonical_json(project_run(directory, include_series=False)) == payloads[-1]
        full = project_run(directory)
        assert full["final"] == result["final"]
        assert full["series"][0]["kpis"] == result["final"]
    assert payloads[0] == payloads[1]
