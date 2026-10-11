# Views: the console

The console is a single web app (`frontend/src/`) served by the platform
HTTP service or any static host pointed at it. It renders only the
**committed feed** projected from the journal — viewer refresh never
advances simulation time, and inspector values come from recorded state. Pose animation may interpolate between known samples; it does not write new facts.

## One workbench

| Route | Purpose |
| --- | --- |
| `/` | The workbench with the scenario gallery: nine ready-made worlds; a thumbnail opens its guide, "Create a project" makes an editable draft. |
| `/w/<workspace>` | The workbench for one project: configuration, versions, runs, replay, records and comparison in place. |
| `/agents/<run>` | Agent console: recorded decisions of one run. |

Earlier addresses (`/studio`, `/runs`, `/runs/<id>`, `/inspect/<id>`, `/aerograph`)
redirect into the workbench and keep their context (run, mode, cut, selection).
The URL query carries the context: `run`, `mode=live|replay`, `cut`, `sel`,
`panel`, `records`, `compare`, `graph`, `contract`. The code is in
`frontend/src/workbench/`.

## One scene, hovering panels

The 3D stage fills the screen; panels hover over it:

- **top bar**: project, version, run status, simulation time and simulation
  control (initialize, start, pause, resume, stop, simulation speed);
- **KPI cards** of the template;
- **objects** (left): every concrete object grouped by type; non-spatial objects
  are marked;
- **inspector** (right): state at the cut, story (moments and changes) and
  operate (admissible commands with receipts);
- **dock** (bottom): playback of the record, the timeline with event markers,
  events, results, photos and "why?" (cause trace);
- **relation graph** (floating, expandable): entities and committed relations;
  selecting a node selects the entity everywhere and flies the camera to it.

The shared selection key is `(runId, epoch, id, generation)`, so a selection
in the graph, list, 3D and inspector is the same entity generation. One
temporal store owns `valid_at`, `known_at`, selection and follow/cursor
state; a frozen historical cut shows a "viewing history" banner with "back to
live" while the live tail grows.

## Simulation pause vs playback pause

- **Playback pause** stops only the display clock. The live run keeps
  committing; you can keep seeking recorded cuts.
- **Simulation pause** is a run-control action in the top bar (see
  [Runs](runs.md)). It freezes host execution at the current committed cut.

Transport errors preserve the last received cut and show the error — never
simulated progress.

## Display time and absence

- Times are canonical decimal strings parsed as BigInt; nanoseconds and
  microsteps are shown exactly. Display interpolation moves display time
  only, uses known bracketing samples, and holds after the last sample.
  Before the first sample, after removal or across a retraction gap there is
  no pose.
- Absence is displayed as absence (`UNKNOWN`/absent), not zero or false.
- Generation changes create separate buffers; explicit discontinuity flags
  create interpolation barriers.
- Optional extensions project predicate truth and chain instances
  (`predicateTruth`, `chainInstances` commit arrays) from WAL records; older
  feeds simply show no recorded predicate/chain data.

## Configuration surfaces

- Configuration opens as a panel inside the workbench while the 3D draft
  preview stays visible: template controls, the seven sections (space,
  objects, resources, tasks, algorithms, components, run) and the file (YAML)
  edit the same draft. Every edit invalidates validation; issues jump to their
  section and field.
- Validate → save version (immutable) → initialize (the world is loaded at
  t=0 and nothing moves) → start. A run always uses one saved version.
- Records: replay a run, compare 2–4 runs, or "modify & retry" (a new draft
  derived from the run's version).

## Where to go next

- [Runs](runs.md) — run lifecycle and control actions.
- [Visualization guide](../guides/visualization.md) — viewer details, assets
  and capture.
- [Views feed reference](../reference/viewer-feed.md) — the feed contract.
