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

The renderer uses a linear half-float EffectComposer buffer, SSAO in medium/high, SMAA, subtle bloom and one ACES output tone-map. Output is sRGB. Low uses pixel ratio 1 and disables shadows/AO/bloom intensity; medium uses capped 1.5 ratio and 1024 shadow maps; high uses capped 2 ratio and 2048 maps with more AO samples. A five-second measured frame window below 28 FPS drops one preset. The UI reflects the effective quality. Sky/lighting presets are display art direction, not weather measurements.

Implementation references: [postprocessing SSAO](https://pmndrs.github.io/postprocessing/public/docs/class/src/effects/SSAOEffect.js~SSAOEffect.html) and [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html).

## Contract and time notes

The original pinned contract added the optional field: `facts[].discontinuity`. A producer can explicitly mark a teleport/discontinuous update without retracting its new exact fact. This is needed because distance alone cannot distinguish a teleport from fast movement for arbitrary entity types and engines. Older feeds remain compatible. The flag inserts a display interpolation barrier; it changes no authoritative value, timestamp or event. `RunHeader.presentation` remains the only source of spatial field/visual bindings. The demo exercises nearest-ancestor fallback for its Amber type. Records have no binding and appear only in the inspector. A model binding must name an asset; a missing/failed model never turns into a generic marker. Decoder assets ship under `public/decoders/` and can be overridden through `ViewportOptions`.

Times are canonical decimal strings, parsed as BigInt; only short relative durations and interpolation ratios become Numbers. The demo deliberately starts above `Number.MAX_SAFE_INTEGER`. Samples are displayed on their commit availability time. `validFrom` remains visible as recorded metadata and does not retroactively revise an earlier committed cut. Display interpolation uses known bracketing samples; before the first sample, after removal, or across a retraction gap it has no pose. After the last valid sample it holds. Generation changes create separate buffers. Discontinuous motion uses the optional explicit flag or a retraction barrier; there is no guessed speed/distance threshold. Orientation-free visuals use an authored renderer orientation, not an invented committed quaternion.

ENU positions are `[east,north,up]`, NED `[north,east,down]`, WGS84 `[latitude degrees,longitude degrees,altitude metres]`. Renderer axes are east/up/south in metres. WGS84 requires `RunHeader.origin`; its orientation is interpreted in the local ENU tangent basis. World quaternions multiply the declared frame basis by the recorded body orientation. Asset authoring must align model local axes with that body basis; model animations and per-asset axis metadata remain future contract work.

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
| Message `subjects` | Typed entity refs explicitly declared in message payloads, including command subject refs |

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
