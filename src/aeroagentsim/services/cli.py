"""Local execution, engine-free replay checking and optional API host."""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from aerokernel.journal import replay

from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario.loader import UniqueLoader

if TYPE_CHECKING:
    from aeroagentsim.authoring.templates import TemplateRegistry

GRACEFUL_SHUTDOWN_SECONDS = 5


def serve_app(app: Any, *, host: str, port: int) -> None:
    import uvicorn

    # Waiting SSE clients cannot block lifespan cleanup indefinitely. Uvicorn
    # cancels remaining requests at this deadline, then the app stops workers.
    uvicorn.run(app, host=host, port=port,
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)


def _registry(templates_root: Path | None) -> "TemplateRegistry":
    from aeroagentsim.authoring.templates import TemplateRegistry

    return TemplateRegistry(templates_root)


def template_list(
    templates_root: Path | None = None,
) -> list[dict[str, Any]]:
    return _registry(templates_root).list()


def _parse_scalar(text: str) -> object:
    """Typed knob values via UniqueLoader (YAML 1.2 booleans, strict)."""
    try:
        value = yaml.load(text, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"--set value {text!r}: invalid scalar ({exc})") from exc
    if isinstance(value, (dict, list)):
        raise ValueError(f"--set value {text!r}: scalar required")
    return value


def _knobs(sets: list[str]) -> dict[str, object]:
    knobs: dict[str, object] = {}
    for item in sets:
        key, separator, raw = item.partition("=")
        if not separator or not key:
            raise ValueError(f"--set {item!r}: KEY=VALUE required")
        if key in knobs:
            raise ValueError(f"--set {key!r}: duplicate knob")
        knobs[key] = _parse_scalar(raw)
    return knobs


def _final_kpis(directory: Path) -> dict[str, object]:
    from aeroagentsim.services.kpis import project_run

    final: dict[str, dict[str, Any]] = project_run(directory, include_series=False)[
        "final"
    ]
    return {name: row["value"] for name, row in final.items()}


def run_template_command(
    template_id: str,
    *,
    sets: list[str],
    headless: bool,
    port: int,
    out: Path,
    templates_root: Path | None = None,
) -> None:
    from aeroagentsim.scenario import load_scenario
    from aeroagentsim.services.template_execution import execution_document

    registry = _registry(templates_root)
    knobs = _knobs(sets)
    prepared = registry.apply(template_id, knobs)
    if headless:
        document = execution_document(template_id, prepared.document, prepared.base)
        directory = out.resolve() / f"{template_id}-{uuid.uuid4().hex[:12]}"
        with RunSession(
            load_scenario(document, base=prepared.base), directory
        ) as session:
            session.storage._atomic(
                "manifest.json",
                {
                    **session.storage.metadata(),
                    "template": prepared.run_metadata(),
                },
            )
            session.run()
        metadata = json.loads((directory / "manifest.json").read_text())
        print(
            json.dumps(
                {
                    "run_id": metadata["id"],
                    "run": str(directory),
                    "status": metadata["status"],
                    "kpis": _final_kpis(directory),
                },
                indent=2,
            ),
            flush=True,
        )
        return
    # Interactive: serve the Studio workspace with the template imported.
    repository = Path(__file__).resolve().parents[3]
    frontend = repository / "frontend/dist"
    if not (frontend / "index.html").is_file():
        raise FileNotFoundError(
            "Frontend build missing for interactive template sessions. "
            "Run: cd frontend && npm ci && npm run build, or pass --headless."
        )
    if port == 0:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    console = f"http://127.0.0.1:{port}"
    os.environ["AEROAGENTSIM_CONSOLE_URL"] = console
    from .app import create_app

    root = out.resolve()
    app = create_app(
        root / "runs",
        frontend=frontend,
        studio_root=root / "workspaces",
        templates_root=templates_root,
    )
    store = app.state.studio
    draft = store.import_template(template_id, knobs, registry=registry)
    draft["scenario"] = execution_document(
        template_id, draft["scenario"], store._base(draft["scenario"], draft["id"])
    )
    store._write(draft)
    url = console + "/w/" + draft["id"] + "?panel=controls"
    print(
        json.dumps({"url": url, "workspace": draft["id"], "template": template_id}),
        flush=True,
    )
    import webbrowser

    webbrowser.open(url)
    serve_app(app, host="127.0.0.1", port=port)


