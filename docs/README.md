# AeroAgentSim documentation

Start with [README](../README.md), [中文概览](../README_CN.md) and
[installation](../INSTALL.md). New code uses `aeroagentsim.Simulation` on the
independent aerokernel. The old SimPy API is deprecated and needs `legacy`.

- [Capability checklist and verification](platform/CAPABILITIES.md)
- [v1 migration with runnable examples](platform/MIGRATION-v1.md)
- [Architecture and milestones](platform/PLAN.md)
- [Platform scenario/run/replay](platform/p1.md)
- [AeroGraph compiler](platform/aerograph-compiler.md)
- [Native adapters](platform/adapters.md)
- [PX4 backend](platform/px4-backend.md), [SUMO backend](platform/sumo-backend.md), [ns-3 backend](platform/ns3-backend.md)
- [Logistics and inspection packs](platform/packs.md)
- [LLM decisions and console](platform/agents.md)
- [Viewer and frontend](platform/frontend.md)
- [Studio authoring (P7b integration in progress)](platform/studio.md)

The older getting-started, user/API, guides and examples pages describe historical
v1 concepts unless explicitly marked otherwise. They remain available for
legacy users; use the migration guide for the new contracts. P7b authoring is
under active integration and is not a prerequisite for running the CLI slice.
