# AeroAgentSim

AeroAgentSim is a general-purpose, ontology-driven simulation platform. It hosts
arbitrary entity types, typed states, relations, predicates and events through an
independent **aerokernel** package. Discrete-event workflows, fixed-step models,
lockstep external simulators and optional real-time pacing share committed state
and integer simulation time. Aircraft, traffic, radio networks and business
records are scenario content; the kernel has no drone-specific assumptions.

AeroAgentSim brings adapters, agent tools, authoring and visualization together
on the shared kernel.

```text
AeroGraph persisted types / fields / relations / predicate definitions
                  │ read-only compiler + pinned registry snapshot
                  ▼
Scenario YAML ──► Simulation / RunSession ──► independent aerokernel
                  │ explicit writer, lifecycle and clock bindings
                  ├─ engines: kinematic / workflow / records / predicates
                  ├─ adapters: PX4+Gazebo / SUMO / ns-3
                  ├─ domain packs: logistics / inspection
                  └─ decision engines: authorized projections → typed commands
                             │ atomic commits + action receipts
                             ▼
                      journal / run storage
                             │ engine-free replay + committed feed
                             ▼
                 CLI / HTTP + SSE / Three.js viewer / Studio
```

The kernel owns scheduling, state history and action receipts. A field has one
authorized writer; business and physical engines can own different fields of the
same entity. Viewer refresh does not advance simulation time. Missing observations
remain absent, and native/model errors remain visible.

## Quickstart

Use Python 3.10+ (tested with 3.11) and a compatible sibling `../aerokernel`
checkout. From this repository:

```bash
python -m venv --symlinks .venv
source .venv/bin/activate
pip install -e ../aerokernel -e '.[server]'
export AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph
aeroagentsim run scenarios/p1-slice.yaml --out runs
# Use the exact directory printed by run:
aeroagentsim replay runs/<printed-run-directory-name>
aeroagentsim serve --help
```

With dependencies and the matching AeroGraph source already available, the
22-second kinematic slice runs in under two minutes on the measured host (8.02 s
on a clean install). Installation/download time is separate. `p1-slice.yaml` uses the explicit
`AEROAGENTSIM_AEROGRAPH_ROOT` source input and pins its predicate source hash.
Keep that hash matched to the reviewed source; do not replace it with mock data. [INSTALL.md](INSTALL.md) explains snapshots, source layout
and a portable snapshot-based logistics example.

New Python code uses:

```python
from aeroagentsim import Simulation
from aeroagentsim.scenario import load_scenario

sim = Simulation(load_scenario("scenarios/p1-slice.yaml"))
try:
    sim.start()
    view = sim.run_until(1_000_000_000)
    print(view.instant.ns)
finally:
    sim.close()
```

## Native simulators and agents

PX4/Gazebo, SUMO and ns-3 run inside separate images. Their host adapters use
explicit clock/field bindings and actual native acknowledgments and outcomes.
The checked-in examples select `aeroagentsim/px4-gazebo:dev-p2b`,
`aeroagentsim/sumo:dev-p3a-5` and `aeroagentsim/ns3:dev-p4b`; these are measured
local tags, not a promise that a public registry hosts them. Build instructions
and requirements are in the [PX4](containers/px4-gazebo/README.md),
[SUMO](containers/sumo/README.md) and [ns-3](containers/ns3/README.md) guides.

```bash
python -m aeroagentsim.adapters.runner scenarios/adapters/px4-flight.yaml \
  --containers --flight --journal /tmp/flight-new.jsonl
python -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml \
  --containers --journal /tmp/traffic-new.jsonl
python -m aeroagentsim.adapters.runner scenarios/adapters/coupled.yaml \
  --containers --journal /tmp/coupled-new.jsonl
```

Use new journal paths. An installed distribution also registers `px4_gazebo`,
`sumo` and `ns3` factories for ordinary `Simulation` construction. The adapter
runner adds managed container lifecycle and the flight command sequence.
See the [container guide](docs/guides/containers.md) and the
[PX4](docs/guides/px4-gazebo.md) and [SUMO](docs/guides/sumo.md) guides for
lag, latching, freshness and available commands. The example coupled run does not model UAV–road-actor
contacts. Native adapter cancellation remains unsupported.

LLM decisions use a stdlib OpenAI-compatible HTTP provider and typed, scoped
tools; the kernel contains no model client. Configure the endpoint/model in a
copy of `scenarios/agents/llm-dispatch.yaml`, then run it with `aeroagentsim run`.
The supplied scenario uses a local endpoint on port 8788. Optional credentials
come from environment variables, never sealed benchmark credential files.
Offline decisions hold simulation time; replay makes zero model calls. See the
[agent guide](docs/guides/agents.md) for configuration and policy.

