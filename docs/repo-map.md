# Repo Map — Where Things Live · 代码地图

A guide for finding every functional module when searching or debugging. Paths are
relative to the repository root; all cited paths are verified to exist.

## Top-level tree · 顶层目录

| Path | What lives there |
| --- | --- |
| `aerokernel/` | The discrete-event simulation kernel, an independent package (`aerokernel/aerokernel`) with its own tests (`aerokernel/tests/kernel/`). |
| `src/aeroagentsim/` | The domain layer built on the kernel: scenario loading, engines, plugins, packs, services, agents. |
| `containers/` | Docker build/service bundles for external simulators: `px4-gazebo/`, `sumo/`, `ns3/` (each with `Dockerfile`, `service/`, `smoke.py`, `tests/`). |
| `frontend/` | The Studio/newcomer web console (React/TS). Pages under `frontend/src/`, Playwright e2e under `frontend/e2e/`. |
| `scenarios/` | Runnable authored scenarios, demos, adapters/agents/behaviours example YAML, and `scenarios/templates/` (see below). |
| `examples/plugins/` | External scenario-plugin package examples; start with the [scenario plugin guide](guides/scenario-plugins.md). |
| `tests/` | Python test suites mirroring `src/aeroagentsim` (`tests/platform/`, `tests/packs/`, `tests/adapters/`, `tests/agents/`, `tests/behaviours/`, `tests/authoring/`, `tests/observations/`, `tests/integrations/`, `tests/demos/`). |
| `tools/` | Developer tooling: `tools/docs/` (link checker, GIF/media recording), `tools/assets/` (city pack builder), `tools/scenarios/`, `tools/templates/`, `tools/demos/`. |
| `docs/` | Release documentation: `concepts/`, `guides/`, `reference/`, `getting-started/`, `examples/`, `media/`. |
| `registry/` | Local persisted registries written by runs/services (e.g. `registry/aeroagentsim/workflows/*.json`). Not source; rebuilt at runtime. |
| `runs/` | Run outputs: journals, checkpoints, artifacts. Runtime data, not source. |
| `constraints/` | Pinned dependency constraint files for install. |

## I want to … → look in …

