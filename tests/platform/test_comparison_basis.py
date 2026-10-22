"""Definition evidence, missing samples and explicit immutable comparison cuts.

The small journal fixtures below have a genuine admitted kernel header and
explicitly authored event frames. They test reading/projection, not replay or
scientific validity of a simulation outcome.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp
from aerokernel.values import canonical_json, freeze

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.runs import Run, RunReadError, open_run
from aeroagentsim.services.comparison import (
    compare_runs,
    compare_runs_at_cuts,
    metric_compatibility,
    metric_identity,
)
from aeroagentsim.services.experiments import ExperimentManager
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.storage import RunStorage


def row(identifier: str, value: Any, **descriptor: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "status": "completed",
        "knobs": {},
        "algorithms": {},
        "kpis": {"latency": {"value": value, "unit": "s", "label": "Latency", **descriptor}},
    }


def test_legacy_rows_keep_unknown_compatibility_and_none_is_not_zero() -> None:
    a, b, missing = row("a", 0), row("b", None), row("missing", None)
    missing["kpis"] = {}
    original = copy.deepcopy([a, b, missing])
    result = compare_runs([a, b, missing])
    metric = result["kpis"][0]
    assert result["alignment"] == "recorded_run_end"
    assert result["basis"] == "provided_rows"
    assert metric["values"] == {"a": 0, "b": None, "missing": None}
    assert metric["compatibility"] == "unknown"
    for run_id in ("a", "b", "missing"):
        evidence = metric["evidence"][run_id]
        assert evidence["run_id"] == run_id
        assert evidence["cut"] is evidence["cursor"] is None
        assert evidence["definition"] is evidence["revision"] is None
        assert evidence["n"] == (1 if run_id == "a" else 0)
        assert evidence["sample_exclusions"] == {
            "failed": 0, "missing": 0 if run_id == "a" else 1,
        }
    assert [a, b, missing] == original


def test_unbound_projection_watermark_is_not_a_metric_source_cursor() -> None:
    legacy = row("legacy", 2)
    legacy["results_cursor"] = 100  # Observed independently after KPI projection.
    result = compare_runs([legacy])
    assert result["runs"][0]["results_cursor"] == 100
    evidence = result["kpis"][0]["evidence"]["legacy"]
    assert evidence["cut"] is evidence["cursor"] is None


def test_known_identity_is_additive_and_preserves_each_source_basis() -> None:
    a = row("a", 2, definition="observed-latency", revision="v1")
    b = row("b", 3, definition_id="observed-latency", definition_revision="v1")
    a.update(cut={"bytes": 100, "index": 4}, cursor=5, engine_versions={"engine": "r1"})
    b.update(cut={"bytes": 200, "index": 8}, cursor=9, engine_versions={"engine": "r2"})
    b["kpis"]["latency"]["label"] = "耗时 / Latency"
    result = compare_runs([a, b])
    metric = result["kpis"][0]
    assert metric["name"] == "latency" and metric["unit"] == "s"
    assert metric["values"] == {"a": 2, "b": 3}
    assert metric["compatibility"] == "compatible"
    assert metric["evidence"]["a"]["cut"] == a["cut"]
    assert metric["evidence"]["b"]["cursor"] == 9
    assert metric["evidence"]["b"]["definition"] == "observed-latency"
    assert metric["evidence"]["b"]["revision"] == "v1"
    # Metric compatibility does not assert equal engine/model/resources.
    assert metric["evidence"]["a"]["engine_versions"] != metric["evidence"]["b"]["engine_versions"]
    a["cut"]["index"] = 1000
    assert metric["evidence"]["a"]["cut"]["index"] == 4


@pytest.mark.parametrize("changed,reason", [
    ({"unit": "ms"}, "units"),
    ({"definition": "different-denominator"}, "definitions"),
    ({"revision": "v2"}, "revisions"),
])
def test_known_mismatch_cannot_hide_between_legacy_rows(
    changed: dict[str, Any], reason: str,
) -> None:
    pins = {"definition": "latency", "revision": "v1"}
    a = row("a", 2, **pins)
    b = row("b", None, **{**pins, **changed})
    legacy = row("legacy", None)
    legacy["kpis"]["latency"]["unit"] = None
    with pytest.raises(ValueError, match=f"incompatible {reason}"):
        compare_runs([a, legacy, b])


def test_unknown_unit_does_not_become_a_conflicting_or_known_definition() -> None:
    a, b = row("a", None), row("b", 1)
    a["kpis"]["latency"]["unit"] = None
    metric = compare_runs([a, b])["kpis"][0]
    assert metric["unit"] == "s" and metric["compatibility"] == "unknown"
    assert metric["evidence"]["a"]["unit"] is None
    assert metric_identity({"definition": {"id": "latency", "unit": "s", "revision": 2}}) == {
        "unit": "s", "definition": {"id": "latency", "unit": "s", "revision": 2}, "revision": 2,
    }
    with pytest.raises(ValueError, match="incompatible revisions"):
        metric_compatibility("latency", [
            {"unit": "s", "definition": "latency", "revision": True},
            {"unit": "s", "definition": "latency", "revision": 1},
        ])


def test_explicit_unequal_sample_and_exclusion_counts_are_not_erased() -> None:
    pins = {"definition": "latency", "revision": "v1"}
    a = row("a", 3, n=2, sample_exclusions={"failed": 0, "missing": 1}, **pins)
    b = row("b", 5, n=1, sample_exclusions={"failed": 2, "missing": 0}, **pins)
    metric = compare_runs([a, b])["kpis"][0]
    assert metric["evidence"]["a"]["n"] == 2
    assert metric["evidence"]["b"]["n"] == 1
    assert metric["evidence"]["b"]["sample_exclusions"]["failed"] == 2
    unknown = row("unknown", None, n=None, sample_exclusions=None)
    evidence = compare_runs([unknown])["kpis"][0]["evidence"]["unknown"]
    assert evidence["n"] is evidence["sample_exclusions"] is None


@pytest.mark.parametrize("counts", [
    {"n": -1}, {"n": True}, {"n": float("nan")},
    {"sample_exclusions": {"failed": -1}},
    {"sample_exclusions": {"missing": "0"}},
    {"sample_exclusions": {"failed": True}},
])
def test_invalid_sample_counts_are_rejected_without_coercion(counts: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        compare_runs([row("invalid", 2, **counts)])


def test_supplied_failed_prefix_is_not_relabelled_as_a_successful_sample() -> None:
    failed = row("failed", 2)
    failed["status"] = "faulted"
    metric = compare_runs([failed])["kpis"][0]
    assert metric["values"] == {"failed": 2}
    assert metric["evidence"]["failed"]["status"] == "faulted"
    # compare_runs is descriptive, not the experiment success-only aggregator.
    assert metric["evidence"]["failed"]["sample_exclusions"]["failed"] == 0


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "missing"])
def test_invalid_measurement_is_rejected_instead_of_filled(value: Any) -> None:
    with pytest.raises(ValueError, match="finite numeric"):
        compare_runs([row("invalid", value)])


def _sample(config: str, seed: int, value: Any, **changes: Any) -> dict[str, Any]:
    return {
        "config_id": config, "seed": seed, "run_id": f"{config}-{seed}",
        "status": "completed", "error": None,
        "cut": {"bytes": 100 + seed, "index": seed + 1}, "cursor": seed + 2,
        "kpis": {"mean_task_latency_s": value},
        "kpi_meta": {"mean_task_latency_s": {
            "unit": "s", "definition": "observed-latency", "revision": "v1",
        }},
        **changes,
    }


def test_experiment_aggregation_exposes_unequal_counts_and_exclusion_reasons(tmp_path: Path) -> None:
    manager = ExperimentManager(tmp_path, TemplateRegistry(None))
    report = {
        "seeds": 3,
        "runs": [
            _sample("c0", 0, 2), _sample("c0", 1, 4), _sample("c0", 2, None),
            _sample("c1", 0, 5), _sample("c1", 1, 0, status="faulted"),
            _sample("c1", 2, 0, collection_error="authored missing result"),
        ],
        "table": [{"config_id": name, "knobs": {}} for name in ("c0", "c1")],
    }
    manager._aggregate_locked({"report": report})
    first, second = [item["metrics"]["mean_task_latency_s"] for item in report["table"]]
    assert first["mean"] == 3 and first["std"] == pytest.approx(2 ** 0.5)
    assert second["mean"] == 5 and second["std"] is None
    assert [first["n"], second["n"]] == [2, 1]
    assert first["sample_counts"] == {"requested": 3, "included": 2, "excluded": 1, "pending": 0}
    assert second["sample_counts"] == {"requested": 3, "included": 1, "excluded": 2, "pending": 0}
    assert {item["reason"] for item in first["sample_exclusions"]} == {"unmeasured"}
    assert {item["reason"] for item in second["sample_exclusions"]} == {"faulted", "collection_error"}
    assert [item["run_id"] for item in first["samples"]] == ["c0-0", "c0-1"]
    assert first["samples"][0]["cut"] == {"bytes": 100, "index": 1}
    assert first["samples"][0]["descriptor"] == {
        "unit": "s", "definition": "observed-latency", "revision": "v1",
    }
    assert first["compatibility"] == second["comparison_compatibility"] == "compatible"
    assert report["outcome_counts"] == {"completed": 5, "faulted": 1}
    assert len(report["sample_exclusions"]) == 2
    # Unobserved physical measurements stay null, even with successful runs.
    energy = report["table"][0]["metrics"]["energy_used_kj"]
    assert energy["mean"] is None and energy["n"] == 0


@pytest.mark.parametrize("same_config", [True, False])
@pytest.mark.parametrize("field,value,reason", [
    ("unit", "ms", "units"),
    ("definition", "different-denominator", "definitions"),
    ("revision", "v2", "revisions"),
])
def test_experiment_rejects_definition_conflicts_before_publishing_means(
    tmp_path: Path, same_config: bool, field: str, value: Any, reason: str,
) -> None:
    manager = ExperimentManager(tmp_path, TemplateRegistry(None))
    a, b = _sample("c0", 0, 2), _sample("c0" if same_config else "c1", 1, None)
    b["kpi_meta"]["mean_task_latency_s"][field] = value
    table = [{"config_id": name, "knobs": {}, "metrics": {"prior": {"mean": None}}}
             for name in sorted({a["config_id"], b["config_id"]})]
    report = {"runs": [a, b], "table": table}
    original = copy.deepcopy(table)
    with pytest.raises(ValueError, match=f"incompatible {reason}"):
        manager._aggregate_locked({"report": report})
    assert table == original


def test_catalogue_display_defaults_are_not_legacy_definition_evidence(tmp_path: Path) -> None:
    manager = ExperimentManager(tmp_path, TemplateRegistry(None))
    report = {
        "runs": [_sample("c0", 0, 2, kpi_meta={})],
        "table": [{"config_id": "c0", "knobs": {}}],
    }
    manager._aggregate_locked({"report": report})
    metric = report["table"][0]["metrics"]["mean_task_latency_s"]
    assert metric["unit"] == "s"  # Existing display default is retained.
    assert metric["definition"] is metric["revision"] is None
    assert metric["compatibility"] == metric["comparison_compatibility"] == "unknown"
    assert metric["samples"][0]["descriptor"]["unit"] is None
    assert metric["sample_counts"]["requested"] is None
    assert metric["sample_counts"]["pending"] is None


def test_stored_legacy_experiment_is_unknown_without_rewriting_numbers(tmp_path: Path) -> None:
    report = {
        "id": "legacy", "contract": "aeroagentsim.experiment/v1",
        "template_id": "probe", "template_title": "Probe", "status": "completed",
        "seeds": 2, "jobs": 1, "horizon_s": None, "knobs": {}, "vary": {},
        "total": 2, "completed": 1, "failed": 1, "error": "recorded failure",
        "runs": [{"run_id": "old-run", "status": "completed"}],
        "table": [{"config_id": "c0", "knobs": {}, "metrics": {"latency": {
            "mean": 0, "std": None, "n": 1, "unit": "s", "label": "Latency", "best": True,
        }}}],
        "csv": [],
    }
    directory = tmp_path / ".experiments" / "legacy"
    directory.mkdir(parents=True)
    stored = directory / "experiment.json"
    stored.write_text(json.dumps(report))
    before = stored.read_bytes()
    manager = ExperimentManager(tmp_path, TemplateRegistry(None))
    restored = manager.get("legacy")
    metric = restored["table"][0]["metrics"]["latency"]
    assert {name: metric[name] for name in ("mean", "std", "n", "unit", "label", "best")} == {
        name: report["table"][0]["metrics"]["latency"][name]
        for name in ("mean", "std", "n", "unit", "label", "best")
    }
    assert metric["compatibility"] == metric["comparison_compatibility"] == "unknown"
    assert metric["definition"] is metric["revision"] is None
    assert metric["samples"] is metric["sample_exclusions"] is None
    assert metric["sample_counts"] == {
        "requested": 2, "included": 1, "excluded": None, "pending": None,
    }
    assert restored["runs"][0]["cut"] is restored["runs"][0]["cursor"] is None
    assert stored.read_bytes() == before
    public = manager.public_report(report)
    assert public["table"] == restored["table"]
    assert "compatibility" not in report["table"][0]["metrics"]["latency"]


def _append_created(path: Path, index: int, at_ns: int) -> None:
    """Append an authored event and explicitly publish that fixture's cut."""
    at = Instant(at_ns)
    payload = {"task": f"task-{index}", "kind": "parcel", "priority": 1}
    message = Message(
        f"created-{index}", "event", "lowalt.task.created", "partition", "fixture",
        index, at, at, freeze(payload), (),
    )
    record = {
        "type": "transaction", "index": index, "instant": encode(at),
        "items": [{
            "kind": "operation",
            "proposal": encode(Emit("event", "lowalt.task.created", "tasks", at, payload)),
            "message": encode(message),
        }],
    }
    journal = path / "journal.jsonl"
    with journal.open("ab") as stream:
        stream.write(canonical_json(record))
    RunStorage(path).publish_prefix(journal.stat().st_size, index)


