# Design notes

The platform separates experiment configuration, domain engines and visualization from a small shared runtime. The kernel owns time coordination, external event ingestion, committed state and deterministic replay; domain plugins supply models and decisions.

Read [the kernel's design](../aerokernel/docs/DESIGN.md) for the runtime's contracts and rationale. The specification lives with its implementation; the kernel is included in the release under `aerokernel/`.

For platform use, begin with [Architecture](concepts/architecture.md) and [Runs and replay](concepts/runs.md).