def experiment_command(
    template_id: str,
    *,
    varies: list[str],
    seeds: int,
    jobs: int,
    horizon: float | None,
    out: Path,
    templates_root: Path | None = None,
) -> None:
    from aeroagentsim.authoring.templates import TemplateRegistry
    from aeroagentsim.services.experiments import ExperimentManager, parse_vary

    vary = parse_vary(varies)
    manager = ExperimentManager(out.resolve(), TemplateRegistry(templates_root))
    report = manager.create(
        template_id,
        vary=vary,
        seeds=seeds,
        jobs=jobs,
        horizon_s=horizon,
    )
    try:
        final = manager.wait(report["id"])
    finally:
        manager.close()
    print(
        json.dumps(
            {
                "experiment": final["id"],
                "status": final["status"],
                "runs": f"{final['completed']}/{final['total']} completed"
                + (f", {final['failed']} failed" if final["failed"] else ""),
                "path": str(out.resolve() / ".experiments" / final["id"]),
                "error": final["error"],
                "table": final["table"],
            },
            indent=2,
        ),
        flush=True,
    )
    if final["status"] != "completed":
        raise ValueError(f"Experiment {final['status']}: {final['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="aeroagentsim")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("scenario", type=Path)
    run.add_argument("--out", type=Path, default=Path("runs"))
    run.add_argument("--engine-profile", type=Path)
    run.add_argument("--provenance", choices=["lean", "full"])
    check = sub.add_parser("replay")
    check.add_argument("run", type=Path)
    metrics_command = sub.add_parser("metrics")
    metrics_command.add_argument("run", type=Path)
    serve = sub.add_parser("serve")
    serve.add_argument("--out", type=Path, default=Path("runs"))
    serve.add_argument("--scenario-root", type=Path, default=Path.cwd())
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8002)
    serve.add_argument("--frontend", type=Path)
    serve.add_argument(
        "--studio-root",
        type=Path,
        help="Studio workspace storage (default: <out>/.workspaces)",
    )
    demo = sub.add_parser("demo", help="Run or open a shipped demonstration")
    demo.add_argument("name", choices=["traffic-accident"])
    demo.add_argument(
        "--profile",
        choices=["kinematic", "sumo", "px4", "live-llm"],
        default="kinematic",
    )
    demo.add_argument("--headless", action="store_true")
    demo.add_argument("--port", type=int, default=0)
    demo.add_argument("--out", type=Path, default=Path("runs/demo"))
    demo.add_argument("--provenance", choices=["lean", "full"])
    templates = sub.add_parser("templates", help="List scenario templates")
    templates.add_argument("--templates-root", type=Path)
    sub.add_parser("plugins", help="List installed plugins and their contributions")
    run_template = sub.add_parser("run-template", help="Run or open a template")
    run_template.add_argument("template")
    run_template.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a template knob (repeatable)",
    )
    run_template.add_argument("--headless", action="store_true")
    run_template.add_argument("--out", type=Path, default=Path("runs/templates"))
    run_template.add_argument(
        "--port", type=int, default=0, help="Interactive API port (0 picks a free port)"
    )
    run_template.add_argument("--templates-root", type=Path)
    experiment = sub.add_parser(
        "experiment", help="Run a full-factorial template experiment"
    )
    experiment.add_argument("template")
    experiment.add_argument(
        "--vary",
        dest="varies",
        action="append",
        default=[],
        metavar="KNOB=VALUE,VALUE",
        help="Experiment axis: knob values to sweep (repeatable)",
    )
    experiment.add_argument(
        "--seeds", type=int, default=3, help="Matched seeds 0..seeds-1 per cell"
    )
    experiment.add_argument(
        "--jobs", type=int, default=1, help="Parallel worker processes"
    )
    experiment.add_argument(
        "--horizon",
        type=float,
        default=None,
        help="Simulated horizon in seconds (default: template duration)",
    )
    experiment.add_argument("--out", type=Path, default=Path("runs"))
    experiment.add_argument("--templates-root", type=Path)
    asset = sub.add_parser("assets", help="Manage optional detailed city packs")
    asset_sub = asset.add_subparsers(dest="asset_command", required=True)
    asset_sub.add_parser("list")
    asset_fetch = asset_sub.add_parser("fetch")
    asset_fetch.add_argument("--url", help="Download URL (or AEROAGENTSIM_ASSET_PACK_URL)")
    asset_fetch.add_argument("--insecure-skip-digest", action="store_true",
                            help="Explicitly skip archive SHA-256 verification / 明确跳过归档 SHA-256 校验")
    asset_install = asset_sub.add_parser("install")
    asset_install.add_argument("source", type=Path, metavar="ZIP-OR-DIR")
    asset_remove = asset_sub.add_parser("remove")
    asset_remove.add_argument("id", nargs="?", default="huangpu-city")
    asset_remove.add_argument("--version")
    args = parser.parse_args()
    if args.command == "assets":
        from aeroagentsim import assets

        def summary(pack: dict[str, Any]) -> dict[str, Any]:
            return {name: pack[name] for name in ("id", "version", "directory", "source")} | ({"title": pack["title"]} if "title" in pack else {})

        try:
            if args.asset_command == "list":
                result: Any = [summary(pack) for pack in assets.installed()]
            elif args.asset_command == "fetch":
                result = summary(assets.fetch(args.url, insecure_skip_digest=args.insecure_skip_digest))
            elif args.asset_command == "install":
                result = summary(assets.install(args.source))
            else:
                result = {"removed": assets.remove(args.id, args.version)}
            print(json.dumps(result, indent=2))
        except (ValueError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
    elif args.command == "templates":
        try:
            print(json.dumps(template_list(args.templates_root), indent=2))
        except (ValueError, FileNotFoundError, OSError) as exc:
            parser.error(str(exc))
    elif args.command == "plugins":
        from .plugins import plugin_inventory

        try:
            print(json.dumps(plugin_inventory(), indent=2))
        except (ValueError, FileNotFoundError, OSError) as exc:
            parser.error(str(exc))
    elif args.command == "run-template":
        try:
            run_template_command(
                args.template,
                sets=args.sets,
                headless=args.headless,
                port=args.port,
                out=args.out,
                templates_root=args.templates_root,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            FileNotFoundError,
            RuntimeError,
        ) as exc:
            parser.error(str(exc))
    elif args.command == "experiment":
        try:
            experiment_command(
                args.template,
                varies=args.varies,
                seeds=args.seeds,
                jobs=args.jobs,
                horizon=args.horizon,
                out=args.out,
                templates_root=args.templates_root,
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            FileNotFoundError,
            RuntimeError,
        ) as exc:
            parser.error(str(exc))
    elif args.command == "demo":
        from .demo import run_demo

        try:
            run_demo(
                profile=args.profile,
                headless=args.headless,
                port=args.port,
                out=args.out,
                provenance=args.provenance,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            parser.error(str(exc))
    elif args.command == "run":
        start = time.perf_counter()
        directory = args.out / f"{args.scenario.stem}-{uuid.uuid4().hex[:12]}"
        from aeroagentsim.scenario import load_scenario
        from aeroagentsim.scenario.profiles import apply_engine_profile

        scenario = load_scenario(args.scenario)
        if args.engine_profile is not None:
            scenario = apply_engine_profile(scenario, args.engine_profile)
        with RunSession(scenario, directory, provenance=args.provenance) as session:
            session.run()
            simulated = session.now_ns / 1e9
        elapsed = time.perf_counter() - start
        print(
            json.dumps(
                {
                    "run": str(directory),
                    "elapsed_wall_s": elapsed,
                    "simulated_s": simulated,
                    "rtf": simulated / elapsed,
                    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                }
            )
        )
    elif args.command == "metrics":
        from aeroagentsim.services.kpis import metrics

        print(json.dumps(metrics(args.run), sort_keys=True, indent=2))
    elif args.command == "replay":
        kernel = replay(args.run / "journal.jsonl")
        print(
            json.dumps(
                {
                    "run": str(args.run),
                    "cut": kernel.view().cut.index,
                    "ns": str(kernel.view().instant.ns),
                    "incomplete": kernel.incomplete,
                }
            )
        )
    else:
        try:
            import uvicorn

            from .app import create_app
        except ImportError as exc:
            parser.error(f"serve requires aeroagentsim[server]: {exc}")
        # The console's own origin feeds city URLs and the in-simulation camera.
        if "AEROAGENTSIM_CONSOLE_URL" not in os.environ:
            host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
            os.environ["AEROAGENTSIM_CONSOLE_URL"] = f"http://{host}:{args.port}"
        studio_root = args.studio_root or Path(
            os.environ.get("AEROAGENTSIM_STUDIO_ROOT") or args.out / ".workspaces"
        )
        serve_app(
            create_app(
                args.out,
                scenario_root=args.scenario_root,
                frontend=args.frontend,
                studio_root=studio_root,
            ),
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
