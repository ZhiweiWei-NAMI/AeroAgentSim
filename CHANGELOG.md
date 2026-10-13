# Changelog

## Unreleased

- **Describe a scenario, confirm, run.** The workbench assistant turns plain Chinese or English into a validated plan with any OpenAI-compatible model, shows its assumptions and open questions, and changes the project only on **Confirm and apply**. The Story tab checks the plan against the recorded run. [Recorded DeepSeek demo with transcript and logs](docs/demos/deepseek-chinese-instruction/README.md).
- **Assistant robustness.** New places apply before the orders that use them; a turn that exhausts its tool budget still ends with an answer; scene summaries state how many randomly released orders will actually arrive.
- **Workbench fixes.** Guide links open referenced docs in place, graph type filters name their vocabulary, the crop field sits on open ground beyond the detailed city, idle delivery drones read "Delivery", and rates show as percentages.

## 2.1.0 — 2026-10-11

- **Operational workbench.** Saved workspaces first, revision-bound authoring, top-level runtime controls, searchable explorer, resizable graph/city views and contextual inspection.
- **Food delivery.** Real preparation queues, physical pickup/delivery, deadline KPIs and typed kitchen disruptions; scheduled and interactive modes share the same runtime.
- **Progressive detailed scenes.** Incremental Huangpu loading, shared image decoding, building batching and camera transitions. Read transport reconnects without resubmitting commands.
- [Verified delivery and access instructions](docs/project/workbench-delivery.md).

- **Nine scenarios to start from.** A thumbnail gallery offers parcel delivery, medical delivery, powerline inspection, crop spraying, air taxi, security patrol and traffic-accident response, plus **city-day**, where several businesses and road-incident responders share one clock and airspace. List them with `aeroagentsim templates`; open an editable workspace or run headlessly with `aeroagentsim run-template`.
- **Algorithms and measured experiments.** Swap allocation, route planning, conflict resolution and charging algorithms, or supply a Python class. Batch allocation exposes the differences between nearest-pickup dispatch, energy-aware auction bids and Hungarian matching by completion time. `aeroagentsim experiment` runs knob grids with matched seeds and parallel workers, exporting per-KPI means, sample standard deviations and observation counts to CSV and Markdown.
- **Results you can follow.** Run comparison and KPIs expose completed tasks, latency, energy and operational alerts. A plain-language Chinese/English event feed explains business activity and links each event to **Why did this happen?** Indexed viewer feeds support seeking and reconnecting to recorded runs.
- **Huangpu city pack 1.1.** All nine templates use the shared city frame. `aeroagentsim assets fetch` installs the optional detailed city from `assets-huangpu-city-v1.1`, including a catalog of vehicle, UAV, bicycle, pedestrian and street-furniture models, class-aware selection and levels of detail. The bundled OSM city remains usable without the pack.
- **Editable operational rules.** Battery reserve, no-fly zones, separation and wind limits are shared predicates and event chains. Template knobs edit their real parameters; charging and separation chains retain physical execution receipts, and active flights can leave newly activated no-fly zones through a bounded emergency route.
- **Cause → effect inspection.** Trace recorded inputs through predicate evaluations and chain actions to before/after state and command receipts. Per-entity change timelines, grouped graphs and linked 3D highlights keep the explanation in context.
- **Watch like a control tower.** Director camera follows activity; cinema mode keeps the city and event ticker in view. Replay supports 0.5–8× speed and keyboard shortcuts. Entity cards show the current task, next stop, battery, recent journey and the recorded reason for holding, rerouting or returning to charge; cargo and charging markers make activity visible.
- **Studio and language fixes.** Template workspaces retain their city, scene layers toggle without rebuilding geometry, generators preserve fractional values, late accident injection works, and background labels are less crowded. Chinese/English controls, templates, help and narratives now cover the console's main workflows.
- **A clearer entry point.** Rewritten English and Chinese READMEs, template quickstart and a [repo map](docs/repo-map.md) connect runnable examples to the code that owns them.

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
