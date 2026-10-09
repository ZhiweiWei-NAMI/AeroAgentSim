# Design notes

The platform separates experiment configuration, domain engines and visualization from a small shared runtime. The kernel owns time coordination, external event ingestion, committed state and deterministic replay; domain plugins supply models and decisions.

Read [aerokernel's design](../aerokernel/docs/DESIGN.md) for the runtime's contracts and rationale. The specification lives with its implementation. The release includes the kernel under `aerokernel/`; current development checkouts may keep it at `../aerokernel`.

For platform use, begin with [Architecture](concepts/architecture.md) and [Runs and replay](concepts/runs.md).
