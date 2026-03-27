# AeroAgentSim Documentation Guide

This guide explains where the main documentation now lives.

`AeroAgentSim` is the outward-facing product name. The technical package name and import path remain `airfogsim`.

## Documentation Entry Points

- [README.md](README.md): product overview, quick install, and workbench summary
- [README_CN.md](README_CN.md): Chinese overview and quick start
- [INSTALL.md](INSTALL.md): installation, 2D workbench setup, and runtime layout
- [docs/README.md](docs/README.md): documentation hub
- [docs/index.rst](docs/index.rst): Sphinx documentation home
- [src/airfogsim/docs/en/architecture.md](src/airfogsim/docs/en/architecture.md): English technical architecture
- [src/airfogsim/docs/cn/architecture.md](src/airfogsim/docs/cn/architecture.md): Chinese technical architecture

## What Changed

- 3D frontend pages were removed
- documentation now describes the 2D developer workbench
- config snapshots and `run_id` artifacts are documented explicitly
- top-level imports now recommend `from airfogsim import Environment`
- `AirFogSimEnv` is documented as a compatibility alias
- the file-based custom registry model is now part of the documented architecture
- the bilingual `zh-CN` / `en-US` frontend behavior is part of the documented workbench flow
- the centralized `Review / Validate` path is part of the documented operator workflow

## Workbench Concepts To Document Consistently

- `Overview`
- `Class Catalog`
- `Workflow Studio`
- `Run Console`
- `Trajectories & Logs`
- `Review / Validate`
- workflow-agent-state relation graph navigation inside the graph canvas
- runtime preflight warnings vs blocking errors

- 2D map modes:
  - `simulation_plane`
  - `geo_osm`

- registry source-of-truth:
  - `registry/aeroagentsim/agents/`
  - `registry/aeroagentsim/tasks/`
  - `registry/aeroagentsim/workflows/`

- runtime storage:
  - `runtime/aeroagentsim/configs/`
  - `runtime/aeroagentsim/runs/<run_id>/`

- language support:
  - `zh-CN`
  - `en-US`

## Sphinx Docs

If you build the Sphinx docs locally:

```bash
pip install -e ".[docs]"
cd docs
make html
```

Open `docs/_build/html/index.html` after the build completes.
