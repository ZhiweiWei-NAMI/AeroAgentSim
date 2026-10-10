"""Align measured run KPIs and pinned experimental settings for comparison."""

from __future__ import annotations

from typing import Any


def compare_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Missing measurements stay null; absent knobs remain absent settings."""
    names = sorted({name for run in runs for name in run["kpis"]})
    table = []
    for name in names:
        descriptors = [run["kpis"][name] for run in runs if name in run["kpis"]]
        units = {item["unit"] for item in descriptors}
        if len(units) != 1:
            raise ValueError(f"KPI {name}: incompatible units across runs")
        table.append(
            {
                "name": name,
                "label": descriptors[0]["label"],
                "unit": descriptors[0]["unit"],
                "values": {
                    run["id"]: run["kpis"][name]["value"]
                    if name in run["kpis"]
                    else None
                    for run in runs
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
    }
