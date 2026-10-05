# P08 实例关系图工作台

Read-only, local/private prototype for the accepted P08 instance graph. This is an instance/semantic explorer, not a P02 city map, operational console or native rule evaluator. No runtime dependency or external network asset is required.

## Double-click offline entry

Open **P08-graph-explorer-offline.html** directly. It contains the same complete canonical dataset, all 75 scenario chunks, global index, aliases, provenance manifests, contract registries and compact review records as the HTTP app. Compressed blocks are decoded lazily, and CSP denies network requests. The file is approximately 34 MiB and requires the browser's native DecompressionStream gzip support; missing support produces an explicit message rather than trying a CDN or remote service.

The offline HTML is an inspection/export view. Byte-exact reconstruction of the original input documents requires the immutable data/inputs snapshots and recovery tools in the accompanying source ZIP; those original document snapshots are not duplicated inside the HTML.

The standalone HTML is delivered separately from the source ZIP to avoid doubling compressed data. To regenerate it from the source ZIP, run `python3 ui/build-offline.py` from the unpacked project root. No Python third-party package is needed for this build.

Offline packaging checks verify all 103 embedded files byte-for-byte, the full graph hash, and file:// startup plus cross-scenario selection in a DOM emulator. This is not actual browser visual verification. See VISUAL_QA.md for the required real screens and interactions.

## Run the HTTP development app

```sh
cd p08-graph-workbench/ui
npm start
# Open http://127.0.0.1:4318/ui/ in an authorized browser.
```

The server binds only to 127.0.0.1, serves ui/, data/, spec/ and review/ under the project root, and sends no telemetry. PORT=4320 npm start chooses a different loopback port. Opening index.html directly is unsupported; use the separate self-contained offline entry for double-click use. Source data is read from ../data/manifest.json and per-scenario JSON. Export downloads the complete loaded graph, not the bounded canvas. It is a read-only projection export; it is not claimed to be an executable native authoring configuration.

## What is loaded and rendered

- Startup fetches the manifest, global search index, and first authored nonempty case. All 75 manifest cases remain selectable, including preserved source cases and 20 machine examples. The manifest states 35 source workflows / 226 source steps.
- The global index exposes all 20,201 current accepted node IDs. Selecting a nonloaded ID fetches its source case before focusing. Search accepts IDs, labels, types and source aliases. Source payloads, generation types and decimal-string timestamps stay unchanged.
- A selected case can be narrowed to an exact source workflow step using the emitted step.node_ids. Node types and semantic levels use full-index facets; relation facets use loaded data. The sidebar explicitly says which scope is active.
- All-case loading is explicit and cancellable. At most three chunk requests run concurrently. A cancelled, failed or stale request cannot replace the prior complete graph.
- The canvas admits 60, 120, 240 or 480 actual nodes and at most 900 original edges. It reports accepted, loaded and admitted counts separately, including nodes/edges outside the budget, boundary edges and viewport labels. Definitions and instances are never replaced by synthetic summary nodes. Parallel relations retain distinct IDs, strokes, direction and roles.
- 3D is a schematic depth layout using the same yaw/pitch projection approach as docs/examples/emergency-delivery-graph.html. Labels face the screen. It does not assign geographic positions. Drag rotates, Shift-drag pans, and the wheel zooms. Flat view locks rotation and preserves readable label size; pan to inspect a large neighborhood.
- Selecting a node shows original/canonical IDs, type, semantic level, exact literal/unit/time fields, separate fixture expectations, identity, aliases, source records, provenance, complete payload and incident relations. Rule definitions expose complete source-native AST trees, including ordered arrays, primitive literals and applicability AST. No evaluator runs and absent relationships are not synthesized.
- Coverage reports preserve unsupported/gap metadata, duplicate-ID diagnostics and missing endpoints. Full aliases, conflicts, coverage, signatures and input manifests are linked. Source-derived identity conflicts are retained, not silently merged.

## Checks

```sh
npm run check
npm test
node --test ../review/technical/ui_acceptance.test.mjs
```

When the separate offline HTML is absent, the four offline tests are explicitly skipped until `python3 build-offline.py` is run. Tests cover real dataset counts, every node kind and relation, exact source-object scene projection, explicit bounds, runtime literal/time preservation, global-index navigation, source-step filtering, rule/AST inspection, edge direction, pagination and failed-load rollback. DOM tests use jsdom only; in this supplied shared environment they can reuse the already-installed stable console's jsdom. For a separate checkout, run npm install for the declared development dependency.

These checks do not verify browser pixels, responsive layout, font availability or interaction rendering. Actual browser visual verification is pending on an authorized browser route. No screenshot or visual pass is claimed; the previously blocked local browser route was not retried or bypassed.

## Integration / test hooks

app.js exports createApp({document, fetchImpl, Renderer, manifestURL}). Set globalThis.__P08_TEST__ = true before importing to suppress browser autostart. The returned app exposes state, start, loadCases, selectNode, selectEdge, render, renderDetail, renderCoverage and destroy. model.js is DOM-free and exports makeIndex, queryNodes, chooseScene, layoutScene, projectPoint and factFields. renderer.js is a local Canvas 2D schematic projector; it does not require WebGL, Three.js or a CDN.

All mutation is confined to ui/. Graph input and source catalogs are not changed by this interface.
