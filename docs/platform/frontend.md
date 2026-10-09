# Frontend: F1 viewer and Vite migration

The React 18 workbench now builds with Vite and TypeScript. `allowJs` preserves the existing workbench pages; their tests run with Vitest and jsdom. Ant Design, React Router and the English/zh-CN provider remain in place. `/viewer-demo` is an explicitly authored in-memory example, independent of backend availability. It never substitutes for a failed real run or service request.

## Run and verify

From `frontend/` with Node 22:

```sh
npm ci --cache .npm-cache
npm run dev
npm run build
npm test
npm run typecheck
npm run test:e2e
```

Open `http://localhost:3000/viewer-demo`. The existing workbench uses `/api` and `/ws` development proxies to port 8002. Existing `REACT_APP_API_BASE_URL` and `REACT_APP_WS_BASE_URL` deployment settings are explicitly compiled by Vite. The production output is `frontend/dist/`; configure the serving host to route history URLs to `index.html` and proxy those services. Playwright builds and serves the production app on port 4179, uses cached Chromium/headless-shell 1234 when available, and bypasses the session proxy for loopback. `PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select another installed executable. Browser temporary/config files stay under ignored `frontend/.tmp/`; the Linux `/proc/self/cwd` alias avoids long Unix socket paths. Screenshots are `test-results/viewer-demo-orbit.png` and `test-results/viewer-demo-follow.png`.

## Legacy workbench migration

The shell and viewer are separate lazy routes. The JSX-in-`.js` compatibility transform runs in dev, build and Vitest; new viewport code is strict TypeScript. Workflow Studio and Run Console are split into small page/model/panel modules, and the former large API file is a re-export entry for real HTTP/WS clients and response-shape translation. Existing Ant Design, router and i18n tests are ported to Vitest with ESM mocks. Maintained frontend source files remain below 600 lines.

The API no longer substitutes hard-coded catalog/config/graph/validation data after connectivity errors, reads local-storage registry/config defaults, invents zero positions or times, supplies battery 100, reports missing health as available, or stamps absent log time with `now`. Requests still call the actual catalog, registry, config, run and health endpoints, with backend errors/diagnostics preserved. Catalog workflow lists retain arbitrary returned IDs. Studio and Console expose graph request errors while retaining previously recorded graph data; catalog loading no longer repeats just because the selected authoring type changes. Legacy authoring convenience helpers remain scoped to the old studio, and never enter the descriptor viewport. `MOCK_CONFIG_ID` remains a deprecated import alias for the real default config ID; it returns no mock data. The authored viewer demo is selected only through its explicit route.

## Separation of responsibilities

`src/contracts/viewer-feed.ts` is the pinned service boundary. There are no kernel imports or engine-specific entity kinds in the viewport. A transport implements `ViewerFeed.header/subscribe`; the renderer accepts a `FeedStore` and a display time. The store owns the committed cut, exact facts, relation changes and message/receipt history. The inspector reads that cut, including producer and validity metadata. Display buffers never write facts back.

Modules in `src/viewport/` have narrow responsibilities:

| Module | Responsibility |
| --- | --- |
| `time.ts`, `clock.ts` | Lossless nanosecond strings/BigInt and a play/pause/speed/seek display clock |
| `bindings.ts` | Exact type binding, then nearest declared actual ancestor; no inference from directory/name |
| `samples.ts`, `feed-store.ts` | Vector lerp, shortest-path quaternion slerp, lifetimes, retractions and exact journal cuts |
| `coordinates.ts` | ENU/NED to east/up/south; WGS84 ECEF delta projected at declared run origin |
| `scene.ts`, `pipeline.ts` | Display sky/environment, ground/grid/fog, sun, soft shadows and postprocessing |
| `assets.ts`, `mesh-pack.ts` | GLTF/Draco/meshopt/KTX2, HDR environment and OSM2World pack loading |
| `entities.ts` | Descriptor-selected marker instances, models, labels, selection and selected history trail |
| `viewport.ts` | Imperative WebGL2 renderer, orbit/follow/chase, picking, sizing and disposal |
| `ViewportView.tsx` | Thin React mount/animation/disposal adapter |
| `ViewerDemoPage.tsx`, `EntityInspector.tsx` | Example controls and generic committed-state inspection |

The renderer uses a linear half-float EffectComposer buffer, N8AO in medium/high,
SMAA, subtle bloom and one ACES output transform. Output is sRGB. The current
presets, model LOD, manual quality pinning and measured automatic degradation
are documented in the P7a section below. Sky/lighting presets are display art
direction, not weather measurements.

Implementation references: [N8AO](https://github.com/N8python/n8ao) and [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html).

## Contract and time notes

The original pinned contract added the optional field: `facts[].discontinuity`. A producer can explicitly mark a teleport/discontinuous update without retracting its new exact fact. This is needed because distance alone cannot distinguish a teleport from fast movement for arbitrary entity types and engines. Older feeds remain compatible. The flag inserts a display interpolation barrier; it changes no authoritative value, timestamp or event. `RunHeader.presentation` remains the only source of spatial field/visual bindings. The demo exercises nearest-ancestor fallback for its Amber type. Records have no binding and appear only in the inspector. A model binding must name an asset. P7a renders an explicitly reported identification marker while a model is pending/unavailable; no physical state is synthesized. Decoder assets ship under `public/decoders/` and can be overridden through `ViewportOptions`.

Times are canonical decimal strings, parsed as BigInt; only short relative durations and interpolation ratios become Numbers. The demo deliberately starts above `Number.MAX_SAFE_INTEGER`. Samples are displayed on their commit availability time. `validFrom` remains visible as recorded metadata and does not retroactively revise an earlier committed cut. Display interpolation uses known bracketing samples; before the first sample, after removal, or across a retraction gap it has no pose. After the last valid sample it holds. Generation changes create separate buffers. Discontinuous motion uses the optional explicit flag or a retraction barrier; there is no guessed speed/distance threshold. Orientation-free visuals use an authored renderer orientation, not an invented committed quaternion.

ENU positions are `[east,north,up]`, NED `[north,east,down]`, WGS84 `[latitude degrees,longitude degrees,altitude metres]`. Renderer axes are east/up/south in metres. WGS84 requires `RunHeader.origin`; its orientation is interpreted in the local ENU tangent basis. World quaternions multiply the declared frame basis by the recorded body orientation. Asset authoring must declare model local axes relative to that body basis. P7a GLB metadata can carry bodyToAssetQuaternion; model animation remains future work.

Playback holds at the available end and never predicts or advances a simulator. The compact timeline seeks actual commits and shows nanoseconds and microsteps; wall-clock animation only moves display time. P1 services must provide a buffered live cut or replay page before interpolating, and extend the available clock end as real commits arrive.

## City assets and port provenance

`ViewportOptions.city` accepts an explicitly selected building/scene GLB URL or `{ kind: 'osm2world', url, assetsBase }`. Mesh-pack manifests retain `aero-bench.osm2world-mesh-pack/v1`, their projection and coordinate provenance. Referenced chunks/textures resolve as `assets/<sha256>` below `assetsBase`, with SHA-256 and byte-count checks. Geometry uses the native positions/normals/UV/index binary layout. Declared textures preserve color space and wrapping. A pack's stored converter-to-pack translation is already in its vertices; only pack-origin to run-origin horizontal translation is applied again. Pack Y values are retained. `city.offset` is an explicit renderer-space adjustment for a caller who knows the vertical datum. GLBs must already use renderer axes/metres, with any placement expressed by that same offset.

With no city data the sky, lit ground, grid, horizon and distance fog form the complete display scene. No procedural city is presented as real geometry. An asset error appears in the UI and does not claim city-load success.

Read-only AeroBench sources reviewed for this implementation: `map.ts` renderer setup; `entity-visuals.ts`; `city-lighting.ts`, `city-lighting-calibration.ts`, `city-day-sky.ts`; `observation-camera.ts`; `osm2world/pack-loader.ts`, `pack.ts`, `projection.ts`, `runtime.ts`; `state/replay.ts`; and `telemetry-hud.ts`. The pack parser/unpacker is copied and adapted with local type declarations. Projection math, imported-light removal and display-lighting values are adapted into the smaller modules. The complete map, city geometry generators, UAV/UGV/person visual switch, flight HUD, sensor readback, business panels and fixed 10 Hz replay timer are intentionally not ported. Postprocessing is new; the old map had no composer pipeline.

Source content hashes at extraction (SHA-256):

| AeroBench source | Digest |
| --- | --- |
| `osm2world/pack.ts` | `a803bccf7d26fe14fec81caaf755fdb42de0b301a66fd2edc70b4b5aa03696a9` |
| `osm2world/projection.ts` | `5f070872b074d64063155d2e706f0fe531a332af8e9460ff04a8fdf87ec7bc9a` |
| `city-lighting.ts` | `0e9946d2a3ec8334de77771e24b461545055fcc04b4118e7a97ff18cbd9478ab` |

## Follow-on integration

P1 connects real live/replay services implementing ViewerFeed, introduces paging/checkpoints and bounded retained history, and adds server-fed descriptor/asset integrity metadata. An interrupted connection must retain and label its last recorded cut, with a visible connection error. Large-run performance work should measure ingest, seek and rendering separately before adding indexed snapshots or worker processing.

P7 adds City Studio as optional authoring: region/OSM import, build artifacts, calibrated weather/solar source bindings, terrain/roads/buildings and pack-defined editors. It must share one compiled coordinate/asset pipeline with the viewport. Sensor cameras remain an explicit optional consumer, separate from this default display renderer.

## F1 validation, 2026-10-08

`npm run build` passes; `npm test` passes 46 tests in 11 files; `npm run typecheck` passes. `npm run test:e2e` passes its production-browser test, including no console/page errors, both camera screenshots, a nonspatial entity and zh-CN switching. The software renderer visibly degraded medium to low during the browser run; the screenshots record the effective setting. This is functional verification, not a hardware performance benchmark. GLB compressed-asset loaders are wired with local codecs; the browser demonstration intentionally exercises markers and no city. A binary mesh-pack loader fixture separately verifies layout, origin placement and corrupt-content rejection.

Production JS+CSS is 2,363.92 kB total (735.32 kB gzip, decimal units). The lazy viewer JS chunk is 936.48 kB (288.15 kB gzip); workbench and shared chunks load separately. Static optional codec files and screenshots are excluded from that bundle total. Artifacts are `frontend/test-results/viewer-demo-orbit.png` and `frontend/test-results/viewer-demo-follow.png`.


## P1-F additive feed contract and temporal viewer

The optional additions in `src/contracts/viewer-feed.ts` preserve authored demo
compatibility while real P1 services publish full temporal data:

| Addition | Meaning |
| --- | --- |
| `EntityKey.generation: number|string` | Large exact generations use canonical decimal strings |
| `acquired` | Source `clockId/mappingId` and exact decimal rational numerator/denominator |
| `available`, `validFrom`, `validTo` | Publication instant and half-open physical validity interval; null end is open |
| `version`, `causes` | Exact journal/item identity and retained cause references |
| Retraction interval/reason | Shadows only the declared interval at its known prefix |
| Edge `assert|close|cancel` and intervals | Each version carries the resulting interval; cancellation has no active interval |
| Field `schema/metadata`, header `runtimeRegistry/messages` | Complete runtime descriptors, including command capability and result schemas |
| Message `subjects` | Typed `$ref` values and authored subject paths resolved to recorded entity generations |
| Optional header `messageSubjects` | Pinned platform subject declarations by message schema ID; additive to viewer-feed/v1 |

`registryDigest` hashes canonical `runtime.registry.json`, including authored
local descriptors. The compiled source snapshot digest is retained separately
in the run manifest. Wire encoding does not change the semantic registry digest.
Units and frames come from field descriptors; scalar records never acquire ENU.
Values beyond JavaScript's safe integer range use `{"$integer":"decimal"}`;
large integral floats use `{"$number":"decimal"}` to preserve their numeric
kind. Single-member authored records that collide with these tags or `$record`
are escaped as `{"$record":{...}}`. Raw unsafe numeric values are rejected.
The inspector prints tagged integers exactly and shows relative seconds with
exact nanoseconds on hover, alongside source stamps and version identities.
Scenario JSON submission preserves the user's decimal tokens instead of
round-tripping them through JavaScript numbers; strict backend schema validation
still rejects incompatible numeric kinds.

String payload IDs need an explicit subject declaration. For a local scenario
message descriptor, add `subjects` alongside `id`, `kind` and `schema`:

```yaml
subjects:
  - path: [entity]
    type_id: example:Job
