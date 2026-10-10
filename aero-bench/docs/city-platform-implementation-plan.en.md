# City Platform Implementation Plan (editable city/scenario system)

Created: 2026-10-01. Branch `repair/inspection-v1-r5`, HEAD `a7c0a1e` plus the uncommitted Track A
work. This plan consolidates the remaining work into one list. The requirement rows (W1–W14,
V1–V16) and their evidence stay in the project's delivery-status record (session
record, omitted from this export). This
document assigns each open item an ID, owner, dependencies, next steps and acceptance evidence.
Interface changes go through the project coordination record (session record,
omitted from this export).

Status codes:

Sections 1–7 preserve Claude's original task breakdown and starting status. Section 8
is the current execution checkpoint; use it to distinguish implementation from
browser, GPU and formal-run acceptance.

| Code | Meaning |
| --- | --- |
| done | Implemented and accepted with evidence. |
| partial | Implemented in part, or implemented but not accepted. |
| missing | Not implemented. |
| blocked | Waiting on a named dependency. |

Owners:

| Owner | Scope |
| --- | --- |
| Claude | Frontend, visuals, editor, and the city-generation scripts in `frontend/`. |
| Claude (backend) | Since 2026-10-01 the user assigned the backend items in this plan to Claude as well: Python packages, contracts, Control routes, formal Providers. Backend changes follow the formal execution rules in `AGENTS.md`, run the backend tests, and are recorded as F-entries in the coordination doc. Codex's earlier B-entries remain the interface record. |
| Codex (takeover) | On 2026-10-01 the user assigned Codex to finish the remaining plan after Claude reached its session limit. Original ownership above records the earlier work. Current frontend/backend integration and remaining module work are owned by Codex; B-013 records the handoff. |

## 0. Accepted baseline (reuse; do not redo)

- A1–A3 ([ground doc](frontend-worker-ground-a1-20260930.en.md)) are accepted, with these published
  inputs:
  - road v3 `89ed7bfd…`
  - effective fixtures `779d65f4…`
  - traffic v2 `bfc3e236…`
  - flight v2 `2f45ac50…`
  - The default and render scenes pin all four.
- Rerun triggers (an input change forces the listed downstream checks):

  | Changed input | Checks to rerun |
  | --- | --- |
  | Road or network | Road gate, effective fixtures, SUMO recording, motion audit, scene re-pin, GPU acceptance |
  | A fixture model or placement | Effective fixtures, recording, audit, re-pin, GPU acceptance |
  | Anything placed in the motor band (for example street trees) | It becomes a motion obstacle: rebuild the motion obstacles, then rerun the recording and the audit |
  | Visual-only layers (vegetation outside the motor band, ground materials, lighting) | Unit tests and matched-camera GPU captures only |

- Backend:
  - The B1 R5 verified pass is accepted.
  - The compiled native Control run `f316faae…` is accepted, with verified public replay (B-012).
  - These are pre-takeover backend results. They do not satisfy the new I3 browser
    save/reload/compile/start/replay acceptance gate.

## 1. City generation, assets, buildings, reflections, roads, ground

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | Region-parametric build pipeline | partial: `build-building-render-runtime.py` hard-codes `shanghai-huangpu-east-v1` (lines 73–75) | Claude | — | Read region paths from the scene manifest (`city_preview_scene.py`). Drive every builder from `--scene`. | Huangpu rebuild is byte-identical. A second region builds through the same commands. |
| C2 | Asset library ledger | partial: ledger incomplete | Claude | — | Ledger records source, licence, hash and use for every model in use. Fail when a GLB is unlisted. | Ledger test over `public/models`. |
| C3 | Building quality (414 GLBs, facades, rooftops, materials) | partial: implemented, not validated (V1, V2, V4) | Claude | — | Near/mid/far matched-camera A/B. Fix measured defects only. | `capture-building-nearview.mjs` report and frames. |
| C4 | Reflections | partial: one static 256 px cube probe, 75 m, 12 buildings | Claude | E4 | Update the probe on time-of-day and weather changes. Measure the cost of a second probe. No SSR unless the frame budget allows it. | Moving-capture video plus frame-time numbers (U5, U2). |
| C5 | Roads, paving, crossings, fixtures | done (engineering preview, A1–A3) | Claude | — | Reuse. Wet-road shading is E3. | Existing A3 evidence. |
| C6 | Large gray ground areas | partial: (a) classification done 2026-10-01 | Claude | C5 | (b) Render tagged land use from source tags. (c) Authored landscape. Add the viewer-side drawn-set check for omissions; the script marks it `not_measured`. | `validation/platform-plan-20261001/G/ground-classification-report.json` and `-map.png`. Gray area of the 1.00 km² extent: unclassified (no source data) 24.3%, excluded tagged land use 22.7%, other tagged 7.0%, plaza 0, water 0. |
| C7 | Legacy scene pack-pin drift | partial | Claude | — | Night scene: placements re-derived against pack `8f70…` (507→508) and pinned 2026-10-01. Walkable/ground scenes stay pinned to `d4d5…`: re-deriving their placements changes the building basis of their SUMO recordings. They need a SUMO re-record or retirement. A hand re-pin was made and reverted. The legacy builders' default scene is now the night scene; `building_render` scenes get an explicit error. | Python suite: only `test_audit_sumo_rendered_footprints` (ground scene) still fails. |
| C8 | Legacy `city-workspace-geometry` 510/519 | done | Claude | — | The data changed in `ae331dc`; the test literal was stale and was updated (519, and 508 placements). | Vitest passes for the touched suites (`validation/platform-plan-20261001/L/`). |

