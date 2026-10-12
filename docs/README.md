# AeroAgentSim documentation

AeroAgentSim is a general-purpose simulation platform for experiments involving physical systems, networks, environmental conditions and decision-making agents. AeroGraph types describe the world; replaceable domain plugins evolve it through one shared discrete-event runtime. The console configures experiments and shows their graph and 3D views on the same timeline.

Start in the workbench gallery: open the guide of parcel / medical delivery, inspection, spraying, air taxi, patrol, traffic-accident response, food delivery or the mixed city day, then create a project. The committed registry snapshots and procedural city work without the AeroGraph repository or a separate mesh pack.

After [installation](getting-started/install.md), `aeroagentsim templates` lists all nine templates. `aeroagentsim run-template parcel-delivery` opens the project in the workbench; choose **Validate → Save version → Initialize → Start**, or add `--headless` to run directly in the terminal. `aeroagentsim assets fetch` installs the optional Huangpu city pack 1.1 from release tag `assets-huangpu-city-v1.1`; reload the viewer to use it.

## Get started

- [Install](getting-started/install.md)
- [Run a template and tour the console](getting-started/quickstart.md)
- [Create your first scenario](getting-started/first-scenario.md)
- [Traffic-accident walkthrough](examples/traffic-accident.md)
- [Many businesses in one city](examples/city-day.md)
- [Repo map — where things live](repo-map.md)

## Concepts

- [Architecture](concepts/architecture.md): shared time, ingress and committed state
- [AeroGraph](concepts/aerograph.md): types, fields, relations and snapshots
- [Predicates](concepts/predicates.md)
- [Behaviours and event chains](concepts/behaviours.md)
- [Domain plugins and field ownership](concepts/plugins.md)
- [Agents](concepts/agents.md)
- [Graph and 3D views](concepts/views.md)
- [Runs, replay and provenance](concepts/runs.md)
- [Design notes](design-notes.md)

## Guides

| Task | Guide |
| --- | --- |
| Build a scenario by describing it | [Build a scenario in conversation](guides/assistant.md) |
| Connect a flight simulator | [PX4/Gazebo](guides/px4-gazebo.md) |
| Connect road traffic | [SUMO](guides/sumo.md) |
| Configure environmental forcing | [Weather](guides/weather.md) |
| Define a condition | [Write a predicate](guides/predicates.md) |
| Define a workflow | [Author an event chain](guides/behaviours.md) |
| Change battery, airspace or wind policy | [Edit the low-altitude rules](guides/lowalt-rules.md) |
| Compare allocation algorithms | [Matched-seed experiments](guides/experiments.md) |
| Publish a reusable scenario | [Make a template](guides/templates.md) |
| Provide external input | [Inject external events](guides/external-events.md) |
| Use a model-backed agent | [Deploy an agent](guides/agents.md) |
| Inspect a result | [Visualization and inspection](guides/visualization.md) |
| Extend a domain | [Write a plugin](guides/plugins.md) |
| Run simulator services | [Containers](guides/containers.md) |

## Reference

- [Scenario YAML](reference/scenario.md)
- [Behaviour packages](reference/behaviour-package.md)
- [Predicate dialect](reference/predicate-dialect.md)
- [HTTP API](reference/http-api.md)
- [Command line](reference/cli.md)
- [Viewer feed](reference/viewer-feed.md)

See [Contributing](../CONTRIBUTING.md) for development and the [changelog](../CHANGELOG.md) for release changes.
