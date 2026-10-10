# Provider-neutral integration boundary

## Intended host

AERO_BENCH remains the physical authority and production Three.js monitoring host. AeroAgentSim owns configuration and provider-neutral contracts. Atlas receives normalized evidence and binding-isolated evaluation contexts. The fixture console is linked from the existing workbench at `/configuration/index.html`; this full-page authoring route remains separate from the future private monitoring component mount.

## Files

- `src/config.js`: desired configuration, validation, structural diff, desired-plan compilation, SceneState projection and independent SSE cursor handling
- `src/network-study.js`: public-reference research profile catalog, explicit Apply/diff, unit/source metadata and study validation
- `src/runtime.js`: browser workspace persistence, version snapshots, bounded authored fixture preparation, seek and shared selection/evidence keys
- `src/app.js`: presentation and user workflows
- `src/style.css`: responsive interface styles, local system fonts
- `src/i18n.js` / `src/translations.js`: persisted Chinese/English authored UI copy; interpolated user values and source titles remain unchanged
- `src/observation-contract.js`: fixture-to-neutral observation projection, nested revision keys and exact/previous seek boundaries

The runtime imports no simulator global state, native run repository, source artifact path reader or external connection client. There are no calls to `/api/runs`, TraCI, SimPy or ns-3.

## Configuration contract

`aero-console.config/v1` is an independent desired-input format. `compileConfig()` is not a BENCH scenario compiler. It emits a detached desired plan and diagnostic records, preserves unsupported inputs, and does not claim the plan is runnable by an existing source service.

Stable template IDs expand explicitly. A count of one preserves the ID; a group expands as `template-1`, `template-2`, etc. Bindings refer to exact instances. Native aliases require explicit join rows when the backend is connected; prefixes and array position are never identity.

Research fields stay in `network.study`, have a profile revision, and retain `execution: research_only`, `calibrated: false`. Changing a source-reported or derived reference value reclassifies that field as a research assumption. Additional geometry, channel and antenna requirements are unimplemented hints, not executable provider options.

## Motion projection seam

`normalizeSceneState(scene, entities)` accepts the documented serialized shape, including `samples`, `pose.position.enu`, `linear_velocity_enu`, exact tick/string-safe nanoseconds, identifiers and digest references. It checks required envelope consistency; it does not authenticate the source or recompute serialized content hashes. Barrier readiness remains unverified until a host adapter validates the actual barrier and integrity chain.

ENU analysis coordinates remain [east,north,up]. The future BENCH renderer mapping is [east,up,-north] and must happen only at its rendering boundary. The fixture preview displays ENU directly. No orientation or body-extent inference is made.

## Transport seam

`advanceSourceCursor(cursor, event)` keeps transition, scene and public-event source cursors independent. Their initialization is -1 / 0 / -1. Cursor metadata can retain attachment, epoch and manifest revision. This is not a live SSE client.

Actual authenticated fetch SSE and sealed replay ingestion are future adapters. A production sealed reader must verify manifest/index/content-addressed shard integrity, path/size/count limits and references, then preload enough previous shards for temporal history. The authored fixture array is not a BENCH replay shard implementation.

## Host view/evaluator seam

The selection uses `aeroagentsim.selection/v1`, with a nested Python-compatible `ViewKey`: attachment/run/epoch/manifest, frame sequence and observation-byte hash, stage evidence key, evaluation revision and binding epoch. The fixture explicitly uses `not-evaluated`; this is a display revision token, not a native Atlas result. Rule truth is null. Entity locks persist with the run epoch and manifest revision and do not substitute previous values during evidence gaps.

The observation export is accepted by the independent Python `ReadOnlyReplay` through an injected opaque `MappingArtifactReader`. It does not use `/api/runs` or a guessed native replay schema. Every exported observation hash covers the exact canonical UTF-8 bytes checked by that reader. Gaps in entity motion remain explicit null values inside complete frames; the fixture does not invent a global source gap.

For real integration, the host supplies an immutable coherent frame to a component-level `setFrame(frame, selectionKey)` boundary. A versioned `selectTarget`/`selectBinding` selection store must drive renderer highlighting, exact state inputs, thresholds and rule/evidence panels. Stale responses must be discarded after seek, run, epoch or revision changes. It must not invoke fixture loading or simulator commands.

Use the existing Atlas evaluator only after its code/data is authorized and available. A separate Context is needed per compatible binding/history/parameter envelope. Full snapshots, source validity gaps, binding lifecycle, exact engine-time origin and event-censoring policy are prerequisites. No simplistic substitute evaluator is included here.

## Remaining work

1. Human/browser visual QA of the included UI in an allowed local preview.
2. Integrate this source into the intended AeroAgentSim frontend path, preserving the repository's architecture and existing work.
3. Bind an authorized resource catalog and actual configuration compiler with complete unsupported-field diagnostics.
4. Connect read-only verified replay ingestion and mount into the private BENCH Three.js host.
5. Attach the authorized Atlas runtime/catalog and prove binding/time/history parity and synchronized visual evidence.
6. Only then connect authenticated live read-only transport; real run controls require the declared BENCH capability and explicit authorization.

No private-source distribution or real simulation is implied by completion of this prototype.