## Viewer and Studio

```bash
cd frontend
npm ci
npm run build
cd ..
aeroagentsim serve --out runs --scenario-root . --frontend frontend/dist
```

Open `http://127.0.0.1:8002/runs`. Inspect arbitrary entities, commands, receipts,
relations and stamped facts in live or replay mode. The Three.js viewer uses
display interpolation; inspectors retain exact recorded values. The decision
console is `/agents/<run-id>?mode=replay`. During `npm run dev`, `/viewer-demo` is explicitly authored
demo content. Optional city assets require verified source/mesh packs.

Open `/studio` for workspace authoring. With `serve`, set
`AEROAGENTSIM_STUDIO_ROOT` to a workspace directory to enable those endpoints.
The shipped demo snapshot supplies its type catalog;
`AEROAGENTSIM_AEROGRAPH_ROOT` optionally selects a full source catalog.
See the [quickstart](docs/getting-started/quickstart.md) and
[visualization guide](docs/guides/visualization.md).

## Engines and measured performance

| Engine / workload | Simulation | Wall time | RTF | Evidence / limits |
| --- | ---: | ---: | ---: | --- |
| Kinematic + workflow slice (5 movers, 10 orders) | 22 s | 8.02 s | 2.74 | Clean install; compile/run/index/close, flush WAL; replay complete. |
| PX4/Gazebo arm → takeoff → goto → land | 27.2 s | 6.42 s | 4.24 | Component-job real container run; startup 13.77 s excluded; replay passed. |
| SUMO, 50 vehicles | 60 s | 24.47 s | 2.45 | Component-job real container run; startup 2.22 s excluded; replay passed. |
| PX4 + SUMO + ns-3, 29 packet deliveries | 30 s | 19.22 s | 1.56 | Component-job real container run; startup 16.99 s excluded; replay passed. |
| ns-3 standalone, 5 nodes / 2,400 datagrams | 61 s | 4.18 s | 14.58 | Native radio run: 430 deliveries, 1,970 application timeouts. |

RTF means simulated seconds divided by wall seconds. Native adapter measurements
include coordination/journal work after reset and exclude startup; the clean-install
CLI timing includes registry compilation, bootstrap, indexing and close. These
single-host workloads are not comparable fidelity benchmarks or fleet scaling
guarantees. Values come from the [adapter measurements](tests/adapters/measurements.json),
the ns-3 smoke record under `containers/ns3/` and the
[platform verification record](docs/concepts/architecture.md). The earlier
1,000-entity run is recorded separately in
[platform measurements](tests/platform/measurements.json).

## AeroGraph integration

The compiler reads persisted AeroGraph definitions across seven directories
(970 entity types in the surveyed source) and preserves type ancestry, value
schemas, units/frames, review disposition, temporal metadata and provenance.
It compiles selected slices into pinned registries; source existence or admission
does not imply production approval or executable behavior. Scenario bindings
supply writer and lifecycle authority. No AeroGraph builder or imported runtime
is required. See [AeroGraph and registry compilation](docs/concepts/aerograph.md).

The platform supports nonspatial records and spectrum/constraint entities as
well as vehicles. `scenarios/p1-spectrum.yaml` demonstrates position-free types.
The current threshold evaluator handles explicit comparison ASTs and sampled
contexts; a complete arbitrary AeroGraph evaluator remains pending.

## Status and limits

Acceptance records for adapters and agent decisions are under `tests/adapters`
and `tests/agents`. Consult the runnable scenarios and their declared profiles
when selecting an experiment.

Kinematic energy/motion and inspection geometry are authored models, not
calibrated vehicle/sensor measurements. ns-3 currently models a shared-medium,
single-radio ad-hoc profile. MAVSDK source time is unavailable for some cached
telemetry and is labeled accordingly. Long-run bounded viewer history,
cross-engine contacts, arbitrary predicates, native cancellation and full city
authoring remain limits. Hardware-GPU frame rates have not been established by
the software-renderer browser checks.

Default dependencies are `aerokernel` and `PyYAML`. Extras: `server`, `agents`,
`dev`, `docs`. [Installation](INSTALL.md) and the
[documentation index](docs/README.md) give the detailed interfaces and gates.

## Citation

Retain the AirFogSim citation for the project's research lineage. The cited paper
describes the historical package, not every new platform capability.
[JOSS paper](https://joss.theoj.org/papers/10.21105/joss.08267).

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
