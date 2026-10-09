# Contributing

Changes should make experiments easier to configure, run and understand. Describe the concrete behaviour being changed, include a small reproducible example and test its observable outcome.

## Development setup

Use Python 3.10+ and Node.js 22. From an activated virtual environment:

```bash
python -m pip install -e ./aerokernel -e '.[server,agents,dev]' -c constraints/dev.txt
(cd frontend && npm ci)
```

See [Install](docs/getting-started/install.md) for console builds and Chromium dependencies. Simulator containers and live model endpoints are optional.

## Checks

```bash
python -m ruff check src/aeroagentsim
python -m mypy --strict src/aeroagentsim
python -m pytest -q tests -m 'not docker and not llm and not slow'
python tools/docs/check_links.py
(cd frontend && npm run typecheck && npm test && npm run build)
```

Tests using the full AeroGraph catalog require `AEROAGENTSIM_AEROGRAPH_ROOT` pointing to a checkout you can access. Snapshot-only examples do not. Docker tests require their actual services; `llm` tests need a model endpoint. Run the slow public demo test when changing its event chain, physical owners, rendering or CLI:

```bash
python -m pytest -q tests/demos/traffic_accident/test_public_demo.py
```

Choose targeted tests while developing and run the relevant broader checks once the change is ready. Report what ran and any external dependencies that prevented checks.

## Add a plugin

Start with [Write a domain plugin](docs/guides/plugins.md). Define which fields the plugin owns, its command and event schemas, its time/ingress contract and its entry point. Provide a small committed scenario and tests for meaningful state changes, command receipts and failure behaviour. A replacement owner must start in a new run; two plugins cannot write the same field in one run.

Live inference belongs in a decision plugin. Keep credentials in named environment variables and expose observation limits and inference timing explicitly. Missing services must remain visible errors rather than synthesized outcomes.

## Style and documentation

Use typed Python and explicit data contracts. The kernel stays pure standard-library at runtime. Keep domain assumptions inside their plugins. Name units in configuration, use integer nanoseconds for runtime time and distinguish simulated time from wall time.

Update the relevant page under [docs](docs/README.md) when changing public configuration, CLI or HTTP contracts. Label YAML fragments, link to runnable complete examples and avoid machine-specific paths. The link checker checks the docs tree and its companion root pages, including kernel links under `aerokernel/`.

Submit a focused change with its purpose, user-visible effect, validation and practical limitations. Preserve concurrent work in shared checkouts.
