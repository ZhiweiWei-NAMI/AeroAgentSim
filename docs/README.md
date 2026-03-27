# AeroAgentSim Documentation Hub

Welcome to the AeroAgentSim documentation hub. The product name is `AeroAgentSim`, while the Python package and import path remain `airfogsim`.

## Start Here

- [Project Overview](../README.md)
- [Chinese Overview](../README_CN.md)
- [Installation Guide](../INSTALL.md)
- [Documentation Guide](../DOCUMENTATION_GUIDE.md)

## Workbench-Oriented Docs

- [Getting Started](getting_started.html)
- [User Guide](user_guide.html)
- [API Reference](api/index.html)
- [Examples](examples.html)

## Technical Docs

- [English Architecture](../src/airfogsim/docs/en/architecture.md)
- [Chinese Architecture](../src/airfogsim/docs/cn/architecture.md)
- [Development Guide](../src/airfogsim/docs/en/development_guide.md)

## Current Visualization Model

The current frontend is a 2D developer workbench:

- no 3D page set
- no three.js dependency requirement
- global `zh-CN` / `en-US` UI switch
- workflow-agent-state coupling shown as a relation graph
- relation graph navigation supports zoom and pan inside the graph canvas
- page-local `Validate` and global `Review / Validate`
- form-driven configuration editing
- centralized `Review / Validate` checks for draft consistency
- builtin and custom definitions shown in a merged catalog view
- run control via REST
- live updates via WebSocket
- trajectory and log review by `run_id`

## Custom Definition Source

Custom `agent`, `task`, and `workflow` definitions are file-backed under:

```text
registry/aeroagentsim/
├── agents/
├── tasks/
└── workflows/
```

These files are the primary editable source. Database state is secondary and limited to indexing, cache, and run reference use cases.

## Runtime Layout

```text
runtime/aeroagentsim/
├── configs/
└── runs/
    └── <run_id>/
        ├── logs/
        ├── workflow_states/
        ├── trajectories/
        ├── spatial/
        └── metrics/
```

## Screenshot

![Workflow Studio relation graph](images/workflow-studio-relation-graph.png)

## Build Docs

```bash
conda activate airfogsim
pip install -e ".[docs]"
cd docs
make html
```