def _fixture(
    path: Path, seed: int, *, header_only: bool = False,
    registry: MemoryRegistry | None = None, record_scenario: bool = True,
) -> Path:
    path.mkdir()
    scenario = {"id": "fixture", "run": {"seed": seed}, "algorithms": {"allocation": "fixture"}}
    if registry is None:
        registry = MemoryRegistry(())
    kernel = Kernel(configuration={"scenario": scenario} if record_scenario else None)
    try:
        kernel.bind(registry, BindingManifest("fixture", "0"), ())
        header_bytes = kernel.journal.bytes
    finally:
        kernel.close()
    (path / "journal.jsonl").write_bytes(header_bytes)
    (path / "manifest.json").write_text(json.dumps({"id": path.name, "status": "running"}))
    RunStorage(path).publish_prefix(len(header_bytes), 0)
    if not header_only:
        _append_created(path, 1, seed + 1)
    return path


def _inventory(path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        file.name: (file.read_bytes(), file.stat().st_mtime_ns)
        for file in path.iterdir() if file.is_file()
    }


def test_fixed_cuts_do_not_race_live_append_or_recapture_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = _fixture(tmp_path / "a", 0), _fixture(tmp_path / "b", 2)
    consume = KPIProjector.consume
    advanced = False

    def advance_during_projection(self: KPIProjector, record: dict[str, Any]) -> dict[str, Any]:
        nonlocal advanced
        if not advanced:
            advanced = True
            _append_created(a, 2, 10)
            _append_created(b, 2, 20)
        return consume(self, record)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Comparison consulted a mutable latest run view")

    with open_run(a) as run_a, open_run(b) as run_b:
        cuts = {"a": run_a.snapshot(), "b": run_b.snapshot()}
        with monkeypatch.context() as patch:
            patch.setattr(Run, "snapshot", forbidden)
            patch.setattr(RunStorage, "metadata", forbidden)
            patch.setattr(KPIProjector, "consume", advance_during_projection)
            result = compare_runs_at_cuts(cuts)
            assert compare_runs_at_cuts(cuts) == result
        assert advanced
        assert result["basis"] == "fixed_cuts"
        assert result["alignment"] == "recorded_run_end"
        assert result["recorded_ends"] == {
            "a": {"at_ns": "1", "commit_index": 1, "status": None},
            "b": {"at_ns": "3", "commit_index": 1, "status": None},
        }
        metric = next(item for item in result["kpis"] if item["name"] == "tasks_created")
        assert metric["values"] == {"a": 1, "b": 1}
        assert metric["compatibility"] == "unknown"
        assert all(item["cut"] == {"bytes": cuts[name].cut_bytes, "index": 1}
                   for name, item in metric["evidence"].items())
        latest = compare_runs_at_cuts({"a": run_a.snapshot(), "b": run_b.snapshot()})
        latest_metric = next(item for item in latest["kpis"] if item["name"] == "tasks_created")
        assert latest_metric["values"] == {"a": 2, "b": 2}