```

The payload schema must declare that path as `string` or `ref`. `type_id` is a
registered entity type; subtypes are accepted. Paths are lists of exact record
member names and nonnegative array indices; `"*"` selects every array item.
Nested example: `path: [jobs, "*", entity]`. Optional or nullable schema members
may omit a subject; missing required values and unknown identities remain errors.

For messages already in a pinned registry snapshot, declare the same bindings
under `registry.message_subjects.<message-schema-id>` in the scenario. Inline
`subjects` and this overlay cannot both define the same message. The declaration
is a platform extension preserved in the scenario digest and run artifacts;
kernel payload validation and the runtime registry remain unchanged.

A string subject resolves to the live, WAL-recorded generation at its committed
message cut. Delayed messages about an earlier generation can declare an integer
`generation_path`, for example `[generation]`, alongside a scalar string `path`.
Both members must then be present, and that generation must have been recorded.
Typed `$ref` payloads retain their original identity. Subjects are deduplicated,
and REST pages/SSE reconnects reconstruct lifecycle identity from the preceding
journal prefix. Inspector and timeline filters use `(id, generation)` only;
ordinary strings, source names, and coordinates never imply a subject.

Real Runs pages use `feeds/temporal-store.ts`. It resolves facts and edges by
physical validity and the selected journal knowledge prefix, including finite,
future and backdated versions. Scoped retractions do not erase unrelated physical
intervals; later publication is invisible at earlier knowledge cuts. The explicit
cut survives live ingest. Initial/removed entity generations remain distinct.
Interpolation is confined to ordinary continuously published motion and does
not bridge finite/future/backdated intervals. The authored demo retains its
original availability-oriented store; the preceding demo time notes describe
that older store, not real P1 temporal semantics.

Terminal manifests pin the next `final_cursor`; pages and SSE advertise
`finalCursor`, and clients verify it before stream completion. Fetch/open and
reader transport exceptions resume from the acknowledged cursor with bounded
retry; malformed JSON, contracts and sequence gaps remain explicit errors.
Typed `subjects` associate commands and receipts with entities, never partition
name comparisons. `p1-e2e.mjs` verifies the actual growing run and reconnect.
The owned gate config places all generated frontend assets and screenshots in
`tests/platform/`. P1-F frontend tests: **59 passed**, strict typecheck and
production build passed; browser screenshots are in that test tree.

## Agent console page

`src/pages/AgentConsole.tsx` is a read-only decision log over the same
`HttpViewerFeed`/`RunsApi` transport as `/runs`. The route
`/agents/<runId>?api=...&mode=live|replay` is matched in `App.js` before the
runs route, so `startsWith('/runs/')` never captures it. The page reads the run
header, subscribes from commit 0, and selects only typed messages with
`schemaId === 'aas.agent.record'`, whose payload must be
`{ decision_id, phase, data_json }`. `data_json` is parsed with `JSON.parse`
at ingest; parse or shape problems are collected per record and shown as error
alerts instead of being dropped, and there is no demo or synthesized data. The
known phases are `observation`, `prompt`, `response`, `validation`, `command`,
`failure`, `finished` and `receipt`.

Each `decision_id` renders as one card: phases with sim time
(`seconds(at.ns, header.start.ns)`), observation fields (count, exact values
via `exactValue`, and an entity link when a field's payload contains a known
entity ID — linked as `/runs/<runId>?api=...&entity=<id>`), command proposals
(`call_id`, `schema`, `target`, `payload` args, `decision_summary`), actual
receipt records (`command_id`, `call_id`, `status`, `result`), plus the
matching journal `commit.receipts` history attached through the receipt record's
`call_id` → `command_id` mapping. Multiple command proposals and all receipt
versions remain visible; a tool call ID is never treated as a kernel command ID.
Entity IDs are gathered from commit `created`/`facts`/`edges` keys, so links
only appear for real registry identities. The subscription runs under an
`AbortController` that is aborted on unmount or parameter change; feed,
contract and record errors stay visible in the page and never fall back to
placeholder content.

The run viewer exposes an **Agent decisions** link and honors the console's
`entity` query parameter for inspector selection. The optional static frontend
host serves `/agents/{path:path}` as an additive SPA route. Console regression
tests live in `tests/agents/agent-console.test.tsx` with an owned Vitest config;
they verify multiple proposals, full receipt correlation, entity links, malformed
observations and subscription cancellation. P6 changes do not modify the viewport
or its feed store.

## P7a city viewer and asset delivery

The viewer now loads a single OSM2World scene pipeline below `src/scene/`, with
run-local east/up/south metres, physically based analytic sky, optional HDRI IBL,
a moving single sun-shadow cascade, N8AO, SMAA, subtle bloom and ACES output.
The existing exact feed/inspector cut remains authoritative. Scene art direction,
model LOD, camera motion and display interpolation do not write kernel facts.

Run the stdlib asset synchronizer from the platform root:

```sh
/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python tools/sync_assets.py
# Optional geometry compression alternatives:
# python tools/sync_assets.py --optimize draco
# python tools/sync_assets.py --optimize none
```

`--source` selects another AeroBench public tree. The script verifies the
content-addressed source pack, copies its referenced chunks and original OSM
JSON, and downloads the CC0 OSM2World car and BSD X500 source meshes. It writes
`frontend/assets.manifest.json` with output paths, SHA-256, byte counts, licenses,
attribution and transformation descriptions; source model hashes are retained
separately. Payloads under `public/assets/` are ignored; converter/npm work stays
under ignored `src/scene/.work/` and is never copied into the production public
tree. No package/lockfile changes are needed. Optional tooling is Three.js,
linkedom and glTF Transform 4.5.1. The kernel has no new runtime dependency.

The Holybro preview GLB in AeroBench has no verified local redistribution notice
and is normalized to 3.1 preview units. It is excluded. The replacement X500 uses
[PX4 x500_base sources](https://github.com/PX4/PX4-gazebo-models/tree/main/models/x500_base)
and that directory's [BSD-3-Clause license, Rudis Laboratories](https://github.com/PX4/PX4-gazebo-models/blob/main/models/x500_base/LICENSE),
with SDF link/visual transforms, native Collada units and a single Y-up conversion.
Only source mesh visuals are retained; source camera/light objects and label
planes are omitted from the display asset. Texture-free PBR, welded display
normals and simplification reduce tessellation before meshopt compression.
`asset.extras.bodyToAssetQuaternion` records the body-to-file basis, so recorded
quaternions compose correctly without applying Y-up conversion twice. No rotor
RPM or animation is fabricated. The source model's exported bounds are
0.407 × 0.297 × 0.641 m; display dimensions are not new physical measurements.

The city geometry/source JSON carry OpenStreetMap attribution and ODbL notices.
Style textures, sky HDRI and car use
[OSM2World-default-style CC0](https://github.com/tordanik/OSM2World-default-style).
Full notices are copied into `assets/licenses/`. Unity BigCity and other car/person
previews without verified redistribution rights are excluded. X500 has no image
textures, so KTX2 is inapplicable to that GLB. `--ktx2` checks for the actual
`toktx` tool; this delivery does not claim a KTX2 conversion of the content-addressed
city textures.

### Additive scene presentation

The frontend accepts optional `RunHeader.scene` through the additive type
augmentation in `src/scene/presentation.ts`. Older headers remain valid:

```json
{
  "origin": { "lat": 31.2288, "lon": 121.481, "alt": 0 },
  "scene": {
    "id": "Shanghai · Huangpu east",
    "city": { "kind": "osm2world", "url": "/assets/city/manifest.json", "assetsBase": "http://localhost:8007/assets/city/" },
    "hdri": "/assets/environment/day.hdr",
    "attribution": "© OpenStreetMap contributors · ODbL · textures CC0"
  }
}
```

`city.kind` also accepts `glb` or GeoJSON `geojson`. GeoJSON requires an origin;
explicit heights are retained, levels × 3 m are flagged display estimates, and
missing heights are counted instead of receiving invented geometry. `roads.url`
accepts SUMO `.net.xml` lane shapes; only explicitly internal edges are skipped.
Absent lane width uses [SUMO's documented 3.2 m default](https://sumo.dlr.de/docs/Simulation/SublaneModel.html) and is counted; malformed
supplied widths fail. The caller must supply `roads.offset` when SUMO's network
origin differs from the run origin; `netOffset` is retained in diagnostics and
is not silently treated as an ENU anchor. `trees` accepts explicitly authored
ENU positions and renders trunks/canopies with two instanced meshes. The selected
Huangpu source contains no `natural=tree` nodes; no tree locations are invented.

A pack applies its recorded projection-to-run horizontal translation once.
Pack vertical coordinates are retained; `city.offset` is the caller's explicit
vertical-datum adjustment. A floating origin rebases camera, entities, trails and
static geometry together in horizontal 2 km cells. Asset absence leaves the
analytic sky/base ground and selectable markers active, with visible source
status. City corruption never reports a loaded city. A missing geographic anchor
never acquires an invented latitude/longitude.

The current service scenario parser/projector is outside P7a ownership and does
not yet forward a scenario `scene` block. Producers can already supply the
additive header field. Until that service extension lands, `?scene=huangpu` is an
explicit viewer choice; it still requires the run's real declared origin. The
copies in `scenarios/visual/` add that geographic anchor and meter-scale model
bindings without changing local trajectories or events. They are visual studies,
not city collision/road-constrained simulations. Originals are untouched.

### Camera and information layout

Runs expose orbit, follow, chase and an event director; `?camera=follow` (or
`chase`, `cinematic`) selects a starting view. Follow/chase work for any entity
with a descriptor spatial binding. The director chooses subjects of recent
recorded events, with seven-second shots; when no associated event exists it
uses the explicitly selected entity. Inspector collapse is a display layout
control. The mission strip uses typed `subjects`, exact message times and
receipt statuses; it never equates arrival with mission/business acceptance.
Event markers seek recorded times and respect the existing journal knowledge
cut. A hidden or absent receipt is not synthesized.

Models share instanced geometry/materials. Beyond 100 m or the near-model budget
of 48 per binding, entities use simple identification markers; the selected
entity retains its model. Missing/pending assets also use visibly reported
markers. Labels prioritize selection, limit ordinary labels to twelve, avoid
screen overlaps and suppress nonselected labels behind city geometry. Selected
labels remain readable. Trails use valid sampled history; selection uses a model
outline when loaded and a small ring otherwise.

Low uses ratio 1, no shadows/AO/bloom intensity; balanced caps ratio 1.25 and uses
2048 sun maps/N8AO half resolution; high caps ratio 1.5 with 4096 sun maps and
higher AO samples. All presets use SMAA and one ACES output transform. A real
five-second frame window below 28 FPS drops an automatic preset; choosing
quality in the viewport explicitly pins it for visual/performance comparisons.

### P7a verification on this host, 2026-10-08

Real CLI runs: `runs/p1-slice-033821af8965` (original),
`runs/p1-slice-city-fab3c5fdfec2` (visual copy), and
`runs/p1-scale-city-5ab312db1765` (visual copy). The city slice has 1,645 committed
transactions and five spatial entities at its terminal cut; the scale copy has
four commits and 1,000 spatial entities. Replay screenshots read the actual
`aeroagentsim serve --out runs/ --frontend frontend/dist --port 8007` endpoints.
The mandated kernel Python interpreter was used for CLI entrypoints with
`PYTHONPATH=src:$PWD/.venv/lib/python3.11/site-packages:/mnt/data2/weizhiwei/aeroagentsim/aerokernel/src`
to expose the platform's already-installed YAML/server dependencies without
installing anything into the kernel environment.

Reproduce the dedicated browser verification with:

```sh
node frontend/e2e/p7a-visual.mjs http://127.0.0.1:8007
```

The script requests ANGLE/EGL first and records its renderer name. This host
exposes no usable NVIDIA device to the process: `nvidia-smi` fails to communicate
with the driver, and EGL resolves to SwiftShader. Hardware RTX 3090 FPS is
therefore **not measured**. The subsequent explicit software run measures
requestAnimationFrame intervals for at least six seconds per view; these are
whole-viewer frame rates, not isolated GPU timings. Presets are pinned during
measurement; browser screenshots are 1440 × 900, device scale 1.

| Real replay view | Preset | Measured FPS | p95 frame ms |
| --- | --- | ---: | ---: |
| p1-slice city orbit | balanced | 1.07 | 1,000 |
| p1-slice city follow | balanced | 1.02 | 1,000 |
| p1-slice city chase | balanced | 1.19 | 866.7 |
| p1-scale city, 1,000 spatial entities | low | 2.05 | 550 |

These software results are below an interactive frame budget. They validate the
rendering path and real entity inventory, not fleet performance on the owner's
GPUs. GPU profiling remains an environment-dependent follow-up. Missing-asset
requests were also exercised explicitly: five real spatial entities remained
selectable in the base scene, the unavailable source status stayed visible, and
the cinematic camera remained operational. No unexpected browser errors were
recorded. `logo192.png` is a pre-existing root-static route missing from the
service's asset mount; the dedicated run excludes only that unrelated console
404 and the explicitly induced missing-asset 404s.

Before: `frontend/test-results/p1-slice-replay.png` and
`tests/platform/screenshots/p1-slice-replay.png`.
After: `frontend/test-results/p7a-slice-orbit.png`, `p7a-slice-follow.png`,
`p7a-slice-chase.png`, `p7a-scale-orbit.png`, and `p7a-without-assets.png`.
Exact FPS, renderer strings, counts and draw statistics are in
`frontend/test-results/p7a-measurements.json`.

The synced inventory has 62 redistributable files totaling 10,169,594 bytes.
The meshopt X500 GLB is approximately 130 kB. Its optimization is display-only;
source collision/dynamics and trajectory semantics are unchanged. Production
JS/CSS total is approximately 2.59 MB, 844 kB gzip; the lazy viewer is
approximately 1.126 MB, 381 kB gzip. Static city/model/HDR/decoder bytes are
separate from those bundle numbers.

Two concurrent `workbuddy/glm-5.3-flash` review sessions completed with a
131,072-token output limit and no effort parameter. Their case-table and
coordinate/license reviews were checked against sources and executable tests.
The reviewed geometry draft was corrected to count missing widths, reject
malformed supplied dimensions, preserve holes, and keep display estimates out
of kernel facts. Earlier expansive GLM audit/geometry sessions did not close
cleanly after their scratch directory moved out of the public tree; their
unfinished findings were not used as verification evidence. Full notices and
manifest hashes were checked directly by the parent agent.

Final frontend gates: **64 tests in 15 files passed**, strict typecheck and production build passed. Source-geometry coverage includes real lane coordinates, missing/malformed width, explicit tree positions, polygon holes, estimated/missing heights, and malformed explicit height rejection.

The existing authored-demo Playwright E2E also passed. Its camera assertion now checks the rendered viewport mode instead of a hidden Ant Design announcement node. The final production JS/CSS is 2,590,045 bytes (843,682 gzip, level 6); the viewer JS chunk is 1,126,361 bytes (381,465 gzip).
