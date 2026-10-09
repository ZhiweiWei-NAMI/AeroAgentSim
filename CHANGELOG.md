# Changelog

## 2.0.0 — first public release of the consolidated platform

- One shared discrete-event runtime for time coordination, external input, committed state and event-chain execution.
- AeroGraph-based types, fields, relations and predicates, with scenario snapshots for standalone use.
- Replaceable kinematic, PX4/Gazebo, SUMO, ns-3 and weather plugins with single field ownership.
- LangGraph decision integration, explicit scripted or live profiles and engine-free replay.
- A guided console for authoring, run control and synchronized graph/3D inspection.
- A public-clone traffic-accident demo with procedural OSM city geometry and real camera capture.
- Lean provenance by default, optional full diagnostics, visible input waits and clean run stopping.

Native container recipes now build the `:dev` tags. Runtime container labels use `aeroagentsim.job=default`; build labels use `aeroagentsim.job=release`. The camera trigger topic is `/aeroagentsim/camera/trigger`. Optional mesh packs use the format labels described in [Asset notes](ASSETS.md). Live providers require an explicit model.

Start with the [documentation](docs/README.md). Native simulator services and live model endpoints require separate configuration; the default demo uses the kinematic and scripted profiles.

## 1.1.1

Previous public release of the AirFogSim-based platform. The 2.0.0 release consolidates simulation on the independent kernel.
