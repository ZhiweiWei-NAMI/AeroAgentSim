"""Local execution, engine-free replay checking and optional API host."""

from __future__ import annotations

import argparse
import json
import resource
import time
import uuid
from pathlib import Path

from aerokernel.journal import replay

from aeroagentsim.platform.simulation import RunSession


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
    args = parser.parse_args()
    if args.command == "demo":
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
        from aeroagentsim.packs.metrics import metrics

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
        uvicorn.run(
            create_app(
                args.out, scenario_root=args.scenario_root, frontend=args.frontend
            ),
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
