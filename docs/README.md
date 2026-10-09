# AeroAgentSim documentation

AeroAgentSim is a general-purpose simulation platform for experiments involving physical systems, networks, environmental conditions and decision-making agents. AeroGraph types describe the world; replaceable domain plugins evolve it through one shared discrete-event runtime. The console configures experiments and shows their graph and 3D views on the same timeline.

Start with the traffic-accident experiment. Its committed registry snapshot and procedural city work without the AeroGraph repository or a separate mesh pack.

## Get started

- [Install](getting-started/install.md)
- [Run the demo and tour the console](getting-started/quickstart.md)
- [Create your first scenario](getting-started/first-scenario.md)
- [Traffic-accident walkthrough](examples/traffic-accident.md)

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
| Connect a flight simulator | [PX4/Gazebo](guides/px4-gazebo.md) |
| Connect road traffic | [SUMO](guides/sumo.md) |
| Configure environmental forcing | [Weather](guides/weather.md) |
| Define a condition | [Write a predicate](guides/predicates.md) |
| Define a workflow | [Author an event chain](guides/behaviours.md) |
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
