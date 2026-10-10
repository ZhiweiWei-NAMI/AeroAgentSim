"""Generic full-factorial experiment runner over scenario templates.

Every reported number comes from a real run (no invented or zero-filled
measurements): the coordinator validates typed knob variations against the
template registry in the parent, then executes each (configuration, seed)
cell as its own run — a direct child of the run root — in a spawned worker
process. Matched seeds 0..seeds-1 and an optional horizon override are
applied to the pinned scenario document BEFORE final ``load_scenario``
expansion, so unexpanded low-altitude generators observe them.

Aggregation reports mean ± sample std per numeric KPI per configuration with
the explicit observed sample count ``n``; missing measurements are nulls, and
the per-metric best highlight is descriptive only (explicit min/max direction
for common business metrics, never a combined ranking).
"""

from __future__ import annotations

import copy
import csv
import itertools
import json
import math
import multiprocessing
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from aeroagentsim.scenario.loader import UniqueLoader

if TYPE_CHECKING:
    from aeroagentsim.authoring.templates import TemplateRegistry

CONTRACT = "aeroagentsim.experiment/v1"
#: Report directory under the run root; regular run directories stay direct
#: children of the root so the normal Runs listing still sees every run.
_EXPERIMENTS_DIRNAME = ".experiments"
#: Descriptive best direction per common KPI. Deliberately excludes pure
#: counters such as tasks_created: "most tasks created" is not a quality
#: claim the platform can support, so no direction is asserted for it.
_BEST_DIRECTIONS: dict[str, str] = {
    "tasks_completed": "max",
    "completion_rate": "max",
    "on_time_rate": "max",
    "mean_task_latency_s": "min",
    "p95_task_latency_s": "min",
    "tasks_failed": "min",
    "energy_used_kj": "min",
    "distance_flown_km": "min",
    "separation_conflicts": "min",
    "geofence_violations": "min",
    "weather_holds": "min",
}


class ExperimentError(ValueError):
    """User-readable experiment specification error."""


def parse_vary(items: list[str]) -> dict[str, list[Any]]:
    """Parse repeated ``knob=a,b,c`` axes with typed UniqueLoader scalars.

    Standalone helper so the CLI and the HTTP layer share one explicit,
    portable implementation.
    """
    if not isinstance(items, list):
        raise ExperimentError("vary: list of KNOB=VALUE,VALUE strings required")
    axes: dict[str, list[Any]] = {}
    for item in items:
        if not isinstance(item, str):
            raise ExperimentError(f"--vary {item!r}: string required")
        key, separator, raw = item.partition("=")
        if not separator or not key:
            raise ExperimentError(f"--vary {item!r}: KNOB=VALUE,VALUE required")
        if key in axes:
            raise ExperimentError(f"--vary {key!r}: duplicate experiment axis")
        values: list[Any] = []
        for text in raw.split(","):
            try:
                value = yaml.load(text, Loader=UniqueLoader)
            except yaml.YAMLError as exc:
                raise ExperimentError(
                    f"--vary {key}={text!r}: invalid scalar ({exc})"
                ) from exc
            if isinstance(value, (dict, list)):
                raise ExperimentError(f"--vary {key}={text!r}: scalar required")
            values.append(value)
        axes[key] = values
    return axes


