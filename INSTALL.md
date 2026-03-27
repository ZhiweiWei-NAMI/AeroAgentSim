# AeroAgentSim Installation Guide

This repository ships the AeroAgentSim developer workbench on top of the `airfogsim` Python package. The product name is `AeroAgentSim`, but installation and imports still use `airfogsim`.

## Requirements

- Python `>=3.8`
- `pip`
- Conda is recommended for the standard local environment name: `airfogsim`
- Node.js and npm only if you want to build or develop the frontend locally
- No OpenGL or 3D graphics stack is required for the current 2D workbench

## Recommended Environment Baseline

If you use the project-maintained Conda environment, activate it first:

```bash
conda activate airfogsim
```

The expected baseline for the current workbench and test flow is:

- `fastapi`
- `uvicorn`
- `pytest`
- `simpy`
- `playwright` for browser-level frontend validation

## Install From PyPI

```bash
python -m venv airfogsim_env
source airfogsim_env/bin/activate
pip install airfogsim
```

Verify the installation:

```bash
python -c "import airfogsim; from airfogsim import Environment, AirFogSimEnv; print('ok')"
```

`AirFogSimEnv` is a compatibility alias. New code should use `Environment`.

## Install From Source

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
python -m venv airfogsim_env
source airfogsim_env/bin/activate
pip install -e .[dev]
```

If you also want documentation tooling:

```bash
pip install -e ".[dev,docs]"
```

## Frontend Workbench Setup

The frontend is a React developer workbench focused on:

- class catalog inspection
- builtin and custom registry browsing
- workflow table/form editing
- workflow-agent-state coupling graph visualization with zoom/pan navigation
- global `Review / Validate` checks
- `zh-CN` / `en-US` UI switching
- run control
- live 2D map
- trajectories and logs

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

## Start the 2D Workbench

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

The workbench keeps 2D visualization and removes the old 3D page set.

The `Workflow Studio` relation graph supports zoom and drag-to-pan navigation. Configuration edits still happen through tables and forms; dragging the graph only changes the viewport.

Wheel and trackpad zoom are scoped to the graph canvas itself so graph inspection does not scroll the outer workbench page.

## Custom Registry Layout

Custom definitions are file-backed and should be stored under:

```text
registry/aeroagentsim/
├── agents/
├── tasks/
└── workflows/
```

Files are the primary source of truth for custom definitions. SQLite is used only for lightweight indexes, cache, and run references.

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

- Each config save produces an immutable config snapshot
- Each simulation launch produces a new `run_id`
- SQLite is used only for active-run cache and indexing

## API / Dependency Notes

The workbench backend depends on FastAPI and the existing visualization stack in this repository. If your local shell environment does not have `fastapi` installed yet, API startup checks will fail until dependencies are installed.

Similarly, automated Python tests require `pytest` and related test dependencies:

```bash
pip install pytest pytest-cov
```

Browser-level frontend validation also needs Python Playwright installed in the active environment:

```bash
pip install playwright
python -m playwright install chromium
```

The intended browser validation path assumes the `airfogsim` Conda environment is active before running backend, frontend, or Playwright commands.

## Review / Validate Workflow

The current workbench supports two validation entry points:

- page-local `Validate` in `Workflow Studio` for the current draft
- global `Review / Validate` aggregation for consistency, compatibility, unresolved references, and graph warnings

Draft validation is expected to run against the current unsaved or in-progress form state, not just the last stored config snapshot.

Runtime launch also performs `POST /api/configs/{config_id}/preflight` before `POST /api/runs`. Preflight warnings are surfaced in the UI and run diagnostics, but only preflight errors block run start.

## Troubleshooting

### ImportError for `AirFogSimEnv`

Use one of the supported imports:

```python
from airfogsim import Environment
from airfogsim import AirFogSimEnv
```

First check your installed version:

```bash
python -c "import airfogsim; print(airfogsim.__version__)"
```

The current source version is **1.1.1**. If your version is older or prints `0.0.0`, the PyPI release is outdated. Install from source instead:

```bash
pip install git+https://github.com/ZhiweiWei-NAMI/AirFogSim.git
```

Or clone and install in editable mode:

```bash
git clone https://github.com/ZhiweiWei-NAMI/AirFogSim.git
cd AirFogSim
pip install -e .
```

### Frontend cannot reach the backend

- confirm the backend port matches `REACT_APP_API_BASE_URL`
- confirm the WebSocket URL matches `REACT_APP_WS_BASE_URL`
- confirm FastAPI dependencies are installed before starting the workbench

### `runtime_preflight` reports skipped `create_airspace` / `create_frequency`

- these messages are currently non-blocking warnings, not startup failures
- they mean the active runtime did not expose the optional default resource injection helpers
- if your config explicitly defines airspaces or frequencies and the runtime still lacks those methods, preflight escalates to an error and run start is blocked

### Pause / resume / reset returns `409`

- `/api/runs/{run_id}/pause|resume|reset` only works for the active run
- historical runs remain inspectable and deletable, but they cannot be controlled after they are no longer active

### Legacy `/api/simulation/*` endpoints return `410`

- the older `/api/simulation/start|pause|resume|reset|configure` routes are intentionally disabled
- use `/api/configs/*` for draft/config operations and `/api/runs/*` for run lifecycle control

### Tests or API startup still fail locally

- install missing runtime dependencies from `requirements.txt` or the extras above
- install frontend dependencies with `npm install`
- install Playwright and browser binaries before browser tests
- rerun the verification command before starting the workbench
