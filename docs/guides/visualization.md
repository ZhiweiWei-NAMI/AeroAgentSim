# Guide: Visualization and the web console

The web console is a React/Vite single-page app in
[../../frontend/src](../../frontend/src), served by `aeroagentsim serve` (or
any static host proxying the API). Routes are defined in
[../../frontend/src/App.js](../../frontend/src/App.js).

## Routes

| Route | Page | What it does |
| --- | --- | --- |
| `/` | [HomePage](../../frontend/src/console/HomePage.tsx) | Landing page and demo gallery |
| `/studio` | [StudioPage](../../frontend/src/studio/StudioPage.tsx) | Guided scenario authoring; `/studio?guide=...` opens embedded guide content |
| `/runs` | [RunsPage](../../frontend/src/pages/RunsPage.tsx) | Start a configured scenario, list runs, operate one |
| `/runs/{id}` | RunsPage (`mode=live` or `mode=replay`) | Synchronized AeroGraph + 3D view, timeline, controls, receipts |
| `/inspect` | RunsPage with `inspectList` | Recorded-state exploration list |
| `/inspect/{id}` | same synchronized view, replay-oriented | One timeline shared by graph and city views |
| `/aerograph` | [AeroGraphPage](../../frontend/src/pages/AeroGraphPage.tsx) | Graph-focused inspection |
| `/agents/{id}` | [AgentConsole](../../frontend/src/pages/AgentConsole.tsx) | Agent decisions, records and receipt history for one run |
| `/viewer-demo` | [ViewerDemoPage](../../frontend/src/viewport/ViewerDemoPage.tsx) | Authored in-memory viewer example, independent of the backend |

Attach a console to a service origin with the `api` query parameter, for
example `/runs/<run-id>?api=http://127.0.0.1:8002&mode=live`. A run id matches
`/^(runs|inspect)\/[^/]+$/`; `mode=live|replay` selects the feed mode.

## Studio authoring steps

Studio walks seven steps (defined in
[../../frontend/src/studio/guided-model.ts](../../frontend/src/studio/guided-model.ts)):

1. **Scene** — region map, geographic extract, entity placement with a chosen
   writer partition and ENU coordinates.
2. **Entities** — typed entities, initial fields, relations.
3. **Domain plugins** — pick engines; one writer per field.
4. **Predicates** — predicate AST authoring.
5. **Event chains & rules** — connect states into transitions with triggers,
   guards, priorities and ordered actions; bindings, conflicts and injection
   points.
6. **Agents** — recorded (stub/fixture) decisions or a live LangGraph model.
7. **Validate & run** — full-scenario validation through the real server
   compiler; **Validate** unlocks **Run now**.

Every model edit invalidates validation; validation issues link back to their
step. **Open the traffic accident demo** from the landing state to copy the
shipped scenario into a workspace. The browser never evaluates predicates or
simulates behaviour — the server does.

## Operating a run

- **Start**: `/runs` → scenario path → **Start scenario** (creates a run via
  `POST /v1/runs`). `mode=live` follows the live feed; `mode=replay` plays a
  recorded run from the same journal.
- **One timeline**: the graph view and 3D city view share the selected
  identity `{runId, epoch, id, generation}`, live-follow and a bitemporal
  cursor. A seek (timeline scrub, decision record cut, photo source cut)
  freezes both views at the same journal prefix, including same-nanosecond
  microsteps. **Follow live** seeks the latest committed cut. Display
  interpolation never changes recorded facts.
- **Run controls**: pause/resume/stop with operational receipts showing
  requested action versus observed effective status; in replay, operational
  controls are disabled. Timeline play/pause controls playback independently.
- **Injection**: the ingress form selects a registered command or a declared
  injection point, renders its typed payload schema, and requires an explicit
  occurrence, stream, source clock/mapping and acquisition numerator/
  denominator. When a run reports `waiting_for_input`, the run list shows a
  banner with an injection shortcut; advancing the stream watermark stays a
  separate action.
- **Inspection**: predicate context cards and chain instance cards link to
  entity identities; unknown/invalid truth values stay distinct from true and
  false, with diagnostics, read cut and source stamps. **Agent decisions**
  opens the same run/identity/prefix in AgentConsole. Artifacts (for example
  PNG captures) open stored bytes with correlation shown separately from
  business acceptance.

## Feed contract

The pinned service boundary is
[../../frontend/src/contracts/viewer-feed.ts](../../frontend/src/contracts/viewer-feed.ts)
(`aeroagentsim.viewer-feed/v1`); transports implement `header`/`subscribe`
over the HTTP/SSE endpoints (`/v1/runs/{id}/header`, `/commits`, `/stream`).
The store owns the committed cut, exact facts, relation changes and
message/receipt history; display buffers never write facts back. Behaviour
extensions (`predicate-truth/v1`, `chain-instance/v1`) appear as optional
header/commit fields and are absent in older runs.

## Limitations

- 3D presentation is display art direction (sky, lighting, interpolation);
  it does not add physics steps or invent state.
- The demo's camera captures render committed poses with the committed lite-city footprints and procedural geometry
  in headless Chromium — simulation-camera output, not licensed city imagery.
- The optional high-detail city pack is not shipped.
- Feed requests use `AbortController`; transport failures stay visible and
  never become empty successful data.

See also: [HTTP API reference](../reference/http-api.md),
[viewer feed reference](../reference/viewer-feed.md).