| I want to … | Look in |
| --- | --- |
| Understand the discrete-event kernel: central barrier, private commits, scheduling, engine contracts | `aerokernel/aerokernel/coordinator.py` ("Serial central barrier, private commits and read-only deterministic replay"), `engine.py`, `scheduling.py` |
| Work with simulation time or RNG | `aerokernel/aerokernel/time.py` (integer time + pinned rational clock), `rng.py` (named partition streams) |
| Read/replay the journal (WAL) or do offline replay | `aerokernel/aerokernel/journal.py` (JSONL WAL + replay), `journal_codec.py`, `journal_stream.py`, `replay.py` |
| Use the kernel SDK, queues, transactions, registry of declarations | `aerokernel/aerokernel/sdk.py`, `queues.py`, `transactions.py`, `registry.py`, `state.py`, `values.py` |
| Load/validate a scenario YAML | `src/aeroagentsim/scenario/loader.py` ("Compile explicitly authored YAML into pinned registry and authority contracts"), plus `paths.py`, `profiles.py`, `subjects.py` |
| Understand behaviour packages: predicates, event chains, compilation | `src/aeroagentsim/behaviours/` (`compiler.py` compiles packages, `bindings.py` selects entities, `evaluation.py`, `schema.py`, `dispatch.py`) and the executor `src/aeroagentsim/engines/behaviour.py` |
| Predicate (AeroGraph) evaluation | `src/aeroagentsim/engines/predicate.py`, `predicate_ast.py` |
| Change low-altitude tasks / energy / airspace / rules | `src/aeroagentsim/packs/lowalt/`: `tasks.py`, `energy.py`, `airspace.py`, `rules.py` (shared `lowalt.rules` behaviour package), `core.py` (read-only committed snapshots), `everyday.py`, `city_traffic.py`, plus registry overlay `registry.overlay.yaml` |
| Work on the traffic-accident pack | `src/aeroagentsim/packs/traffic_accident/` (`decisions.py`, `traffic_assessment.py`, `road_motion.py`, `route_inputs.py`, `narratives.py`); scenario template `scenarios/templates/traffic-accident/`; tests `tests/demos/traffic_accident/` |
| Add or inspect an allocation/routing algorithm slot | `src/aeroagentsim/algorithms/` (`protocols.py` — immutable inputs/proposals, `builtins.py`, `llm.py`, `routes.py`, `metadata.py`) |
| Package templates and knob hooks | `src/aeroagentsim/plugin_api.py` (`ScenarioPlugin`, `TemplateSource`, API version and discovery); `src/aeroagentsim/scenarios/` (built-in registrations); `examples/plugins/` (external packages); [guide](guides/scenario-plugins.md), [contracts](reference/plugin-contracts.md) |
| Add a domain plugin / external-simulator adapter | `src/aeroagentsim/adapters/`: `px4_gazebo.py`, `sumo.py`, `ns3.py`, `container.py` (Docker lifecycle), `lockstep_base.py`, `runner.py`; plugin entry points in `src/aeroagentsim/platform/plugins.py` |
| Build or patch simulator containers | `containers/px4-gazebo/`, `containers/sumo/`, `containers/ns3/` (Dockerfiles, `service/`, `smoke.py`); guides `docs/guides/containers.md`, `px4-gazebo.md`, `sumo.md` |
| Find LangGraph agent code | `src/aeroagentsim/agents/`: `langgraph.py` (typed causal WAL decision engine), `langgraph_client.py`, `decision.py`, `dispatch.py`, `observation.py`, `tools.py` |
| HTTP API / REST+SSE host | `src/aeroagentsim/services/app.py`; per-run workers `worker.py`; storage `storage.py` |
| CLI entry points | `src/aeroagentsim/services/cli.py` (local run, engine-free replay check, API host); docs `docs/reference/cli.md` |
| Viewer feed / projections / KPIs / narratives / experiments | `src/aeroagentsim/services/`: `projector.py`, `feed_index.py` (SQLite cuts + checkpoints), `kpis.py`, `narratives.py`, `experiments.py` (full-factorial runner), `comparison.py`, `algorithm_catalog.py` |
| Studio authoring backend | `src/aeroagentsim/authoring/` (`api.py`, `scene.py`, `catalog.py`, `templates.py`, `workspace.py`, `explorer.py`, `traffic_graph.py`) |
| Asset / city packs | `src/aeroagentsim/assets.py` (pack discovery, stored outside the checkout), builder `tools/assets/build_pack.py`, sync `tools/sync_assets.py`; docs `ASSETS.md` |
| Scenario templates | `scenarios/templates/<id>/` (e.g. `city-day/`, `air-taxi/`, `traffic-accident/`) — each with `template.yaml`, `scenario.yaml`, `registry.snapshot.json`, behaviours; docs `docs/guides/templates.md` |
| Web workbench (the one UI) | `frontend/src/workbench/` (shell, configuration, gallery, objects, inspector, dock, records), reusing `frontend/src/viewport/` (3D stage), `frontend/src/scene/`, `frontend/src/behaviours/` (graphs, operations), `frontend/src/newcomers/` (KPIs, events, comparison, template API), `frontend/src/pages/` (inspection, AeroGraph overlay, agent console), `frontend/src/trace/`, `frontend/src/observations/` (photos, capture renderer), `frontend/src/studio/` (draft preview, rule editor, studio API), `frontend/src/feeds/` (viewer-feed HTTP + temporal store), `frontend/src/i18n/`; acceptance e2e in `frontend/e2e/workbench.spec.ts` |
| Frontend e2e tests | `frontend/e2e/workbench.spec.ts` (Playwright; `frontend/playwright.config.ts` starts a real backend and builds into a scratch dir); docs-media stories in `frontend/e2e/flows/` (`frontend/playwright.flows.config.ts`) |
| Python tests for a module | `tests/platform/` (services, kernels compat, predicates, platform), `tests/packs/` (lowalt, logistics, inspection), `tests/adapters/`, `tests/agents/`, `tests/behaviours/`, `tests/authoring/`, `tests/observations/`, `tests/integrations/`; kernel tests `aerokernel/tests/kernel/` |
| Documentation pages | `docs/concepts/` (aerograph, behaviours, plugins…), `docs/guides/` (per-adapter how-tos), `docs/reference/` (cli, http-api, scenario, viewer-feed), `docs/getting-started/`, `docs/examples/` |
| Media/GIF tooling | `tools/docs/record-docs-media.sh`, `make_gifs.py`, `prepare_recording.py`, `recording_server.py`; output lives in `docs/media/` |
| Registries/outputs written at runtime | `registry/` and `runs/` — regenerated data, safe to delete locally (e.g. `registry/aeroagentsim/workflows/` holds persisted custom plugin/workflow records) |

See also: [docs/README.md](README.md) for the full documentation index and
[CONTRIBUTING.md](../CONTRIBUTING.md) for how to extend the platform.