def test_fixed_projection_uses_only_embedded_configuration_and_registry(tmp_path: Path) -> None:
    a, b = _fixture(tmp_path / "a", 4), _fixture(tmp_path / "b", 9)
    for path in (a, b):
        # These mutable/derived artifacts must never be consulted by this API.
        (path / "scenario.json").write_text("not the embedded configuration")
        (path / "runtime.registry.json").write_text("not the embedded registry")
        RunStorage(path)._atomic("manifest.json", {"id": path.name, "status": "faulted"})
        (path / "index.json").write_text("invalid derived index")
    before = [_inventory(path) for path in (a, b)]
    with open_run(a) as run_a, open_run(b) as run_b:
        result = compare_runs_at_cuts({"a": run_a.snapshot(), "b": run_b.snapshot()})
    assert [_inventory(path) for path in (a, b)] == before
    seed = next(item for item in result["config_diff"] if item["path"] == "/run/seed")
    assert seed["values"] == {"a": 4, "b": 9}
    for run in result["runs"]:
        assert run["status"] is None  # No host-status claim at a journal cut.
        assert run["projection"]["mode"] == "current_code_recomputation"
        assert run["projection"]["revision"] is None
        assert run["kpis"]["energy_used_kj"]["value"] is None
        assert run["cursor"] == run["cut"]["index"] + 1
        assert run["engine_versions"] == {}


