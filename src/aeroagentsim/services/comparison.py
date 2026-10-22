"""Align measured run KPIs and pinned experimental settings for comparison."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aeroagentsim.runs import Snapshot


def metric_identity(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Retain known definition pins; a name/unit alone is not a definition.

    ``definition_id``/``definition_revision`` are accepted as explicit aliases.
    No identity is inferred from a label, the current metric catalogue or a run's
    model/engine version. A structured definition may carry its own unit/revision.
    """
    definition = descriptor.get("definition")
    if definition is None:
        definition = descriptor.get("definition_id")
    structured = definition if isinstance(definition, dict) else {}
    unit = descriptor.get("unit")
    if unit is None:
        unit = structured.get("unit")
    revision = descriptor.get("revision")
    if revision is None:
        revision = descriptor.get("definition_revision")
    if revision is None:
        revision = structured.get("revision")
    return copy.deepcopy(
        {"unit": unit, "definition": definition, "revision": revision}
    )


def _same_identity(left: Any, right: Any) -> bool:
    """Type-strict equality for JSON definition pins (True is not revision 1)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_identity(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_identity(a, b) for a, b in zip(left, right)
        )
    return bool(left == right)


def metric_compatibility(name: str, descriptors: list[dict[str, Any]]) -> str:
    """Reject conflicting known pins, even when other samples are legacy.

    Compatibility describes declared metric meanings only, not equal horizons,
    resources, model randomness, sample sizes or scientific validity. All three
    pins must be known in every descriptor to assert ``compatible``.
    """
    identities = [metric_identity(item) for item in descriptors]
    for field, plural in (
        ("unit", "units"), ("definition", "definitions"), ("revision", "revisions")
    ):
        known = [item[field] for item in identities if item[field] is not None]
        if known and any(not _same_identity(known[0], item) for item in known[1:]):
            raise ValueError(f"KPI {name}: incompatible {plural} across runs")
    return (
        "compatible" if identities and all(
            item[field] is not None
            for item in identities for field in ("unit", "definition", "revision")
        ) else "unknown"
    )


def configuration_differences(documents: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare pinned JSON values, preserving missing versus explicit null."""
    missing = object()
    differences: list[dict[str, Any]] = []

    def visit(values: dict[str, Any], path: str) -> None:
        first = next(iter(values.values()))
        if all(isinstance(value, dict) for value in values.values()):
            for key in sorted({key for value in values.values() for key in value}):
                escaped = key.replace("~", "~0").replace("/", "~1")
                visit({run: value.get(key, missing) for run, value in values.items()}, path + "/" + escaped)
        elif all(isinstance(value, list) for value in values.values()):
            for index in range(max(len(value) for value in values.values())):
                visit({run: value[index] if index < len(value) else missing for run, value in values.items()}, path + "/" + str(index))
        elif first is not missing and all(value is not missing and value == first and (
                type(value) is type(first) or
                (type(value) in (int, float) and type(first) in (int, float)))
                for value in values.values()):
            return
        else:
            differences.append({
                "path": path,
                "values": {run: value for run, value in values.items() if value is not missing},
                "present": {run: value is not missing for run, value in values.items()},
            })

    visit(documents, "")
    return differences


def _measurement_evidence(
    run: dict[str, Any], descriptor: dict[str, Any]
) -> dict[str, Any]:
    value = descriptor.get("value")
    if value is not None and (
        type(value) not in (int, float) or not math.isfinite(value)
    ):
        raise ValueError("KPI comparison requires finite numeric measurements or null")
    # n counts run-level values here, not the metric's underlying events. An
    # aggregate adapter may instead supply its actual n/exclusions explicitly.
    exclusions = descriptor.get("sample_exclusions", run.get("sample_exclusions"))
    if "sample_exclusions" not in descriptor and "sample_exclusions" not in run:
        exclusions = (
            None if "n" in descriptor else {"failed": 0, "missing": int(value is None)}
        )
    count = descriptor.get("n", int(value is not None))
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError("KPI sample n must be a nonnegative integer or null")
    if isinstance(exclusions, dict):
        if any(
            item is not None and (type(item) is not int or item < 0)
            for item in exclusions.values()
        ):
            raise ValueError("KPI exclusion counts must be nonnegative integers or null")
    elif exclusions is not None and not (
        isinstance(exclusions, list) and all(isinstance(item, dict) for item in exclusions)
    ):
        raise ValueError("KPI sample exclusions must be counts, evidence rows or null")
    return {
        "run_id": run["id"],
        "cut": copy.deepcopy(run.get("cut")),
        # results_cursor is an independently observed projection watermark in
        # legacy HTTP summaries, not necessarily the source of these KPI values.
        "cursor": run.get("cursor"),
        **metric_identity(descriptor),
        "n": count,
        "sample_exclusions": copy.deepcopy(exclusions),
        "status": run.get("status"),
        "service_run_id": run.get("service_run_id"),
        "kernel_run_id": run.get("kernel_run_id"),
        "epoch": run.get("epoch"),
        "journal_identity": copy.deepcopy(run.get("journal_identity")),
        "configuration_digest": run.get("configuration_digest"),
        "registry_digest": run.get("registry_digest"),
        "engine_versions": copy.deepcopy(run.get("engine_versions")),
        "projection": copy.deepcopy(run.get("projection")),
    }