C6 details:

- **(a) Classification.** New script `frontend/scripts/classify-city-ground.py`. It partitions the
  scene extent into these classes:
  - roadbed and walkbed (road v3)
  - building footprints
  - recognised OSM greens
  - excluded OSM land use: residential, construction, commercial, retail, brownfield, pitch
  - plazas: `highway=pedestrian` areas and `place=square`
  - water
  - none of the above: no source data

  It also counts rendering omissions: source polygons the viewer should draw but does not. It
  compares the parser's accepted set with the drawn meshes. The report gives area by class,
  polygon IDs and an overhead class map.
- **(b) Rendering.** Real open spaces get surfaces from their source tags (plaza paving, pitch,
  construction ground, water). Areas with no source data stay the explicit neutral "unclassified"
  surface and are labelled in the inspector. Nothing is invented.
- **(c) Authored landscape.** Add a separate authored file and the matching draft field. Each item
  carries provenance `authored` and is shown labelled as a design addition. Items can be created,
  edited and deleted in the editor. The parser accepts them only from the authored file, never
  mixed into OSM.
- **Acceptance:**
  - Classification report: the omission count is 0, and every remaining gray square metre is
    attributed to a class.
  - Before/after overhead and street-level captures with matched cameras.
  - Provenance tests.

## 2. Vegetation, weather, lighting (bound to accepted roads)

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | Vegetation road clearance | partial: implemented 2026-10-01 | Claude | C5 | GPU captures with matched cameras. The default scene now plans vegetation against verified road v3 and the effective fixtures (`roadGeometry: "published"`), and an environment source without `road_assets` is a scene-config error. | Real-data test: 0 overlaps; trees 157→138, clump placements 18236→16233, greens 7. `validation/platform-plan-20261001/V/`. GPU capture pending. |
| E2 | Wind (trees, grass, shadows) | done (limited, V10) | Claude | — | Reuse. | Existing report. |
| E3 | Rain and wet surfaces | partial: wetness only on the ground plane and grass | Claude | E4 | Apply `applySurfaceWetness` to road, sidewalk and crossing materials, with limits for facades. Couple it to precipitation and rate. | Matched dry/rain captures (roads darker, with specular). Module tests. |
| E4 | Day/twilight/night lighting | partial: roads, buildings and vehicles map night to dusk (`map.ts:744`) | Claude | — | Add `night` to the road, building-window and vehicle-lighting moods: lamp pools, window emission and headlights. Keep the calibration tests. | Matched captures for 3 times of day × 2 weather presets. Luminance numbers per frame. |
| E5 | Weather presets and particles | done (V11) | Claude | — | Reuse. | Existing evidence. |

## 3. Traffic, signals, pedestrians, bicycles, UAVs, cameras

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | Recorded traffic playback (signals, vehicles, pedestrians, bicycles) | done (preview, A3) | Claude | — | Reuse. | A3 audit and GPU acceptance. |
| T2 | Traffic config regenerates SUMO demand (W7) | missing (filtering only) | Claude (offline preview, then formal SUMO Provider binding) | T1 | Editor demand parameters feed the existing recording builder (pinned SUMO image). The output is a new preview file that passes the motion audit. The formal SUMO Provider binding follows I5. | Two demand settings give two audited recordings with different counts. |
| T3 | UAV visualization | done (preview) | Claude | — | Reuse. | — |
| T4 | Camera and follow controls (free/chase/cockpit, actor follow) | partial: follow not recorded (V14) | Claude | U5 | Follow a vehicle, a pedestrian, a bicycle and a UAV. Check that the camera is not obstructed. | Video of each follow mode, plus the frame-time report. |