def _product(axes: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not axes:
        return [{}]
    keys = list(axes)
    return [dict(zip(keys, values)) for values in itertools.product(*axes.values())]


def _child_run(
    document: dict[str, Any],
    base: str,
    directory: str,
    template: dict[str, Any],
    experiment: dict[str, Any],
    config_id: str,
    seed: int,
) -> dict[str, Any]:
    """Spawned-process worker: recompile the pinned document HERE, run, report.

    The compiled Scenario registry is deliberately not pickled: the child
    receives the validated knob document plus its base path and loads it
    locally, after the coordinator already applied seed/horizon/pacing.
    """
    from aeroagentsim.platform.simulation import RunSession
    from aeroagentsim.scenario import load_scenario
    from aeroagentsim.services.artifacts import scope_capture_storage
    from aeroagentsim.services.template_execution import execution_document

    path = Path(directory)
    scenario = load_scenario(
        execution_document(template["id"], document, Path(base)), base=Path(base)
    )
    scenario = scope_capture_storage(scenario, path)
    manifest = {
        "template": template,
        "experiment": {**experiment, "config_id": config_id, "seed": seed},
    }
    with RunSession(scenario, path) as session:
        session.storage._atomic(
            "manifest.json",
            {**session.storage.metadata(), **manifest},
        )
        session.run()
    metadata = session.storage.metadata()
    from aeroagentsim.services.kpis import project_run

    final = project_run(path, include_series=False)["final"]
    return {
        "run": str(path),
        "run_id": metadata["id"],
        "status": metadata["status"],
        "error": metadata.get("error"),
        "kpis": {name: row["value"] for name, row in final.items()},
        "kpi_meta": {
            name: {"unit": row.get("unit"), "label": row.get("label")}
            for name, row in final.items()
        },
    }


def _write_report(directory: Path, report: dict[str, Any]) -> None:
    """Atomic-enough report trio: machine JSON, tidy CSV, human Markdown."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "experiment.json.tmp"
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temporary.replace(directory / "experiment.json")
    rows = report["csv"]
    if rows:
        with (directory / "experiment.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        (directory / "experiment.csv").write_text("")
    lines = [
        f"# Experiment {report['id']}",
        "",
        f"- template: `{report['template_id']}` ({report['template_title']})",
        f"- contract: `{report['contract']}`",
        f"- status: **{report['status']}**",
        f"- configurations: {len(report['table'])} x seeds {report['seeds']}"
        f" = {report['total']} runs"
        f" ({report['completed']} completed, {report['failed']} failed)",
        "- horizon: "
        + (
            f"{report['horizon_s']} s"
            if report["horizon_s"] is not None
            else "template default"
        ),
        "",
    ]
    if report.get("error"):
        lines += [f"- error: {report['error']}", ""]
    lines += ["Best is descriptive per metric (min/max direction), not a ranking.", ""]
    if report["runs"]:
        lines += [
            "| configuration | seed | run | status |",
            "| --- | --- | --- | --- |",
        ]
        for run in report["runs"]:
            link = (
                f"[{run['run_id']}](../../{Path(run['run']).name}/manifest.json)"
                if run["run"] is not None
                else "not created"
            )
            lines.append(
                f"| {run['config_id']} | {run['seed']} | {link} | {run['status']} |"
            )
        lines.append("")
    for row in report["table"]:
        lines.append(f"## {row['config_id']} — knobs `{json.dumps(row['knobs'])}`")
        lines.append("")
        if not row["metrics"]:
            lines.append("No numeric KPI observed for this configuration.")
            lines.append("")
            continue
        lines += [
            "| metric | mean | std | n | unit | best |",
            "| --- | --- | --- | --- | --- | --- |",
        ]

        def fmt(value: Any) -> str:
            return "null" if value is None else f"{value:.6g}"

        for name, metric in row["metrics"].items():
            mean = fmt(metric["mean"])
            if metric["best"]:
                mean = f"**{mean}**"
            lines.append(
                f"| {metric['label']} (`{name}`) | {mean} "
                f"| {fmt(metric['std'])} | {metric['n']} | {metric['unit'] or ''} "
                f"| {'yes' if metric['best'] else ''} |"
            )
        lines.append("")
    (directory / "experiment.md").write_text("\n".join(lines))


class ExperimentManager:
    """Owns experiment coordinators for one run root.

    ``create`` validates the full factorial synchronously (typed knobs,
    bounds, positive seeds/jobs/horizon) and returns immediately; the fan-out
    runs on a background coordinator thread with a spawn process pool.
    ``close`` stops only coordinators this manager started.
    """

    def __init__(self, root: Path, registry: "TemplateRegistry") -> None:
        self.root = Path(root).resolve()
        self.registry = registry
        self._lock = threading.Lock()
        self._experiments: dict[str, dict[str, Any]] = {}

    def create(
        self,
        template_id: str,
        *,
        vary: dict[str, list[Any]],
        seeds: int = 3,
        jobs: int = 1,
        horizon_s: float | None = None,
        knobs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(template_id, str) or not template_id:
            raise ExperimentError("template id: nonempty string required")
        if type(seeds) is not int or seeds <= 0:
            raise ExperimentError("seeds: positive integer required")
        if type(jobs) is not int or jobs <= 0:
            raise ExperimentError("jobs: positive integer required")
        if horizon_s is not None and (
            type(horizon_s) not in (int, float)
            or not math.isfinite(horizon_s)
            or horizon_s <= 0
        ):
            raise ExperimentError("horizon_s: positive finite seconds required")
        if knobs is not None and not isinstance(knobs, dict):
            raise ExperimentError("knobs: mapping required")
        if not isinstance(vary, dict):
            raise ExperimentError("vary: mapping of knob -> values required")
        axes: dict[str, list[Any]] = {}
        for name, values in vary.items():
            if not isinstance(name, str) or not name:
                raise ExperimentError("vary: knob names must be nonempty strings")
            if name in axes:
                raise ExperimentError(f"vary {name!r}: duplicate experiment axis")
            if not isinstance(values, list) or not values:
                raise ExperimentError(
                    f"vary {name!r}: nonempty list of values required"
                )
            axes[name] = list(values)
        base_knobs = dict(knobs or {})
        if overlap := axes.keys() & base_knobs.keys():
            raise ExperimentError(
                f"knobs and vary overlap: {', '.join(sorted(overlap))}"
            )
        configs: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        bases: list[str] = []
        controls: dict[str, str] = {}
        template: dict[str, Any] | None = None
        for combination in _product(axes):
            merged = {**base_knobs, **combination}
            # Typed validation (unknown knob, wrong type, out of bounds) and
            # structural apply-hook expansion happen here in the parent,
            # against the registry, long before any worker spawns.
            prepared = self.registry.apply(template_id, merged)
            for knob in prepared.metadata.get("knobs", []):
                pointer = knob.get("pointer")
                if pointer == "/run/seed":
                    if knob["id"] in merged:
                        raise ExperimentError(
                            f"{knob['id']}: use --seeds for matched experiment seeds"
                        )
                    controls[knob["id"]] = "seed"
                elif pointer == "/run/until_ns" and horizon_s is not None:
                    if knob["id"] in merged:
                        raise ExperimentError(
                            f"{knob['id']}: cannot vary a horizon overridden by --horizon"
                        )
                    controls[knob["id"]] = "until_ns"
            if template is None:
                template = prepared.run_metadata()
            document = copy.deepcopy(prepared.document)
            # Registry snapshots are stored relative to the template base so
            # the pinned document stays relocatable; child processes resolve
            # it against the recorded base directory.
            document["run"] = {**document["run"], "pacing": "fast"}
            documents.append(document)
            bases.append(str(prepared.base))
            config = copy.deepcopy(prepared.knobs)
            for name, control in controls.items():
                if control == "seed":
                    del config[
                        name
                    ]  # Seed is a replicate coordinate, not a fixed configuration.
                else:
                    assert horizon_s is not None
                    config[name] = round(float(horizon_s) * 1e9)
            configs.append(config)
        assert template is not None
        experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
        directory = self.root / _EXPERIMENTS_DIRNAME / experiment_id
        report: dict[str, Any] = {
            "id": experiment_id,
            "contract": CONTRACT,
            "template_id": template_id,
            "template_title": template["title"],
            **({"template_title_zh": template["title_zh"]} if "title_zh" in template else {}),
            "status": "created",
            "seeds": seeds,
            "jobs": jobs,
            "horizon_s": horizon_s,
            "knobs": copy.deepcopy(base_knobs),
            "vary": copy.deepcopy(axes),
            "total": len(configs) * seeds,
            "completed": 0,
            "failed": 0,
            "error": None,
            "runs": [],
            "table": [
                {"config_id": f"config-{index}", "knobs": configs[index], "metrics": {}}
                for index in range(len(configs))
            ],
            "csv": [],
        }
        entry: dict[str, Any] = {
            "report": report,
            "directory": directory,
            "configs": configs,
            "documents": documents,
            "bases": bases,
            "seeds": seeds,
            "jobs": jobs,
            "horizon_s": horizon_s,
            "template_id": template_id,
            "controls": controls,
            "abort": threading.Event(),
            "done": threading.Event(),
            "lock": threading.Lock(),
        }
        self._save(entry)
        with self._lock:
            self._experiments[experiment_id] = entry
        threading.Thread(
            target=self._coordinate,
            args=(experiment_id,),
            name=f"experiment-{experiment_id}",
            daemon=True,
        ).start()
        with entry["lock"]:
            return self.public_report(report)

    @staticmethod
    def public_report(report: dict[str, Any]) -> dict[str, Any]:
        keys: tuple[str, ...] = (
            "id",
            "contract",
            "template_id",
            "template_title",
            "status",
            "seeds",
            "jobs",
            "horizon_s",
            "knobs",
            "vary",
            "total",
            "completed",
            "failed",
            "error",
            "runs",
            "table",
        )
        if "template_title_zh" in report:
            keys += ("template_title_zh",)
        return copy.deepcopy({key: report[key] for key in keys})

    def get(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._experiments.get(experiment_id)
        if entry is not None:
            with entry["lock"]:
                return self.public_report(entry["report"])
        stored = self.root / _EXPERIMENTS_DIRNAME / experiment_id / "experiment.json"
        if stored.is_file():
            report = json.loads(stored.read_text())
            if report.get("status") in {"created", "running"}:
                # A previous server died mid-run: stale progress is marked
                # interrupted, never synthesized as success.
                report["status"] = "interrupted"
                report["error"] = report.get("error") or (
                    "experiment interrupted by a server restart"
                )
                _write_report(stored.parent, report)
            return report  # type: ignore[no-any-return]
        raise KeyError(f"unknown experiment id: {experiment_id}")

    def wait(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._experiments.get(experiment_id)
        if entry is not None:
            entry["done"].wait()
            with entry["lock"]:
                return self.public_report(entry["report"])
        return self.get(experiment_id)

    def close(self) -> None:
        """Signal this manager's coordinators to stop; never touch others."""
        with self._lock:
            entries = list(self._experiments.values())
        for entry in entries:
            entry["abort"].set()
        for entry in entries:
            entry["done"].wait()

    def _save(self, entry: dict[str, Any]) -> None:
        with entry["lock"]:
            _write_report(entry["directory"], entry["report"])

    def _coordinate(self, experiment_id: str) -> None:
        with self._lock:
            entry = self._experiments[experiment_id]
        report = entry["report"]
        try:
            with entry["lock"]:
                report["status"] = "running"
            self._save(entry)
            context = multiprocessing.get_context("spawn")
            with context.Pool(processes=min(entry["jobs"], report["total"])) as pool:
                self._drive(entry, pool)
        except Exception as exc:
            with entry["lock"]:
                report["status"] = "faulted"
                report["error"] = f"{type(exc).__name__}: {exc}"
                self._aggregate_locked(entry)
        finally:
            with entry["lock"]:
                if report["status"] not in {"faulted", "interrupted"}:
                    report["status"] = "faulted" if report["failed"] else "completed"
                    if report["failed"]:
                        report["error"] = report["error"] or (
                            f"{report['failed']} of {report['total']} runs failed"
                        )
                self._aggregate_locked(entry)
            self._save(entry)
            with self._lock:
                self._experiments.pop(experiment_id, None)
            entry["done"].set()

    def _drive(self, entry: dict[str, Any], pool: Any) -> None:
        report = entry["report"]
        pending: list[tuple[int, int, Any]] = []
        for config_index, seed in [
            (config_index, seed)
            for config_index in range(len(entry["configs"]))
            for seed in range(entry["seeds"])
        ]:
            if entry["abort"].is_set():
                with entry["lock"]:
                    report["status"] = "interrupted"
                    report["error"] = report["error"] or (
                        "experiment cancelled before this run started"
                    )
                return
            arguments = self._arguments(entry, config_index, seed)
            async_result = pool.apply_async(_child_run, arguments)
            pending.append((config_index, seed, async_result))
            if len(pending) >= entry["jobs"]:
                self._collect(entry, pending)
                pending = []
        self._collect(entry, pending)

    def _arguments(
        self, entry: dict[str, Any], config_index: int, seed: int
    ) -> tuple[Any, ...]:
        document = copy.deepcopy(entry["documents"][config_index])
        # Seed/horizon/pacing are applied to the pinned knob document BEFORE
        # the worker's final load_scenario expansion of lowalt generators.
        document["run"] = {
            **document["run"],
            "seed": seed,
            "pacing": "fast",
        }
        if entry["horizon_s"] is not None:
            document["run"]["until_ns"] = round(float(entry["horizon_s"]) * 1e9)
        template = {
            "id": entry["template_id"],
            "title": entry["report"]["template_title"],
            **(
                {"title_zh": entry["report"]["template_title_zh"]}
                if "template_title_zh" in entry["report"]
                else {}
            ),
            "knobs": copy.deepcopy(entry["configs"][config_index]),
        }
        for name, control in entry["controls"].items():
            template["knobs"][name] = document["run"][control]
        experiment_record = {
            "id": entry["report"]["id"],
            "contract": CONTRACT,
            "template_id": entry["template_id"],
        }
        directory = str(
            self.root
            / (
                f"{entry['template_id']}-{entry['report']['id']}"
                f"-c{config_index}-s{seed}"
            )
        )
        return (
            document,
            entry["bases"][config_index],
            directory,
            template,
            experiment_record,
            f"config-{config_index}",
            seed,
        )

    def _collect(
        self,
        entry: dict[str, Any],
        pending: list[tuple[int, int, Any]],
    ) -> None:
        report = entry["report"]
        for config_index, seed, async_result in pending:
            try:
                result = async_result.get()
            except Exception as exc:
                from .storage import RunStorage

                path = Path(self._arguments(entry, config_index, seed)[2])
                error = f"{type(exc).__name__}: {exc}"
                metadata = None
                if (path / "manifest.json").is_file():
                    storage = RunStorage(path)
                    metadata = storage.metadata()
                    if metadata["status"] not in {
                        "completed",
                        "faulted",
                        "stopped",
                        "interrupted",
                    }:
                        storage.status("faulted", error=error)
                        metadata = storage.metadata()
                result = {
                    "run": str(path) if metadata is not None else None,
                    "run_id": metadata["id"] if metadata is not None else None,
                    "status": "faulted",
                    "error": error,
                    "kpis": {},
                    "kpi_meta": {},
                }
            with entry["lock"]:
                report["runs"].append(
                    {
                        "config_id": f"config-{config_index}",
                        "seed": seed,
                        **{
                            key: result.get(key)
                            for key in ("run", "run_id", "status", "error")
                        },
                        "kpis": copy.deepcopy(result.get("kpis") or {}),
                        "kpi_meta": copy.deepcopy(result.get("kpi_meta") or {}),
                    }
                )
                if result.get("status") == "completed" and not result.get("error"):
                    report["completed"] += 1
                else:
                    report["failed"] += 1
                    report["error"] = result.get("error") or report["error"]
                report["status"] = "running"
                self._aggregate_locked(entry)
            self._save(entry)

    def _aggregate_locked(self, entry: dict[str, Any]) -> None:
        """Aggregate observed KPI samples per configuration (entry lock held)."""
        report = entry["report"]
        from .kpis import METRICS

        samples: dict[str, dict[str, list[float]]] = {}
        meta: dict[str, dict[str, dict[str, Any]]] = {}
        for run in report["runs"]:
            config_id = run["config_id"]
            if run.get("status") != "completed":
                continue
            for name, value in (run.get("kpis") or {}).items():
                description = (run.get("kpi_meta") or {}).get(name) or {}
                meta.setdefault(config_id, {}).setdefault(name, description)
                if value is None:
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"Invalid measured KPI {name}: {value!r}")
                samples.setdefault(config_id, {}).setdefault(name, []).append(
                    float(value)
                )
        for row in report["table"]:
            observed = samples.get(row["config_id"], {})
            metrics: dict[str, dict[str, Any]] = {}
            for name in sorted(METRICS.keys() | observed.keys()):
                values = observed.get(name, [])
                count = len(values)
                mean = math.fsum(values) / count if count else None
                std: float | None = None
                if count > 1 and mean is not None:
                    variance = math.fsum((v - mean) ** 2 for v in values) / (count - 1)
                    std = math.sqrt(variance)
                description = meta.get(row["config_id"], {}).get(name, {})
                if not description and name in METRICS:
                    unit, label = METRICS[name]
                    description = {"unit": unit, "label": label}
                metrics[name] = {
                    "mean": mean,
                    "std": std,
                    "n": count,
                    "unit": description.get("unit"),
                    "label": description.get("label") or name,
                    "best": False,
                }
            row["metrics"] = metrics
        # Descriptive best per metric across configurations.
        names: set[str] = set()
        for row in report["table"]:
            names.update(row["metrics"])
        for name in sorted(names):
            direction = _BEST_DIRECTIONS.get(name)
            if direction is None:
                continue
            candidates = [
                (row["config_id"], row["metrics"][name])
                for row in report["table"]
                if row["metrics"].get(name, {}).get("mean") is not None
            ]
            if not candidates:
                continue
            _, best = min(
                candidates,
                key=lambda item: (
                    item[1]["mean"] if direction == "min" else -item[1]["mean"],
                    item[0],
                ),
            )
            for _, candidate in candidates:
                candidate["best"] = candidate["mean"] == best["mean"]
        report["csv"] = self._csv_rows(report)

    @staticmethod
    def _csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in report["table"]:
            if not row["metrics"]:
                rows.append(
                    {
                        "configuration": row["config_id"],
                        "knobs": json.dumps(row["knobs"], sort_keys=True),
                        "metric": "",
                        "mean": "",
                        "std": "",
                        "n": 0,
                        "unit": "",
                        "best": "",
                    }
                )
                continue
            for name, metric in row["metrics"].items():
                rows.append(
                    {
                        "configuration": row["config_id"],
                        "knobs": json.dumps(row["knobs"], sort_keys=True),
                        "metric": name,
                        "mean": metric["mean"],
                        "std": metric["std"],
                        "n": metric["n"],
                        "unit": metric["unit"] or "",
                        "best": "yes" if metric["best"] else "",
                    }
                )
        return rows
