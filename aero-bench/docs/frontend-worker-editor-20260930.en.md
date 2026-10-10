# Frontend worker report — editor environment (2026-09-30)

Unit: `editor-environment` · branch `repair/inspection-v1-r5` · all work in `frontend/`.

## What changed

### Task 1 — shared draft-environment domain model (`city-draft-environment.ts`, new)

- `CityDraftTimeOfDay = CityTimeOfDay` (`"day" | "twilight" | "night"`), re-exported from
  `city-lighting-calibration`.
- `CityDraftEnvironment extends CityWeatherSettings` adds `timeOfDay` and `reflectionsEnabled`
  (8 fields total, exactly).
- `parseCityDraftEnvironment(value)` accepts only JSON objects with exactly those 8 keys; each field
  is validated by type/range (timeOfDay allowlist, boolean reflection flag, weather settings handed to
  the existing `checkCityWeatherSettings`, which throws `RangeError` on out-of-range values).
- `migrateLegacyEnvironment(value)` accepts a JSON object with mood `"day"` or `"dusk"` plus weather
  fields and returns the draft environment; `dusk` maps to `twilight`, `day` to `day`, and any other
  mood is rejected with `Legacy city environment mood must be "day" or "dusk", got …`. No legacy
  value other than day/dusk is invented.
- `weatherOf(environment)` returns the `CityWeatherSettings` view (used to feed the weather layer).
- `city-draft-environment.test.ts` covers parse round trip, exact-key rejection, out-of-range
  rejection, and day/dusk migration (7 tests).

### Task 2 — workspace config v2 (`city-workspace-config.ts`) and runtime panel

- New schema `"aero-bench.city-workspace/v2"` with storage key `"aero-bench.city-workspace.v2"`.
  The old schema/key are kept as `CITY_WORKSPACE_LEGACY_SCHEMA` / `CITY_WORKSPACE_LEGACY_STORAGE_KEY`.
- `environment` is now a `CityDraftEnvironment`; the Ajv schema requires `timeOfDay` in the
  day/twilight/night enum; the default environment is the previous default plus `timeOfDay: "day"`.
- `parseCityWorkspaceConfig` accepts v2 and (migration entry) v1; `exportCityWorkspaceConfig` and the
  save path always write v2.
- `migrateCityWorkspaceV1(legacy)` validates the v1 schema/purpose and converts the legacy
  day/dusk environment through `migrateLegacyEnvironment`, then re-parses the migrated document as v2.
- `loadCityWorkspaceConfig()` reads the v2 key first; when only the v1 key exists it migrates,
  saves the result as v2, and leaves the v1 entry untouched.
- Runtime panel: the day/dusk mood control is replaced by a 时段 select
  (`environment.timeOfDay`, options 日景/黄昏/夜景). Presets carry `timeOfDay: "day"`.
  Tests cover the v1→v2 round trip, export/import keeping night, load/save migration with a fake
  localStorage, and the panel's 时段 control (`city-workspace-config.test.ts`, `city-runtime-panel.test.ts`).

### Task 3 — map workspace flow for building-only render scenes (`map.ts`)

- `requireWorkspaceReady` now accepts a traffic-pack scene **or** the active calibrated render scene
  (`renderSceneActive` with the building presentation loaded). Without either it throws
  `City workspace preview requires a ready traffic replay or a loaded render scene`.
- `applyWorkspaceConfig` gains the explicit traffic guard: on a render scene (no SUMO replay) a
  non-zero traffic demand returns a `PreviewValidationIssue`
  (`traffic-replay-unavailable`, Chinese message telling the user to set demand to 0 or use a
  SUMO-recording scene) instead of silently doing nothing.
- `environment.timeOfDay` drives `setCityTimeOfDay`; weather settings drive `setCityWeather`; the
  tail renders through `renderStaticFrame` when no traffic preview exists.
- New callback `MapCallbacks.onEnvironmentEdit?(environment: CityDraftEnvironment)`. The render-bar
  weather controls and the 时段 button apply a new `CityDraftEnvironment` through
  `applyWorkspaceEnvironment` and report it to the host page after the workspace re-applies.
- The render-bar note (`data-role="render-weather-note"`) shows the mode:
  `草稿环境 · 已保存到工作区` (mode `workspace`) once a workspace is applied, `未保存的视觉预览`
  (mode `preview`) otherwise; the aria label follows.
- `map.test.ts` adds render-scene readiness acceptance/rejection, the static-frame workspace apply,
  the traffic-demand issue, the `onEnvironmentEdit` round trip, and the render-bar mode labels
  (13 tests total).

### Task 4 — scene-level environment source (`city-scene-config.ts`, `default-scene-v1.json`)

- Optional `environment_source: CitySceneJsonAssetRef` on building-render scenes (strict parse:
  `/city-presentation/…​.json` path, hex64 sha256, positive size). Pack scenes reject the field with
  `City environment source requires building_render`.
- `public/city-presentation/default-scene-v1.json` binds
  `"environment_source": {"url": "/city-presentation/shanghai-source-environment-v1.json", "sha256":
  "c1277200aebf59c728cd4c5eb8cb91421cf3b65b2510dfadde8c592494f6f590", "size_bytes": 172205}`.
  Verified twice during the task: the file exists, is 172205 bytes, and its sha256 matches.
- The vegetation layer is untouched (separate worker). `city-scene-config.test.ts` (15 tests) covers
  the binding and tampered/misplaced rejections.

### Task 5 — studio for building-only render scenes (`city-studio.ts`)