## 4. Unified editor

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| ED1 | One draft model | partial: workspace v2 plus three "selected" drafts with conflicting enums | Claude (frontend and backend schema) | — | Propose workspace v3 in F-004. It absorbs orders and order generation, the seed, authored landscape and logistics algorithm enums. v2 migrates explicitly. The "selected" drafts become read-only import sources. | `aero_bench/authoring/workspace.py` and the compiler accept v3. Generated contracts regenerated (`contracts:check`). Migration tests. |
| ED2 | Facilities, airspace, fleets, traffic, algorithms, events editors | done for v2 fields (W4–W6, W9–W10) | Claude | — | Revalidate on the default scene: snap, collision and capacity against road v3. | Editor browser review on the current scene. |
| ED3 | Orders editor | missing in workspace v2 (exists only in the logistics draft) | Claude | ED1 | Move the order editor onto v3. | Browser save/reload/export round-trip. |
| ED4 | Seed editor | partial: control added 2026-10-01 (`city-runtime-panel.ts`, test passes) | Claude | — | Browser round-trip in the next editor review. | Unit test passes; browser round-trip pending. |
| ED5 | Save/restore, import/export | done (W11, 17-check review) | Claude | — | Extend the checks to v3. | Rerun the editor review. |
| ED6 | Event consumption | missing: saved, not consumed | Claude (preview display; backend runtime later) | ED1 | Preview: show scheduled events on the timeline. Formal consumption is backend. | Timeline test. Backend run evidence. |

ED1 design (workspace v3):

- **New fields.** v3 adds three fields to v2:
  - `orders`: the exact `LogisticsOrderRequest` shape. The backend `AuthoredOrderRequest`
    (`aero_bench/tasks/logistics/authoring.py`) already accepts it by alias.
  - `orderGeneration`.
  - `performanceProfiles`, keyed by fleet entry ID.

  Order facility references must name v3 `facilities`. Hub handoff follows `effectiveHubHandoff`.
- **Algorithms.** The v2 `algorithms` enums stay as they are. No backend code implements any named
  algorithm; they label the agent workload. Importing a selected-logistics draft maps only `external`
  to `external`, and its `externalImageRef` to `deployment.imageRef`. Every other algorithm value is
  an explicit import error. It is never guessed.
- **Authored landscape.** `authoredLandscape[]` (C6(c)) holds polygons with
  `provenance: "authored"`, `kind` (`green`, `plaza`, `planting_strip`) and a label. These are visual
  inputs, not physics.
- **Migration.** v2 migrates to v3 explicitly: the new arrays start empty. The v2 storage key stays
  untouched, as v1 did before.
- **Sequencing.** The accepted native reference registration embeds a v2 `reference_draft`, built
  by `aero_bench/authoring/reference_registration.py`. v3 therefore changes the registration
  identity. Order of work:
  1. Do I3 on the current v2 pins first.
  2. Then land v3.
  3. Re-issue the registration.
  4. Recompile, and rerun the formal reference once to restore the accepted pins.
- **Compiler.** It treats `orders`, `orderGeneration`, `performanceProfiles` and `authoredLandscape`
  as retained authoring input. Each one gets a field-pointer blocker unless the registration lists
  it as editable.

## 5. Compile / run / verify / replay integration

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| I1 | Authoring client: native-scene catalog and compilations | done (unit level) 2026-10-01 | Claude | — | Browser check in I3. | `city-authoring-api.ts` uses generated validators (added to `tools/generate_contracts.py`). Studio tab 05 "编译运行". 20 tests pass. |
| I2 | Explicit native reference scene selection | partial | Claude | I1 | Select the `public-scenario/v1` document through the existing public-scenario renderer (B-011). Never pass it to `parseCitySceneConfig`. | Test plus capture. |
| I3 | Browser save → compile → run → verified replay on the explicit reference (B-012 request) | missing | Claude | I1, I2, I4 | Run the authoring API on 8124 and Control `serve` on 8123 (pass `--port`; the default is 8765). Credentials stay in memory. | Recorded browser run: compiled ID, Run ID, phase `verified`, replay loaded. Report under `validation/`. |
| I4 | Replay of a finished run over HTTP | done (unit level) 2026-10-01 | Claude (backend) | — | Frontend `ControlClient` methods and app loading after a terminal run, then I3. | `GET /v1/runs/{id}/public/trace` and `/public/replay-manifest`: exact sealed bytes, digest-checked, 409 until terminal. `tests/test_control_public_replay.py` (15 tests); 21 Control tests pass. |
| I5 | Shanghai city execution | missing: no native WorldPackage | Claude (backend) | I3, C5 | Register the Huangpu scene as a native WorldPackage with digest-pinned inputs, extend the lowering, and run it in `docker_reference`. Large; starts after I3. | Formal run, sealed artifacts, verifier pass. |

