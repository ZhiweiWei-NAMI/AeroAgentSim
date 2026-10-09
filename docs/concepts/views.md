# Views: the console

The console is a single web app (`frontend/src/`) served by the platform
HTTP service or any static host pointed at it. It renders only the
**committed feed** projected from the journal — viewer refresh never
advances simulation time, and inspector values come from recorded state. Pose animation may interpolate between known samples; it does not write new facts.

## Pages

| Route | Page | Purpose |
| --- | --- | --- |
| `/` | Home (`frontend/src/console/HomePage.tsx`) | Entry page with run gallery and shortcuts. |
| `/studio` | Studio (`frontend/src/studio/StudioPage.tsx`) | Author scenario, entities, engines and behaviour packages; validate; launch runs. |
| `/aerograph` | AeroGraph (`frontend/src/pages/AeroGraphPage.tsx`) | Browse the pinned registry: types, effective fields, relations. |
| `/runs` | Runs (`frontend/src/pages/RunsPage.tsx`) | Live and replay run views: graph + 3D side by side, operate, inspect. |
| `/inspect` | Inspect (`RunsPage` in inspect-list mode) | Run list and recorded-feed inspection. |

`/studio?guide=1` shows the guided console walkthrough
(`frontend/src/console/ConsoleGuide.tsx`).

## One run, two synchronized views

A run view (`/runs/<id>?mode=live|replay&api=<service-origin>`) shows:

- the **AeroGraph graph**: entities grouped by directory/type with actual
  committed relations. Nonspatial entities — tasks, coordinators, records —
  are graph nodes without poses; selecting one opens the inspector and
  preserves the 3D camera.
- the **3D viewer**: spatial placement from `RunHeader.presentation` only.
  The shared selection key is `(runId, epoch, id, generation)`, so a spatial
  selection highlights the same entity generation in both panes.

A single temporal store owns `valid_at`, `known_at`, selection and
follow/cursor state. Seeking a chain transition selects its actual journal
cut in both panes; a frozen historical cut stays frozen while the live tail
grows.

## Simulation pause vs playback pause

- **Playback pause** stops only the display clock. The live run keeps
  committing; you can keep seeking recorded cuts.
- **Simulation pause** is a run-control action from the Runs page (see
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

- Studio edits scenario YAML, engine configuration (forms from plugin
  `config_schema`, plus raw JSON) and behaviour packages (structured forms +
  state-machine graph + raw editors) as one model. Every edit invalidates
  validation; profile/writer changes create a new run.
- The demo import copies the shipped scenario and its pinned closure into a
  workspace draft; it never modifies a running run.

## Where to go next

- [Runs](runs.md) — run lifecycle and control actions.
- [Visualization guide](../guides/visualization.md) — viewer details, assets
  and capture.
- [Views feed reference](../reference/viewer-feed.md) — the feed contract.