def test_unknown_embedded_scenario_stays_unknown(tmp_path: Path) -> None:
    path = _fixture(tmp_path / "kernel-only", 0, record_scenario=False)
    with open_run(path) as run:
        result = compare_runs_at_cuts({"kernel-only": run.snapshot()})
    projected = result["runs"][0]
    assert projected["configuration"] is None
    assert projected["status"] is None
    assert projected["kpis"]["tasks_created"]["value"] == 1
    assert all(metric["compatibility"] == "unknown" for metric in result["kpis"])


def test_fixed_physical_metric_uses_embedded_registry_and_stop_is_not_success(tmp_path: Path) -> None:
    registry = MemoryRegistry(
        (TypeDescriptor("Vehicle"),),
        (FieldDescriptor("charge", "Vehicle", {"type": "number"}, {"unit": "J", "role": "state"}),),
    )
    path = _fixture(tmp_path / "physical", 0, registry=registry)
    ref = EntityRef("fixture", "0", "vehicle", 0, "Vehicle")
    journal = path / "journal.jsonl"
    for index, energy in ((2, 1000), (3, 750)):
        at = Instant(index)
        operation = FactWrite(
            (ref, "charge"), energy,
            Stamp(clock_id="canonical", mapping_id="canonical", numerator=index, denominator=1),
            Interval(at, None),
        )
        with journal.open("ab") as stream:
            stream.write(canonical_json({
                "type": "transaction", "index": index, "instant": encode(at),
                "items": [{"kind": "operation", "proposal": encode(operation)}],
            }))
    with journal.open("ab") as stream:
        stream.write(canonical_json({
            "type": "run_stop", "index": 4, "instant": encode(Instant(3)), "items": [],
        }))
    RunStorage(path).publish_prefix(journal.stat().st_size, 4)
    (path / "runtime.registry.json").write_text(json.dumps(MemoryRegistry(()).to_data()))
    before = _inventory(path)
    with open_run(path) as run:
        result = compare_runs_at_cuts({"physical": run.snapshot()})
    assert _inventory(path) == before
    metric = next(item for item in result["kpis"] if item["name"] == "energy_used_kj")
    assert metric["values"] == {"physical": 0.25}
    # One run-level value, not the two energy observations used by the reducer.
    assert metric["evidence"]["physical"]["n"] == 1
    assert result["recorded_ends"]["physical"] == {
        "at_ns": "3", "commit_index": 4, "status": None,
    }
    assert result["runs"][0]["status"] is None


def test_fixed_api_requires_live_reader_snapshots_and_matching_ids(tmp_path: Path) -> None:
    path = _fixture(tmp_path / "a", 0)
    with pytest.raises(ValueError, match="explicit run snapshots"):
        compare_runs_at_cuts({})
    with pytest.raises(ValueError, match="Snapshot"):
        compare_runs_at_cuts({"a": {"bytes": 1, "index": 1}})  # type: ignore[dict-item]
    with open_run(path) as run:
        snapshot = run.snapshot()
        with pytest.raises(ValueError, match="identity differs"):
            compare_runs_at_cuts({"other": snapshot})
        assert compare_runs_at_cuts({"a": snapshot})["runs"][0]["id"] == "a"
    with pytest.raises(RunReadError):
        compare_runs_at_cuts({"a": snapshot})
    header = _fixture(tmp_path / "header", 0, header_only=True)
    with open_run(header) as run:
        with pytest.raises(ValueError, match="world state"):
            compare_runs_at_cuts({"header": run.snapshot()})