## 6. UI, performance, multi-region, regressions, captures

| ID | Item | Status | Owner | Depends on | Next steps | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| U1 | Formal, polished UI | partial | Claude | — | Design tokens in `styles.css`: type scale, spacing, surface and accent colours. Consistent panels, status chips and provenance labels (authored / preview / formal). Accessibility basics. Interface language is unchanged. | Before/after screenshots of every panel. No vitest regressions. |
| U2 | Performance checks | partial | Claude | — | The overlay is in the viewer (`?perf=1`, F9). Measured during video capture on an RTX 3090: 17–19 FPS, frame p50 50 ms, p95 67 ms. Profile it without recording (CPU update vs GPU vs shadow/reflection passes) and set budgets after the measurement. | `validation/platform-plan-20261001/P/*/report.json`. |
| U3 | Multi-region validation | missing | Claude | C1 | Build a second region (Jing'an source exists) through the same gates. Run the same acceptance scripts. | Second-region reports and captures. |
| U4 | Regressions | partial (vitest 785/786, Python 4 errors) | Claude | C7, C8 | Fix, then record clean suites. | Logs. |
| U5 | Matched-camera screenshots and videos | done (tooling) 2026-10-01 | Claude | — | Use it for T4, C4, E3 and E4 evidence. | `scripts/capture-city-video.mjs`; 4 `.webm` segments in `validation/platform-plan-20261001/P/`. |

## 6a. Findings from wave 1 (2026-10-01)

- **Backend contract defect, fixed.** `aero_bench/authoring/workspace.py` placed `Field` bounds
  after a `BeforeValidator`. The published JSON Schemas therefore carried raw `ge`/`le` keys
  instead of `minimum`/`maximum` for the draft seed, counts and capacities: the schemas did not
  enforce those bounds. Runtime validation was correct. Fixed, the contracts were regenerated, and
  164 authoring tests pass.
- **ED2 finding.** The studio spatial tab still states that road geometry awaits the native gate,
  and its facility collision check uses building envelopes and no-fly zones only. It must use road
  v3 now.
- **ED6 and B-012.** `editable_execution_fields` in the generated catalog schema allows `/seed` and
  `/deployment/executor`. B-012 text lists only `/seed`. Check the live catalog in I3.

## 7. Execution plan

Workers are native Claude Code subagents (Sonnet), working in the shared tree. Worktrees would
branch from HEAD without the large uncommitted state. Each worker owns separate files. The main
session alone edits `map.ts`, `app.ts`, scene manifests, docs and coordination entries. It also
does review, integration and GPU acceptance.

Wave 1 (completed 2026-10-01), run in parallel:

| Worker | Items | Owns | Required tests |
| --- | --- | --- | --- |
| G | C6(a) classification | `scripts/classify-city-ground.py`, `scripts/test_classify_city_ground.py` | Python unit tests and a real run on the default scene |
| V | E1 core | `city-vegetation-layer.ts`, `city-environment.ts` and their tests | Vitest for the touched suites and typecheck |
| I | I1 | `city-authoring-api.ts`, new `city-compile-panel.ts`, their tests | Vitest and typecheck |
| P | U2 overlay and U5 video | new `city-perf-overlay.ts` + test, `scripts/capture-city-video.mjs` | Vitest and typecheck |
| L | C7, C8 | the legacy scene placement files, `city-workspace-geometry.test.ts`, `city-scene-config.test.ts` | Python suite and vitest |
| R | I4 public replay routes | `aero_bench/control/manager.py`, `server.py`, contract generator and replay-route tests | Control tests and generated-contract checks |

Main session during wave 1:
- ED4 seed control
- F-004 coordination entry (workspace v3 proposal; replay route request)
- `map.ts` wiring for E1 and P

Wave 2:
- C6(b, c)
- E4 then E3
- I2, then I3
- T4 videos
- U1
- C1 then U3
- T2

Wave 3:
- ED1/ED3, after I3 and the v3 design review (accepted for implementation in B-013)
- C3/C4 acceptance
- Full regression
- GPU acceptance on the default scene and the second region
- Docs

Every wave ends with these checks from the main session:
- `npx vitest run`
- `npm run -s typecheck`
- `npm run -s build`
- `python3 -m unittest discover -s frontend/scripts -p 'test_*.py'`
- GPU captures with the stored cameras
- An update to the delivery-status rows

## 8. Codex takeover checkpoint

Updated: 2026-10-01 00:06 PDT. This checkpoint supplements the original rows above;
module implementation is not browser, GPU or formal-execution acceptance.

| Items | Current result | Remaining acceptance | Evidence |
| --- | --- | --- | --- |
| C1, U3 | Regional builders are parameterized. The Huangpu runtime rebuild is byte-identical across 506 files. Jing'an's 43 buildings, accepted roads/fixtures, real SUMO recording, PASS motion audit and visual flight are byte-checked and published. Explicit region selection replaces the draft only after the new spatial source verifies. | Browser failure-preservation, switch/reload/return checks and the same GPU acceptance as Huangpu. | `validation/codex-takeover-20261001/C/publication-candidate/publish-receipt.json`; `frontend/scripts/capture-city-region-switch.mjs` |
| C2 | Canonical v2 ledger lists 782 GLBs: 325 public models and 457 regional building assets, including all 43 Jing'an buildings. Independent audit passed with zero issues. Multi-region inventory/audit handling passes 8 focused tests. | Complete for the current published inventory; rerun if another asset is published. | `frontend/asset-library-ledger.json`; `validation/codex-takeover-20261001/F/asset-ledger/` |
| C3 | Sequential hardware A/B passed: 12 controlled-baseline and 12 candidate frames, matched cameras and application identity, 414/414 loaded buildings, no application/scene errors. Reviewed near/mid/far facade and roof views in day/dusk; no blocking clipping, seam, texture loss or roof penetration was observed. | Complete for the matched visual comparison. Baseline metadata adaptations are private visual fixtures, not physical traffic or formal evidence. Roof additions are modest and original-reference fidelity remains unmeasured. | `validation/codex-takeover-20261001/K/buildings/gpu/review/visual-review.json`; `K/buildings/handoff.json` |
| C4 | Weather/time dirty updates and probe isolation passed actual GPU capture checks. Measured one-probe p50/p95: 10.4673/11.4533 ms; two-probe: 22.9171/26.0024 ms. | These costs are diagnostic because the host GPU was saturated. Keep production at one probe; do not infer an uncontended frame budget. | `validation/codex-takeover-20261001/R/report.json` |
| C6(b) | Actual GPU drawn-set check: 19/19 published source-tagged covers, no omissions. Existing source greens are unchanged. | Matched final captures; source-unclassified ground remains explicit. Rendering every known cover does not resolve all gray land. | `validation/codex-takeover-20261001/K/` |
| C6(c) | Active v3 draft applies, replaces and clears authored landscape against verified source footprints and road surfaces. Real browser round-trip passed with 864 m² of labelled authored green geometry, weather/remount, save/reload and JSON export. | Complete for preview authoring; no OSM or physics claim. | `frontend/src/map.authored-landscape.test.ts`; `validation/codex-takeover-20261001/U/studio-v3-roundtrip-run4/report.json` |
| C7, U4 | Stale legacy acceptance defaults are retired; historical assets/evidence remain intact. Stable full backend rerun passed 2,748 tests with 5 explicit skips. Before native renderer changes, 968/968 frontend tests, 415/415 frontend Python tests, typecheck and 86 generated-contract checks passed. The earlier concurrent traffic-profile snapshot failure remains recorded. | Repeat frontend regression after native renderer integration. | `validation/codex-takeover-20261001/integration/backend-regression-rerun-junit.xml`; `validation/codex-takeover-20261001/L/` |
| E3, E4 | Wet road/paving/crossings, explicit night lighting and matched weather/time captures are implemented. Claude corrected lamp emission and placement using the actual lens geometry. | A final night recapture must include the corrected lamps; visual weather is not a formal Weather Provider. | `validation/codex-takeover-20261001/N/`; coordination F-004 |
| ED1, ED3, ED6 | Strict workspace v3 is active across backend, contracts, Studio and storage. Real browser edit round-trip passed 11/11 checks for facilities, orders/generation, seed, environment, authored landscape, events, save/reload/export. The new v3 reference completed and independently verified. | Retained noneditable fields still receive compiler blockers. Preview event timeline is accepted; general formal event lowering/consumption remains open. | `validation/codex-takeover-20261001/U/studio-v3-roundtrip-run4/report.json`; `I3-v3/browser-flow-report.json` |
| ED2, I2 | Facilities use verified published road clearance. R5 remains explicitly nonrenderable and compilable. A separate declared native-city route now verifies four JSON layers and per-building GLBs, with scene/world identity and exact GLB placement checks. Its real I5 loader audit loaded 414 buildings and 160,364,476 bytes. | Explicit city-inspection catalog profile/publication and fresh hardware browser capture. Headless image decoding was stubbed, so the loader audit is not visual acceptance. | `validation/codex-takeover-20261001/S/`; `frontend/src/native-city-presentation.ts`; `R/native-city-headless-load.json` |
| I3 | v2 and fresh v3 browser flows are accepted, each 13/13 checks. V3 Run `1b0d3424…` passed the independent verifier, loaded verified replay in the same browser and independently audited 42 files / 106,071,200 bytes. | Complete for the declared R5 inspection reference. This embedded replay gate does not accept city-scale indexed delivery. Earlier failed attempts remain failed. | `validation/codex-takeover-20261001/I3-v3/browser-flow-report.json`; `I3-v3/report.md` |
| I5 | V5 failed at tick 300 with Harness OOM, exit 137, before sealing; it has no verdict or public trace. A measured capacity model selects 32 GiB and 9,000 s for fresh v6 Run `44f2ebb7…`, started 2026-09-30 23:50:31 PDT. Scenario, Providers, OCI images and assets are unchanged. Control indexed-file routing and separate 512 MiB asset / 1 GiB trace limits pass 46 focused tests. | V6 sealing, independent verification, large indexed replay delivery and native browser rendering. Weather physics, Airspace enforcement and physical logistics remain separate unimplemented formal capabilities. | `validation/codex-takeover-20261001/B/huangpu-native-runtime-capacity-v6.json`; `integration/control-regression-junit.xml` |
| T2 | Two earlier real demand recordings passed independent motion audits. Strict v3 job API, pinned registry, Studio controls and atomic whole-trace map application are integrated. The first fresh v3 job failed its observed-bus gate and correctly published nothing. A new 10-motor / 6-person / 3-bicycle, seed-202 job is recording under profile `a07f96e6…`. | Fresh v3 API POST→job→audited artifact and real-browser application; stale-draft rejection. Offline preview never claims formal execution. | `validation/codex-takeover-20261001/D/`; `T2-v3-live/`; `city-audited-traffic.test.ts` |
| U1, U2, U5 | v3 visual review passed 88 checks across 24 frames, six tabs and four modules. Bicycle/UAV follow captures passed. | Final scene publication, vehicle/pedestrian follow clips and frozen integrated frame profile. The earlier drifting-actor profile is diagnostic only. | `validation/codex-takeover-20261001/U/acceptance-v3-run2/report.json`; `F/dynamic-pass/` |

### 8.1 Final verification by Claude (2026-10-01 11:45 PDT)

Executed on the current working tree after all W1–W6 lanes and Codex's UI
integration had stopped editing. This supersedes the "Current result" column
above where they differ; failures and open items remain listed.
Evidence: `validation/platform-plan-20261001/final-verify/`.

| Check | Result |
| --- | --- |
| Backend `python -m pytest -q` | 2,933 passed, 5 skipped, 0 failed (`backend-pytest.log`) |
| `tools/generate_contracts.py --check` | 90 generated files match |
| Frontend `vitest run`, typecheck, build | 1,220/1,220 tests in 131 files; typecheck and production build pass |
| Frontend script tests | Python unittest OK; `node --test` 49/49 from both the repository root and `frontend/` |
| Sealed formal runs, re-hashed from disk (`recheck_sealed_runs.py`) | Native inspection v8 `c69f303f…`: 14 sealed artifacts (1,440,913,091 bytes), verifier passed 15/15, 442 content-addressed replay files re-hashed, 443/443 terminal replay files match. Traffic-restriction R5 `c073afde…`: 14 sealed artifacts, 15/15 goals. Logistics `order.created` v7 `316379c1…`: 5 sealed artifacts, 1/1 goal. All `docker_reference`, `formal_benchmark`. |
| GPU-3 captures (RTX 3090, ANGLE/Vulkan, PCI 89:00.0) | Region switch 8/8 (the one console error is the injected 503 fixture); Jing'an matrix 86/86; Huangpu E3/E4 weather × lighting matrix 86/86; native-city gate 10/10 with 4 screenshots and no page or console errors |

Corrections made during verification:

- W6's collapsed "城市控制" dock moved the OSM ground-cover provenance chip
  into hidden content, so the Huangpu matrix failed its visible-provenance gate
  (run preserved in `final-verify/run1/`). `viewer-chrome.ts` now keeps labelled
  `.provenance-chip[data-provenance]` nodes visible beside the toggle; other
  controls still collapse. The collapsed dock no longer paints an empty surface.
- `capture-city-native-presentation.test.mjs` assumed `frontend/` as the
  working directory; CLI output paths resolve against the caller's directory.

Reviewed, not changed:

- W5's static before/after comparison differs by 251 overview and 7 street
  pixels (recomputed from the raw readbacks). Magnified review shows isolated
  pixels on alpha-tested foliage, thin poles and silhouette edges; no geometry,
  lighting or texture change. This is reviewed visual equivalence, not bitwise
  identity. The short-warmup repeatability failure remains unexplained.
- The user's authoring service on port 8124 runs Sep 28 code without the
  traffic-preview route. Final captures used a current-tree authoring API on
  port 8151 with the W4 native-scene registration and pinned profile manifest.
- `docker_owner_token` in a takeover evidence JSON is the executor's container
  ownership label (`aero_bench/executor/docker.py`), not an authentication secret.

Open:

- Native-city street view: the bottom-left source note overlaps the minimap
  legend (layout owned by the integrated `map.ts` monitor).
- Codex's browser gate records the camera-preview/event-strip overlap, a
  disabled source selector, and no actionable sealed event with declared
  severity and location.
- Formal capability blockers: weather physics, airspace enforcement, physical
  logistics and charging. `kubernetes_cluster` remains deferred.
- Hardware compositing is unavailable on this host; headless readback dominates
  frame time, so measured FPS is not a desktop budget.

### 8.2 Owner handoff gates 1–2 (2026-10-01 18:55 PDT)

Executed by Claude against the real Control service (port 5393, sealed v8 only)
and the workspace replay picker on a current-tree Vite server. No Control write
was issued. Evidence: `validation/ui-calibration-20261001/layout-gate/` and
`validation/ui-calibration-20261001/state-transitions/`.

Gate 1, layout (closes the three overlap items in 8.1 "Open"):

- Opening a Control replay rebuilds the application shell. The viewer chrome
  header stayed on the detached map, so replay had no chrome and every chip
  fell back onto the monitor. `viewer-chrome.ts` now re-homes the header on the
  new map and discloses the new shell's source note.
- With the chrome present, the monitor reserves a 40 px top band for the
  chrome header and a 58 px bottom band. The scenario badges moved from top
  centre (under the monitor clock) into that bottom band beside the legend.
  The scene toggle follows the narrower right rail at ≤1366 px. At ≤1300 px the
  replay source picker takes a second 40 px header row instead of covering the
  map. At ≤1700 px the subtitle hides while the picker is shown, so the
  top-bar buttons do not wrap.
- Layout workflow run3 (`layout-gate/run3/`, 1600×900 and 1280×800): every
  state has empty chrome/monitor overlap once telemetry is minimized. The only
  overlap left is the PFD's expanded state before the user minimizes it. The
  preview is fully visible, with zero overlap against the event strip.
- GPU-3 re-run on this tree (`layout-gate/gpu-gates/`): region switch 8/8,
  Jing'an 86/86, Huangpu 86/86, native city 10/10 with no page or console
  errors. The native street-view source note now sits under the clock card,
  clear of the minimap.

Gate 2, state transitions (`state-transitions/run3/`, 25/25; `run4/`
re-checked source switching after the map fix below, 12/12):

| Transition | Exercised with | Result |
| --- | --- | --- |
| Source switching | Workspace picker, real sealed public traces: v8 inspection → R5 traffic → v8 | Header names the new run, no visible text names the previous run, metrics change (525/298/918 ↔ 47/524/1,596), the new source starts unselected |
| Missing sensor | v8 entities | UAV onboard camera enabled once the city is ready (positive control); bicycle and vehicle onboard camera disabled. The gimbal button is a static "unavailable" label, not data-driven |
| Control unreachable | Unused port 5399 | Explicit error, credentials cleared, no catalog |
| Control lost mid-session | Labelled browser-side request abort after a real catalog load | Explicit error; the previous catalog is no longer presented as current |
| Explicit disconnect | Real Control | Catalog cleared, run controls disabled |
| Stale telemetry | Not exercised | No sealed trace (v8, R5, logistics v7) contains a sample older than its tick; covered by unit tests only |
| Live channel interruption | Not exercised | Needs a live run; none was started, so no writes were made |

Control offers one sealed run, so the two-source switch used the workspace
trace picker, not Control. The current registered replay still has a disabled
single-source placeholder.

Defects found and fixed:

- `disconnectControlService` left the old catalog and run controls actionable.
  It now clears the catalog and disables the run controls.
- A selected entity id carried into a newly loaded source, where it names a
  different object. `loadDocument` and `loadSealedReplay` now clear the
  selection.
- While no presentation was bound, the map showed "等待官方场景网格发布"
  beside the application's real loading progress. For R5 the panel stayed
  next to the explicit failure indefinitely. The map now leaves progress and
  failure to the application. R5 remains explicitly non-renderable on the
  native route (it declares `buildings` and `missions` layers in addition to
  the native route's four required layers); this is the documented state, not
  a new regression.

Frontend checks after these changes: vitest 1,226/1,226 in 131 files,
typecheck pass, scratch build pass (`--outDir`, not `frontend/dist`), node
script tests 49/49.

Still open: the error toast and the expanded PFD share the lower-right corner
when both are shown.

### 8.3 Owner handoff gates 3–4 (2026-10-01 19:16 PDT)

Gate 3, incident/logistics evidence: not closable with current sealed runs.
The measured gap and a narrow, ordered backend proposal are in
[incident-logistics-public-record-proposal.en.md](incident-logistics-public-record-proposal.en.md).
In short:
- v8 and R5 each have two `mission.event.v1` records, which are ns-3 delivery
  records with `severity: info`.
- The projector validates their `severity` and `message`, then drops them.
- Logistics v7 publishes no events.
- The viewer's full exception workflow remains covered only by labelled
  fixtures in `operations-data.test.ts` and `operations-monitor.test.ts`.

No fixture was added to sealed evidence.

Gate 4, rendering profile. Evidence:
`validation/ui-calibration-20261001/render-profile/` (script
`profile-observation.mjs`; runs `run2` baseline, `run3`, `run4`; each has
`physicalDeviceProof: PASS`).

Declared conditions:

| Condition | Value |
| --- | --- |
| GPU | GPU 3 (RTX 3090), pinned via `run-capture.mjs`; 0% utilization at start |
| Browser | Headless Chromium, ANGLE/Vulkan (no hardware compositing on this host) |
| Viewport | 1600×900 at DSF 1 |
| Build | Vite dev server (unminified, for function-level profiles) |
| Scene | v8 workspace replay: 1,162 draw calls, 2.48 M triangles; `uav.inspector` selected; PFD minimized |
| Host | 96 cores, load average 15–22 |
| Phases (20 s each) | A 1× with preview closed; B 1× with preview open; C 2× with preview open |
| Sampling | CDP CPU profile at 1 ms; the map's `data-*` timings; a timed `readPixels` wrapper; rAF intervals; long tasks |

No GPU timer queries were used, so the GPU cost is bounded only by the
readback stall.

Measured per path:
- **Main render:** CPU submit 12–15 ms per tick.
- **Secondary camera:** 22–23 ms per refresh, of which the synchronous
  `readPixels` stall is 8.5–8.9 ms (first readback 8.5–9.3 ms).
- **SVG minimap:** the minimap `renderMap` is 12–17 ms per update (about 2.5% of
  CPU).

None of these paths dominated. The handoff's earlier 510 ms first readback and
73–130 ms "minimap" samples came from contended runs, and `minimapUpdateMs`
measures the entire monitor update, including snapshot construction.

The actual hotspots were outside rendering. All three are fixed, with tests:

| Hotspot (run2 CPU per 20 s phase A/B/C) | Cause | Fix |
| --- | --- | --- |
| `TerminalDock.renderActive` 2.6 / 6.8 / 9.1 s | Each replay tick rebuilt up to 400 rows per channel and forced layout | Keyed incremental append/drop; full rebuild only on seek back, channel or language change |
| `latestOperationTask` 1.8 / 2.3 / 0.8 s | Called once per entity, each call re-scanning and re-validating every event (525 × 918) | `latestOperationTasks`: one pass per snapshot, same newest-by-time-then-sequence rule |
| `TelemetryHud.renderHud` and mini cards 0.53 / 0.60 / 0.47 s | Canvases redrawn every tick while hidden by the minimized state | Skip while minimized; redraw the latest state on restore |

Result, run2 → run4:

| Phase | Monitor update p50 | Long task p50 | rAF frames per 20 s |
| --- | --- | --- | --- |
| A | 83.9 → 35.0 ms | 137 → 83 ms | 507 → 806 |
| B | 102.3 → 37.5 ms | 291 → 107 ms | 158 → 735 |
| C | 125.2 → 41.3 ms | 423 → 114 ms | 66 → 288 |

The HUD fell to 9–22 ms per phase. The predicted monitor range was
25–40 ms; measured 35–41 ms. These are headless measurements, not a desktop
FPS budget. Remaining per-tick CPU is spread across:
- app `renderAll`, about 11 ms;
- snapshot construction, about 9 ms;
- native "(program)" time, which includes driver submission.

Gate 5 (wallboard/comparison) was not started.

Frontend checks after gates 3–4: vitest 1,230/1,230 in 131 files,
typecheck pass, scratch build pass, node script tests 49/49. The gate 2 browser
check re-ran on this tree (`state-transitions/run5/`): 25/25.

Research asset authorization is user-declared: the research group shares the assets
internally and the mentor authorizes research use. Original licence fields remain
unknown where no source record supplies them; that does not block this assigned
research development. No CC0 or public redistribution claim is inferred. OSM's
recorded source licence is a separate provenance record.
