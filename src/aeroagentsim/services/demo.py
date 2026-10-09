"""Public-clone entry point for the committed traffic accident scenario."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
import webbrowser
from pathlib import Path
from typing import Any

import yaml
from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records

from aeroagentsim.authoring.demo import configure_console, live_decisions
from aeroagentsim.authoring.templates import demo_source
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader


def browser_path(modules: Path) -> str:
    """Use an explicit browser, else the Playwright-selected installed Chromium."""
    configured = os.environ.get("AEROAGENTSIM_CHROMIUM")
    if configured:
        if not Path(configured).is_file():
            raise FileNotFoundError(
                f"AEROAGENTSIM_CHROMIUM: browser missing: {configured}"
            )
        return configured
    # Let Playwright choose its platform-specific cache or the operator override.
    environment = {**os.environ, "PLAYWRIGHT_SKIP_BROWSER_GC": "1"}
    script = (
        "const {pathToFileURL}=require('node:url');"
        "import(pathToFileURL(process.argv[1]).href).then(p=>console.log(p.chromium.executablePath()))"
    )
    module = modules / "playwright/index.mjs"
    if not module.is_file():
        raise FileNotFoundError(
            "Demo capture needs Playwright: set AEROAGENTSIM_NODE_MODULES, "
            "install frontend dependencies, or allow the first-use npm bootstrap"
        )
    result = subprocess.run(
        ["node", "-e", script, str(module)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    path = result.stdout.strip()
    if not Path(path).is_file():
        subprocess.run(
            ["node", str(modules / "playwright/cli.js"), "install", "chromium"],
            check=True,
            env=environment,
            stdout=sys.stderr,
        )
    if not Path(path).is_file():
        raise FileNotFoundError(
            f"Playwright Chromium is missing after installation: {path}"
        )
    return path


def demo_document(
    profile: str = "kinematic", *, standalone: bool = False
) -> tuple[dict[str, Any], Path]:
    source = demo_source("traffic-accident")
    document = yaml.load((source / "scenario.yaml").read_bytes(), Loader=UniqueLoader)
    if not isinstance(document, dict):
        raise TypeError("Demo scenario must be a mapping")
    if profile in {"sumo", "px4"}:
        native = yaml.load(
            (source / "profiles" / f"{profile}.yaml").read_bytes(), Loader=UniqueLoader
        )
        requirements = "; ".join(native["configuration_required"])
        raise ValueError(
            f"{profile} has a configuration contract, not a runnable "
            "replacement yet. Required: "
            f"{requirements}. "
            "Use --profile kinematic for the shipped demo."
        )
    configure_console(document, source, primitive=standalone)
    if standalone:
        # Standalone capture uses shipped OSM footprints, without a console server.
        capture = document["engines"]["capture"]["config"]
        capture["renderer"]["asset_digest"] = "traffic-lite-city/v1"
        document["engines"]["capture_bridge"]["config"]["asset_digest"] = (
            "traffic-lite-city/v1"
        )
    renderer = document["engines"]["capture"]["config"]["renderer"]
    renderer["browser_executable"] = browser_path(Path(renderer["node_modules"]))
    if profile == "live-llm":
        names = (
            "AEROAGENTSIM_LLM_BASE_URL",
            "AEROAGENTSIM_LLM_MODEL",
            "AEROAGENTSIM_LLM_API_KEY_ENV",
        )
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise ValueError(
                "live-llm requires " + ", ".join(missing) + " and the langgraph extra"
            )
        live_decisions(
            document,
            {
                "base_url": os.environ[names[0]],
                "model": os.environ[names[1]],
                "api_key_env": os.environ[names[2]],
            },
        )
    return document, source


def outcome(directory: Path) -> dict[str, Any]:
    events = {
        "traffic.incident.activated": "accident",
        "traffic.award.committed": "award",
        "traffic.capture.stored": "capture",
        "traffic.capture.accepted": "upload",
    }
    found: dict[str, dict[str, Any]] = {}
    for raw in iter_records(directory / "journal.jsonl"):
        record = expand_record(raw)
        for item in record.get("items", []):
            if "message" not in item or "proposal" not in item:
                continue
            message = decode_record(item["message"])
            if message.schema_id in events:
                label = events[message.schema_id]
                if label not in found:
                    found[label] = {"event": label, "simulated_s": message.at.ns / 1e9}
    if set(found) != set(events.values()):
        raise RuntimeError(
            "Demo did not complete the event chain; observed: " + ", ".join(found)
        )
    metadata = json.loads((directory / "manifest.json").read_text())
    return {
        "status": metadata["status"],
        "run": str(directory),
        "journal": str(directory / "journal.jsonl"),
        "events": list(found.values()),
    }


def run_demo(
    *,
    profile: str,
    headless: bool,
    port: int,
    out: Path,
    provenance: str | None = None,
) -> None:
    repository = Path(__file__).resolve().parents[3]
    frontend = repository / "frontend/dist"
    if not headless and not (frontend / "index.html").is_file():
        raise FileNotFoundError(
            "Frontend build missing. Run: cd frontend && npm ci && npm run build"
        )
    if headless:
        document, source = demo_document(profile, standalone=True)
        directory = out.resolve() / ("traffic-accident-" + uuid.uuid4().hex[:12])
        with RunSession(
            load_scenario(document, base=source), directory, provenance=provenance
        ) as session:
            session.run()
        print(json.dumps(outcome(directory)), flush=True)
        return
    if port == 0:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    console = f"http://127.0.0.1:{port}"
    os.environ["AEROAGENTSIM_CONSOLE_URL"] = console
    from .app import create_app

    app = create_app(
        out.resolve() / "runs",
        scenario_root=repository,
        frontend=frontend,
        studio_root=out.resolve() / "workspaces",
    )
    store = app.state.studio
    draft = store.create("Traffic accident")
    draft = store.import_demo(
        draft["id"], "traffic-accident", console=True, operator_injection=False
    )
    document, _ = demo_document(profile)
    # Workspace copies keep relative package/snapshot paths local to that draft.
    draft["scenario"]["engines"] = document["engines"]
    if provenance is not None:
        draft["scenario"]["provenance"] = provenance
    store.save(draft["id"], {"scenario": draft["scenario"]})
    url = console + "/studio?workspace=" + draft["id"]
    print(
        json.dumps({"url": url, "workspace": draft["id"], "profile": profile}),
        flush=True,
    )
    webbrowser.open(url)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port)
