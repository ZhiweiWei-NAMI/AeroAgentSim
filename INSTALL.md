# AeroAgentSim Installation Guide

This guide focuses on the current developer workflow for the AeroAgentSim repository, examples, and workbench.

## Requirements

- Python `>=3.8`
- `pip`
- Node.js and npm for frontend development
- `pytest` for local verification
- `playwright` and Chromium for browser smoke tests
- `SUMO_HOME` only if you run SUMO-backed traffic examples
- `OPENWEATHERMAP_API_KEY` only if you run weather-backed examples

## Recommended Developer Environment

Create and activate a fresh environment:

```bash
python -m venv aeroagentsim_env
source aeroagentsim_env/bin/activate
```

Install the repository in editable mode:

```bash
pip install -e .[dev]
```

If you build the docs locally as well:

```bash
pip install -e ".[dev,docs]"
```

## Verify Python Imports

```bash
python -c "import aeroagentsim, airfogsim; from aeroagentsim import Environment; from airfogsim import Environment as LegacyEnvironment; print(Environment.__name__, Environment is LegacyEnvironment)"
```

## Frontend Workbench Setup

Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

Build the frontend:

```bash
cd frontend
npm run build
cd ..
```

## Start The Workbench

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

The workbench is organized around:

- `Overview`
- `Class Catalog`
- `Workflow Studio`
- `Run Console`
- `Trajectories & Logs`

`Workflow Studio` combines table and form editing with an interactive relation graph. The graph supports wheel or trackpad zoom inside the canvas, background drag-to-pan, and node drag refinement. Persisted configuration still comes from the tables and forms.

## Runtime Storage

The current runtime layout is:

```text
runtime/aeroagentsim/
├── configs/
│   └── cfg_<timestamp>_<id>.json
└── runs/
    └── run_<timestamp>_<id>/
        ├── logs/
        ├── workflow_states/
        ├── trajectories/
        ├── spatial/
        └── metrics/
```

Each config save produces an immutable snapshot. Each simulation launch creates a new `run_id`.

## Custom Registry Layout

Custom definitions are file-backed and stored under:

```text
registry/aeroagentsim/
├── agents/
├── tasks/
└── workflows/
```

These files are the editable source of truth. SQLite is used as an index and active-run cache.

## Environment Variables

Backend storage and logging:

- `AEROAGENTSIM_RUNTIME_DIR`
- `AEROAGENTSIM_REGISTRY_DIR`
- `AEROAGENTSIM_DB_PATH`
- `AEROAGENTSIM_LOG_LEVEL`

Compatibility aliases still accepted by the backend:

- `AIRFOGSIM_RUNTIME_DIR`
- `AIRFOGSIM_REGISTRY_DIR`
- `AIRFOGSIM_DB_PATH`
- `AIRFOGSIM_LOG_LEVEL`

Frontend connectivity:

- `REACT_APP_API_BASE_URL`
- `REACT_APP_WS_BASE_URL`
- `REACT_APP_ENABLE_MOCK_FALLBACK`

External integrations:

- `OPENWEATHERMAP_API_KEY`
- `SUMO_HOME`

## Browser Validation

Install Playwright in the active Python environment:

```bash
pip install playwright
python -m playwright install chromium
```

## Review / Validate Workflow

The workbench exposes two validation entry points:

- page-local `Validate` in `Workflow Studio`
- global `Review / Validate` for draft consistency, compatibility checks, and runtime readiness

Runtime launch also runs `POST /api/configs/{config_id}/preflight` before `POST /api/runs`. Preflight `warning` entries remain visible and non-blocking. Preflight `errors` block run start.

## Troubleshooting

### Backend or frontend does not start

- confirm the backend port matches `REACT_APP_API_BASE_URL`
- confirm the WebSocket URL matches `REACT_APP_WS_BASE_URL`
- confirm FastAPI, Uvicorn, and frontend dependencies are installed

### `runtime_preflight` reports skipped `create_airspace` / `create_frequency`

- these are compatibility warnings
- they indicate that the active runtime did not expose the optional default resource bootstrap helpers
- explicit config requirements still escalate to an error when the runtime cannot satisfy them

### Pause / resume / reset returns `409`

- `/api/runs/{run_id}/pause|resume|reset` applies only to the active run
- historical runs remain inspectable and deletable

### Legacy `/api/simulation/*` endpoints return `410`

- use `/api/configs/*` for config operations
- use `/api/runs/*` for run lifecycle control

### Tests or API startup still fail locally

- reinstall the editable package: `pip install -e .[dev]`
- reinstall frontend dependencies: `cd frontend && npm install`
- install Playwright and Chromium before browser tests
