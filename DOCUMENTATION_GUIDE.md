# AeroAgentSim Documentation Guide

This guide maps the current documentation set to the developer tasks it supports.

## Main Entry Points

- [README.md](README.md): project overview, workbench scope, runtime layout, environment variables, and citation
- [README_CN.md](README_CN.md): Chinese overview and quick start
- [INSTALL.md](INSTALL.md): editable install, frontend setup, runtime storage, and troubleshooting
- [docs/README.md](docs/README.md): documentation hub
- [docs/index.rst](docs/index.rst): Sphinx home page
- [src/aeroagentsim/docs/en/architecture.md](src/aeroagentsim/docs/en/architecture.md): English architecture notes
- [src/aeroagentsim/docs/cn/architecture.md](src/aeroagentsim/docs/cn/architecture.md): Chinese architecture notes
- [src/aeroagentsim/examples/README.md](src/aeroagentsim/examples/README.md): runnable examples
- [src/aeroagentsim/examples/README_cn.md](src/aeroagentsim/examples/README_cn.md): 中文示例说明

## Documentation Themes

The current docs are organized around these developer questions:

- how to install and import AeroAgentSim for local development
- how to configure and launch the workbench
- how to understand the registry, config snapshot, and run directory layout
- how to use `Workflow Studio`, the relation graph, live runtime map, and trajectory review pages
- how preflight, validation, and run control behave
- how to run builtin examples and what external integrations they require

## Workbench Concepts That Should Stay Consistent

- `Overview`
- `Class Catalog`
- `Workflow Studio`
- `Run Console`
- `Trajectories & Logs`
- `Review / Validate`
- relation graph as an interactive inspection canvas
- canvas-local zoom and pan behavior
- node drag refinement for graph layout
- runtime preflight `warning` versus `error`
- config snapshots and per-run artifact directories

## Sphinx Docs

Build the Sphinx docs locally with:

```bash
pip install -e ".[dev,docs]"
cd docs
make html
```

Open `docs/_build/html/index.html` after the build completes.
