# Guide: Visualization and the web console

The web console is a React/Vite single-page app in
[../../frontend/src](../../frontend/src), served by `aeroagentsim serve` (or
any static host proxying the API). Routes are defined in
[../../frontend/src/App.js](../../frontend/src/App.js).

## Routes

| Route | Page | What it does |
| --- | --- | --- |
| `/`, `/w/{workspace}` | [Workbench](../../frontend/src/workbench/Workbench.tsx) | Gallery, configuration, versions, live runs, replay, records and comparison — one 3D scene with hovering panels |
| `/agents/{id}` | [AgentConsole](../../frontend/src/pages/AgentConsole.tsx) | Agent decisions, records and receipt history for one run |
| any route with `?capture=1` | [CaptureConsole](../../frontend/src/observations/CaptureConsole.tsx) | Headless renderer used by the in-simulation camera |

Attach a console to a service origin with the `api` query parameter, for
example `/?run=<run-id>&mode=live&api=http://127.0.0.1:8002`. Earlier
addresses (`/studio`, `/runs/<id>`, `/inspect/<id>`, `/aerograph`) redirect
into the workbench with their context.

## Configuring a project

Click a thumbnail in the gallery to read the scenario guide, then **Create a
project**. The configuration panel opens next to the 3D draft preview:

- **Controls** — the template's knobs (written into the draft).
- **Space, Objects, Resources, Tasks, Algorithms, Components, Run** — the
  seven sections of the draft. Tasks includes a structured editor for
  predicates, triggers and event chains.
- **File** — the same draft as YAML (export/import).

**Validate** lists every issue and jumps to its section and field. **Save
version** freezes the validated draft. **Initialize** creates a run from that
version: the world is loaded at t=0 and does not move until **Start**. The
browser never evaluates predicates or simulates behaviour — the server does.

## Operating a run

- **Simulation control** (top bar): start, pause, resume, stop and simulation
  speed (0.5× to max). Refreshing the page never starts, stops or resubmits.
- **Playback** (dock): play, seek and playback speed browse the record and
  never affect the simulation. Seeking a past cut shows a "viewing history"
  banner with **Back to live**.
- **One selection**: selecting in the 3D scene, the object list, the graph or
  an event selects the same identity `{runId, epoch, id, generation}`
  everywhere. **Focus**/**Follow** fly the camera; the camera pad zooms,
  rotates and resets.
- **Operate**: the inspector's Operate tab selects a registered command or a
  declared injection point with its typed payload and shows the receipt. When a
  run reports `waiting_for_input`, a banner explains what it needs.
- **Why?**: an event's cause trace on the timeline. **Photos**: camera
  captures (traffic accident) open at their source cut.
- **Records**: replay, compare 2–4 runs, **Modify & retry** to derive a new
  draft from a run.

The persistent camera browser acknowledges app readiness, then initializes the
issued run header's pinned city resources and shaders before its first frame.
Browser startup and asset initialization each have a 30-second bound. Asset
readiness is reused for the same camera resources; preparation creates no PNG
or journal event. Frame acquisition still uses the smaller of the renderer and
request timeouts and verifies the exact source cut, camera and assets. Failure
in any phase remains an explicit capture failure and never produces a photo.

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
- The demo's camera captures render committed poses with the selected city geometry
  in headless Chromium — simulation-camera output, not licensed city imagery.
- The optional high-detail city pack is a separate download, keeping clones small.
- Feed requests use `AbortController`. Temporary network and server failures
  reconnect with bounded backoff from the last received journal index; they
  never resubmit commands or become empty successful data. Leaving the view
  cancels pending reads.

See also: [HTTP API reference](../reference/http-api.md),
[viewer feed reference](../reference/viewer-feed.md).

## Shared Huangpu city

Shipped templates declare `city: huangpu` in their manifest or scenario. Display
coordinates share the traffic demo's local ENU metres: east is scene X, north is
negative scene Z, and up is scene Y. The city reference selects display geometry;
it does not move entities, change flight paths or add obstacle physics.

Without an installed pack, the viewer and photo camera render the committed lite
city. `aeroagentsim assets fetch` adds the detailed city for every Huangpu template.
Reload the viewer after installation or removal. The viewer header advertises
`cityDetail` (`lite` or `detailed`) and `assetPacks`; the lightweight view shows a
small download hint. An explicitly configured asset root has priority.

The lite bundle covers a smaller central excerpt, while the detailed scene spans
approximately ±540 m. Template site placement must be checked against actual
footprints and rooftop heights; selecting a city does not certify a landing site.

For parcel-delivery's current placement and a proposed translation for template
authors, see [parcel city placement](parcel-city-placement.md). The template
coordinates remain unchanged.