- `loadSpatialSource` keeps the mesh-pack branch; a building-only render scene now fetches its
  digest-pinned render manifest (`fetchBuildingRenderManifest`) and builds `SpatialMapData` from it:
  origin from `scene.origin_wgs84`, extent from block envelopes, buildings from
  `buildingPlacement` entries (normalized), roads empty. The old pack-style "no assets" error message
  still applies to a pack scene without workspace assets.
- The spatial tab appends an explicit note when roads are empty
  (`data-role="spatial-road-coverage"`): 道路几何待原生几何门验收 — no road polygons exist on a
  building render scene, so facility checks use building envelopes and airspace only.
- `startMap` registers `onEnvironmentEdit`: a render-bar environment edit updates `config.environment`
  (guarded by map epoch), re-validates the draft, saves through the existing
  `saveCityWorkspaceConfig` path, and refreshes the runtime/spatial tab and summary. Panel edits
  (runtime tab) keep the pre-existing explicit-save flow; the render-bar path is the one that
  auto-persists. `city-studio.test.ts` (5 tests) and `city-workspace-integration.test.ts` still pass
  unchanged.

### Task 6 — browser acceptance (`scripts/capture-editor-environment.mjs`, new)

- Playwright Chromium against a running dev server (default `http://127.0.0.1:5310`), evidence into
  `validation/frontend-opus-20260930/editor/`. It publishes the map instance by routing
  `/src/city-studio.ts` (the studio never exposes it), loads `city-studio.html` on the default scene,
  and asserts:
  1. fresh profile → no stored draft; render scene ready; workspace draft applied;
  2. render-bar 时段 cycle day→twilight→night with auto-persisted v2 drafts, note in
     `草稿环境 · 已保存到工作区` mode;
  3. runtime-panel rain (雨, 6 mm/h) + explicit save → v2 draft with `timeOfDay: "night"`,
     `precipitation: "rain"`;
  4. reload → draft restored, `data-city-time-of-day="night"`, scene weather rain, panel controls
     show night+rain;
  5. export → v2 JSON download; change to day; re-import → night rain restored;
  6. screenshots `before-reload.png` / `after-reload.png` from the identical camera pose
     (verified equal), `report.json` with per-check results, console errors, the WebGL renderer
     string and PNG sha256 hashes.
- 17 checks, all passing; zero page/console errors.

## Validation commands and results

Run from `frontend/` (Node 22, current tree):

| Command | Result |
| --- | --- |
| `npx vitest run src/city-draft-environment.test.ts src/city-workspace-config.test.ts src/city-runtime-panel.test.ts src/city-scene-config.test.ts src/map.test.ts src/city-studio.test.ts src/city-render-weather-controls.test.ts src/city-workspace-integration.test.ts` | 8 files, **55/55 tests passed** |
| `npm run -s typecheck` | **0 errors** (whole tree, including the vegetation worker's files after their fixes landed) |
| `npm run -s build` | **success** (exit 0) |
| `node scripts/capture-editor-environment.mjs http://127.0.0.1:5310 ../validation/frontend-opus-20260930/editor` | **PASS: 17 checks, 0 console errors** |

## Evidence

`validation/frontend-opus-20260930/editor/`:

- `report.json` — status PASS, 17 checks, `launchMode: "software"`, renderer string
  `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`.
- `before-reload.png` / `after-reload.png` — same camera pose ([-240, 90, 120] / target [-160, 20, -120]
  verified equal in the report frames), sha256 recorded in the report.
- `exported-workspace.json` — the downloaded v2 workspace used in the import round trip.

## Constraints, disclosures, and open items

- **GPU unavailability (environment, not code).** Earlier the same day (03:44) the GPU-flagged
  Chromium run passed on this host with an RTX 3090 renderer string. During this task
  `nvidia-smi` stopped reaching the driver, and the GPU-flagged browser now dies/hangs at GPU init.
  The script prefers the GPU launch, probes WebGL within timeouts, force-kills a wedged browser, and
  falls back to SwiftShader, recording `launchMode` and the actual renderer string in `report.json`.
  The delivered evidence therefore ran on software WebGL; rerunning the same script once the driver
  is back will regenerate GPU evidence without code changes.
- **Studio page hides the on-canvas render bar (pre-existing design).**
  `city-studio.css` contains `#city-studio .city-preview-controls { display: none !important; }`
  (pre-existing line, untouched). The render-bar controls — including the 时段 button and the weather
  note — exist in the studio DOM and their handlers work, but they are not visible on the studio
  page. The browser script drives the 时段 button with a DOM click on the real control and verifies
  every downstream effect (workspace re-apply, v2 persistence, scene lighting, note mode), but it
  cannot demonstrate visual discoverability of that button inside the studio page. Whether the
  studio should surface the render bar is a design decision left open; the map-level behavior is
  fully validated (unit tests + DOM-driven acceptance).
- **Persistence split (intended behavior).** Render-bar environment edits auto-persist through
  `onEnvironmentEdit`; runtime-panel edits follow the studio's pre-existing explicit-save flow. The
  browser script exercises both paths explicitly.
- **Untouched shared files.** `city-environment.ts`, `city-vegetation-*.ts`, `city-grass-clumps.ts`,
  `city-surface-wetness.ts`, `city-roads.ts`, `city-weather.ts` were never edited by this unit. The
  five conditional files (`city-operations-preview.ts`, `city-presentation.ts`, `city-spatial-panel.ts`,
  `city-algorithm-panel.ts`, `city-event-panel.ts`) typechecked without changes, so none were edited.
- The road-geometry note in the spatial panel reflects the current state of the building-render scene
  (no road polygons yet); it becomes outdated automatically once a scene ships with road assets.
