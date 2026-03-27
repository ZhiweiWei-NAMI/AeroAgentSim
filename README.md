<a href="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9"><img src="https://joss.theoj.org/papers/3bf61975c569326131f0bf169bfe4db9/status.svg" alt="JOSS status"></a>
[![DOI](https://zenodo.org/badge/735258267.svg)](https://doi.org/10.5281/zenodo.15779000)

# AeroAgentSim

<div align="center">
  <img src="src/aeroagentsim/docs/img/logo.png" alt="AeroAgentSim Logo" width="300">
</div>

AeroAgentSim is a discrete-event simulation toolkit and developer workbench for low-altitude autonomous systems. It is designed for developers who need to configure workflows, run algorithmic experiments, inspect execution chains, and verify runtime behavior through trajectories, logs, and spatial snapshots.

The current repository, workbench, and source distribution use `AeroAgentSim` and `aeroagentsim`. Existing code can continue to import `airfogsim`, and the citation section below points to the JOSS paper published under the original `AirFogSim` title.

[中文版本](README_CN.md)

## Overview

- Recommended developer install path: `pip install -e .[dev]`
- Recommended Python import: `from aeroagentsim import Environment`
- Current workbench pages: `Overview`, `Class Catalog`, `Workflow Studio`, `Run Console`, `Trajectories & Logs`
- Runtime outputs are stored under `runtime/aeroagentsim/`
- Custom `agent` / `task` / `workflow` definitions are file-backed under `registry/aeroagentsim/`
- Builtin workbench workflows are aligned with runnable inspection, charging, logistics, and image-processing flows

## Developer Workbench

The AeroAgentSim workbench is built for configuration, validation, execution, and runtime verification:

1. `Overview`: current config version, recent run, and validation status
2. `Class Catalog`: builtin and custom metadata plus compatibility lookup
3. `Workflow Studio`: table/form editing with a workflow-agent-state relation graph
4. `Run Console`: start, pause, resume, reset, live logs, critical path, and live 2D map
5. `Trajectories & Logs`: run history, trajectory replay, and structured log inspection

The visualization layer is intentionally lightweight and developer-oriented:

- Leaflet-based 2D spatial views
- `simulation_plane` mode with `CRS.Simple`
- `geo_osm` mode with geographic coordinates
- live markers with workflow, task, status, and recent log context
- stored 2D trajectories rendered as polylines

`Workflow Studio` uses forms and tables as the persisted source of truth. The relation graph is an interactive inspection canvas with auto layout, wheel or trackpad zoom inside the canvas, background drag-to-pan, and node drag refinement for local layout adjustment.

![Workflow Studio relation graph](docs/images/workflow-studio-relation-graph.png)

The workbench also provides:

- global `zh-CN` / `en-US` UI switching
- page-local `Validate` for the active draft in `Workflow Studio`
- centralized `Review / Validate` checks for draft consistency and runtime readiness
- merged browsing across builtin and custom definitions
- structured log and trajectory inspection by `run_id`

## Registry And Runtime

Custom definitions are stored as files:

- `registry/aeroagentsim/agents/`
- `registry/aeroagentsim/tasks/`
- `registry/aeroagentsim/workflows/`

Runtime state is separated cleanly from configuration snapshots:

- config snapshots: `runtime/aeroagentsim/configs/`
- run directories: `runtime/aeroagentsim/runs/<run_id>/`
- typical run subdirectories: `logs/`, `workflow_states/`, `trajectories/`, `spatial/`, `metrics/`

Run start performs runtime preflight. Compatibility findings such as skipped default `create_airspace` or `create_frequency` injection remain `warning` entries. Only preflight `errors` block `POST /api/runs`.

## Developer Configuration

These environment variables are the main public knobs for local development and automation:

- Backend storage:
  - `AEROAGENTSIM_RUNTIME_DIR`
  - `AEROAGENTSIM_REGISTRY_DIR`
  - `AEROAGENTSIM_DB_PATH`
  - `AEROAGENTSIM_LOG_LEVEL`
- Compatibility aliases accepted by the backend:
  - `AIRFOGSIM_RUNTIME_DIR`
  - `AIRFOGSIM_REGISTRY_DIR`
  - `AIRFOGSIM_DB_PATH`
  - `AIRFOGSIM_LOG_LEVEL`
- Frontend endpoints:
  - `REACT_APP_API_BASE_URL`
  - `REACT_APP_WS_BASE_URL`
  - `REACT_APP_ENABLE_MOCK_FALLBACK`
- Example-specific integrations:
  - `OPENWEATHERMAP_API_KEY` for weather-backed examples
  - `SUMO_HOME` for SUMO traffic workflows

## Installation

For the current repository and workbench, use a source install:

```bash
python -m venv aeroagentsim_env
source aeroagentsim_env/bin/activate
pip install -e .[dev]
```

If you also want documentation tooling:

```bash
pip install -e ".[dev,docs]"
```

### Verify the package

```bash
python -c "import aeroagentsim, airfogsim; from aeroagentsim import Environment; from airfogsim import Environment as LegacyEnvironment; print(Environment.__name__, Environment is LegacyEnvironment)"
```

## Quick Example

```python
from aeroagentsim import Environment
from aeroagentsim.agent import DroneAgent
from aeroagentsim.component import ChargingComponent, MoveToComponent
from aeroagentsim.workflow.inspection import create_inspection_workflow

env = Environment()

drone = env.create_agent(
    DroneAgent,
    "drone1",
    properties={
        "position": [10, 10, 0],
        "battery_level": 100,
    },
)

drone.add_component(MoveToComponent(env, drone))
drone.add_component(ChargingComponent(env, drone))

workflow = create_inspection_workflow(
    env,
    drone,
    [
        (10, 10, 50),
        (100, 40, 80),
        (180, 120, 60),
        (10, 10, 0),
    ],
)

workflow.start()
env.run(until=600)
```

## Start The Workbench

```bash
python main_for_visualization.py --backend-port 8002 --frontend-port 3000
```

## API Surface

The current workbench API exposes these main groups:

- `GET /api/catalog/agents|components|tasks|workflows`
- `GET /api/catalog/compatibility`
- `GET/POST /api/registry/{kind}`
- `GET/PUT/DELETE /api/registry/{kind}/{definition_id}`
- `POST /api/registry/{kind}/{definition_id}/validate`
- `GET/PUT /api/configs/{config_id}`
- `GET/POST /api/configs/{config_id}/graph`
- `POST /api/configs/{config_id}/preflight`
- `POST /api/configs/{config_id}/validate`
- `GET /api/health`
- `POST /api/runtime/reset`
- `GET /api/runs`
- `POST /api/runs`
- `POST /api/runs/{run_id}/pause|resume|reset`
- `DELETE /api/runs/{run_id}`
- `GET /api/runs/{run_id}/status|logs|trajectories|spatial`

## Documentation

- [Installation Guide](INSTALL.md)
- [Documentation Guide](DOCUMENTATION_GUIDE.md)
- [Documentation Hub](docs/README.md)
- [System Architecture](src/aeroagentsim/docs/en/architecture.md)

## Citation

If you use AeroAgentSim in research, cite the JOSS paper:

- JOSS page: <https://joss.theoj.org/papers/10.21105/joss.08267>
- PDF: <https://www.theoj.org/joss-papers/joss.08267/10.21105.joss.08267.pdf>

```bibtex
@article{Wei2025,
  doi = {10.21105/joss.08267},
  url = {https://doi.org/10.21105/joss.08267},
  year = {2025},
  publisher = {The Open Journal},
  volume = {10},
  number = {111},
  pages = {8267},
  author = {Wei, Zhiwei and Li, Bing and Zhang, Rongqing},
  title = {AirFogSim: A Python Package for Benchmarking Collaborative Intelligence in Low-Altitude Vehicular Fog Computing},
  journal = {Journal of Open Source Software}
}
```