def compare_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Align supplied rows without guessing their source cuts or definitions.

    Historical/HTTP callers remain supported. These rows are not recaptured and
    their real prefix measurements (including failed-run prefixes) are retained.
    For race-free projection from recorded evidence, use compare_runs_at_cuts.
    """
    runs = copy.deepcopy(runs)
    names = sorted({name for run in runs for name in run["kpis"]})
    table = []
    for name in names:
        descriptors = [run["kpis"].get(name, {}) for run in runs]
        identities = [metric_identity(item) for item in descriptors]
        compatibility = metric_compatibility(name, descriptors)
        known_units = [item["unit"] for item in identities if item["unit"] is not None]
        first = next((item for item in descriptors if item), {})
        table.append(
            {
                "name": name,
                "label": first.get("label", name),
                "unit": known_units[0] if known_units else None,
                "values": {
                    run["id"]: descriptor.get("value")
                    for run, descriptor in zip(runs, descriptors)
                },
                "compatibility": compatibility,
                "evidence": {
                    run["id"]: _measurement_evidence(run, descriptor)
                    for run, descriptor in zip(runs, descriptors)
                },
            }
        )
    differences: dict[str, list[dict[str, Any]]] = {}
    for section in ("knobs", "algorithms"):
        keys = sorted({key for run in runs for key in run[section]})
        differences[section] = []
        for key in keys:
            values = {run["id"]: run[section].get(key) for run in runs}
            presence = {run["id"]: key in run[section] for run in runs}
            if any(
                values[run["id"]] != values[runs[0]["id"]]
                or presence[run["id"]] != presence[runs[0]["id"]]
                for run in runs[1:]
            ):
                differences[section].append(
                    {"name": key, "values": values, "present": presence}
                )
    return {
        "contract": "aeroagentsim.run-comparison/v1",
        "runs": runs,
        "kpis": table,
        "differences": differences,
        "alignment": "recorded_run_end",
        "basis": "provided_rows",
    }


def compare_runs_at_cuts(cuts: Mapping[str, Snapshot]) -> dict[str, Any]:
    """Project explicit immutable reader snapshots, never each run's latest end.

    Pass snapshots from open ``aeroagentsim.runs.open_run`` contexts. Each
    snapshot is an acknowledged, inclusive per-run cut, not a common horizon.
    This function never captures another cut or reads mutable service summaries,
    template defaults or projection caches. Host terminal status is unavailable
    in a snapshot and remains None; a run_stop/ACK is not success.
    """
    from aerokernel.codec import decode_record
    from aerokernel.registry import MemoryRegistry

    from aeroagentsim.runs import Snapshot

    from .kpis import KPIProjector

    if not 1 <= len(cuts) <= 20:
        raise ValueError("Provide 1–20 explicit run snapshots")
    # Pin the mapping itself before projecting. Snapshots remain valid only
    # inside their reader contexts; an append cannot extend any of these values.
    sources = list(cuts.items())
    rows = []
    recorded_ends = {}
    documents = {}
    for run_id, snapshot in sources:
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Run snapshot ids must be nonempty strings")
        if not isinstance(snapshot, Snapshot):
            raise ValueError(f"Run {run_id}: an acknowledged reader Snapshot is required")
        header = snapshot.header
        if header.service_run_id is not None and header.service_run_id != run_id:
            raise ValueError(f"Run {run_id}: snapshot service identity differs")
        if snapshot.cut_index < 1:
            raise ValueError(f"Run {run_id} has not committed a world state yet")
        records = snapshot.iter_records()
        raw_header = next(records)
        configuration = raw_header["configuration"]
        document = configuration.get("scenario") if isinstance(configuration, dict) else None
        if document is not None and not isinstance(document, dict):
            raise ValueError(f"Run {run_id}: invalid recorded scenario configuration")
        documents[run_id] = copy.deepcopy(document)
        projector = KPIProjector(
            MemoryRegistry.from_data(raw_header["registry"]),
            document.get("presentation", []) if document is not None else [],
        )
        last = raw_header
        for record in records:
            projector.consume(record)
            last = record
        if last["index"] != snapshot.cut_index:
            raise ValueError(f"Run {run_id}: snapshot did not supply its declared cut")
        instant = decode_record(last["instant"])
        cut = {"bytes": snapshot.cut_bytes, "index": snapshot.cut_index}
        recorded_ends[run_id] = {
            "at_ns": str(instant.ns), "commit_index": snapshot.cut_index,
            "status": None,
        }
        rows.append({
            "id": run_id,
            "service_run_id": header.service_run_id,
            "kernel_run_id": header.kernel_run_id,
            "epoch": header.epoch,
            "provenance": header.provenance,
            "configuration_digest": header.configuration_digest,
            "registry_digest": header.registry_digest,
            "engine_versions": (
                dict(header.engine_versions) if header.engine_versions is not None else None
            ),
            "journal_identity": list(header.journal_identity),
            "cut": cut,
            "cursor": snapshot.cut_index + 1,
            "status": None,
            # No current template registry is consulted to reconstruct knob
            # aliases. The exact embedded configuration is compared below.
            "knobs": {},
            "algorithms": copy.deepcopy(document.get("algorithms", {})) if document is not None else {},
            "configuration": copy.deepcopy(document),
            "kpis": projector.snapshot(),
            "projection": {
                "contract": "aeroagentsim.kpi/v1",
                "mode": "current_code_recomputation",
                "definition": None,
                "revision": None,
            },
        })
    return {
        **compare_runs(rows),
        "basis": "fixed_cuts",
        "recorded_ends": recorded_ends,
        "config_diff": configuration_differences(documents),
    }
