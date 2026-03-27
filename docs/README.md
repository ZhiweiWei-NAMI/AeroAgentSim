# AeroAgentSim Documentation Hub

Welcome to the AeroAgentSim documentation hub.

## Start Here

- [Project Overview](../README.md)
- [Chinese Overview](../README_CN.md)
- [Installation Guide](../INSTALL.md)
- [Documentation Guide](../DOCUMENTATION_GUIDE.md)

## Workbench Guides

- [Getting Started](getting_started.html)
- [User Guide](user_guide.html)
- [Guides Index](guides/index.html)
- [API Reference](api/index.html)
- [Examples](examples.html)
- [Contributing](contributing.html)

## Technical References

- [English Architecture](../src/aeroagentsim/docs/en/architecture.md)
- [Chinese Architecture](../src/aeroagentsim/docs/cn/architecture.md)
- [Examples README](../src/aeroagentsim/examples/README.md)

## Current Workbench Scope

The current frontend is a developer workbench for:

- configuration editing
- relation graph inspection
- validation and preflight review
- run control
- live 2D runtime monitoring
- stored trajectory and log analysis

The relation graph is an interactive inspection canvas with auto layout, zoom inside the graph canvas, background pan, and node drag refinement. Persisted config continues to come from the forms and tables.

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
pip install -e ".[dev,docs]"
cd docs
make html
```
