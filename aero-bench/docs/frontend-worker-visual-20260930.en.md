# Frontend worker report — visual vegetation pipeline (2026-09-30)

Unit: `visual-vegetation` · branch `repair/inspection-v1-r5` · all work in `frontend/`.

## What changed

### Task 1 — road geometry status on the city environment contract

`frontend/src/city-environment.ts` (edited) + `frontend/src/city-environment.test.ts` (edited):

- Added `export type CityEnvironmentRoadGeometry = "published" | "pending-native-gate"` and a required
  `roadGeometry` field on `CityEnvironmentInput` and `CityEnvironmentPlan`.
- `validate()` now rejects any other status, and when the status is `"pending-native-gate"` it requires
  `roadbed`, `walkbed`, `crossings`, `junctions` to be empty arrays and `streetTrees` to be
  `{kind:"disabled"}` (explicit errors naming the field).
- The old unconditional "requires loaded roadbed, walkbed" check now applies only to `"published"`.
- `generateCityEnvironment` pushes `"road-clearance-pending-native-gate"` into `plan.missing` for the
  pending gate, and the plan carries `roadGeometry`.
- The renderer sets `group.userData.environmentRoadGeometry` alongside `environmentPlan`.

### Task 2 — vegetation wind (`frontend/src/city-vegetation-wind.ts`, new)

- `VegetationWindUniforms {uTime, uWindDir, uWindStrength}`, `createVegetationWindUniforms()`,
  `setVegetationWind(u, windMps, windDirectionDeg, timeS)`, `applyVegetationWind(material, u, profile)`.
- Direction convention matches `CityWeatherSettings.windDirectionDeg` (direction of travel, clockwise
  from north; city frame +X east, −Z north): `uWindDir = (sin d, −cos d)`.
- Strength is monotone and saturating: `windStrengthFor(mps) = 1 − exp(−mps / 6)` with exported
  `VEGETATION_WIND_SATURATION_MPS = 6`. At 6 m/s (the rain preset wind) strength is 1−e⁻¹ ≈ 0.63; it
  never reaches 1, so extreme input values cannot lock the sway phase.
- `applyVegetationWind` patches `begin_vertex` via `onBeforeCompile`: world-XZ displacement along
  `uWindDir`, scaled by clamped object-space height squared, with a gust factor from `uTime` and the
  instance world position (from `instanceMatrix` under `USE_INSTANCING`). Cache key is per profile
  (`city-vegetation-wind-v1:tree` / `:grass`). Idempotent per profile; conflicting profile or an
  already-patched material throws.

### Task 3 — authored grass clumps (`frontend/src/city-grass-clumps.ts`, new)

- `planGrassClumps(plan, {seed, designId, densityPerM2, radiusM, maxCount})` samples patch areas
  area-weighted (barycentric, deterministic FNV-1a hash stream) inside `plan.grass` triangles and
  rejects: disks crossing the patch outer boundary (boundary edges = edges used once) and disks
  overlapping a planned trunk (0.35 m × tree scale, spatial hash). `rejected.cap` is the residual:
  `requested − accepted − boundary − tree`.
- `createGrassClumps(placements, wind)` builds one `THREE.InstancedMesh` of 12 tapered, vertex-coloured
  blades per clump (`GRASS_CLUMP_BLADE_COUNT = 12`, no textures), `MeshStandardMaterial` with
  `applyVegetationWind(..., "grass")`, mesh name exactly
  `"Authored grass clumps (presentation design)"`, `receiveShadow = true`, and a `dispose()`.
- Exported `GRASS_CLUMP_TRUNK_RADIUS_M = 0.35` so callers and tests share the trunk-clearance rule.

### Task 4 — surface wetness (`frontend/src/city-surface-wetness.ts`, new)

- `surfaceWetnessFromWeather(settings)`: `none`, `snow`, `hail` → 0 (snow and hail do not wet surfaces
  in this presentation model — grains bounce instead of forming a film); `drizzle` counts at half the
  film exposure of `rain` at the same rate; wetness `= 1 − exp(−rate·factor / 6)` — monotone,
  saturating, 0 at 0 mm/h. Input validation is delegated to `checkCityWeatherSettings`.
- `applySurfaceWetness(material, u, {maxDarkening?, minRoughness?})` patches standard/physical
  materials: up-facing world normals darken diffuse by up to `maxDarkening` (default 0.45) and lower
  roughness toward `minRoughness` (default 0.08) proportional to `uWetness`; wall faces stay matte.
  Cache key `city-surface-wetness-v1`; second application throws (no stacking).

### Task 5 — verified-source vegetation facade (`frontend/src/city-vegetation-layer.ts`, new)

`loadCityVegetationLayer(input)`:

1. Verifies the source bytes: declared size must match exactly, SHA-256 via `crypto.subtle` must match
   `input.source.sha256`, `assertNotAborted` before and after the fetch; `fetchBytes` is injectable
   (default: `fetch` + `readBoundedResponse` from `src/verified-bytes.ts`).
2. `parseCityEnvironmentSource` with the declared authority (osm/objects SHA-256 + origin) — mismatch
   rejects the load.
3. Builds the plan with `roadGeometry: "pending-native-gate"`, empty road arrays, buildings/greens/
   sourceTrees from the verified source, `CITY_ENVIRONMENT_DEFAULTS`.
4. `loadAssets` is injectable (tests inject fake assets; production defaults to
   `loadCityEnvironmentAssets`, which loads the real FBX tree set).
5. Renders `createCityEnvironment`, applies the `"tree"` wind profile to every tree part material,
   plans and builds authored clumps with `designId "aero-bench.authored-grass-clumps/v1"`,
   seed `20260930`, density 0.4/m², radius 0.3 m, cap 24 000 (`CITY_VEGETATION_CLUMP_DEFAULTS`).
6. Exposes `CityVegetationLayer {group, summary, setWeather, update, dispose}`; `setWeather` validates
   via `checkCityWeatherSettings` and drives `setVegetationWind`; `update(camera, visualTimeS)` keeps
   `uTime` advancing and calls `updateLod`. `group.userData` carries `vegetationSummary`,
   `environmentMissing`, `environmentRoadGeometry`, `environmentPlan`. On any failure after asset
   loading, clumps, renderer and assets are disposed before rethrowing.

## Measured numbers (real source, executed)

From `public/city-presentation/shanghai-source-environment-v1.json`
(sha256 `c1277200…f590`, 172 205 bytes, 414 buildings, 8 OSM greens, 0 source trees), plan built with
`roadGeometry: "pending-native-gate"` and an extent measured from the loaded footprints/greens
(−500..500 m both axes; the source carries no extent field, so the integrator supplies it —
`measuredExtent` in the env-gated test shows the method):

- 7 of 8 greens survive planning; `grassAreaM2 = 47 024.86`, `grassRemovedAreaM2 = 662.77`
  (one green overlaps buildings: 8 696.58 → 8 033.81 m²).
- 157 derived trees; `plan.missing = ["road-clearance-pending-native-gate", "source-has-no-tagged-tree-nodes"]`.
- Clump plan: 18 236 placements, 490 boundary rejections, 85 tree rejections, 0 cap rejections
  (designId `aero-bench.authored-grass-clumps/v1`, seed 20260930, 0.4/m², radius 0.3 m).
- Full evidence: `validation/frontend-opus-20260930/visual/plan-summary.json` (counts, rejections,
  missing, per-green areas).

## Validation (all executed from `frontend/`)

| Command | Result |
| --- | --- |
| `npx vitest run src/city-environment.test.ts src/city-vegetation-wind.test.ts src/city-grass-clumps.test.ts src/city-surface-wetness.test.ts src/city-vegetation-layer.test.ts` | **73 passed / 0 failed** (5 files) |
| `npm run -s typecheck` | **clean** (exit 0) |
| `npx vitest run` (full suite regression check) | 732 passed / 41 failed — identical to the pre-existing baseline (41 failures in 12 files, generated-contract drift); none of the failures are in files touched here |
| `AERO_VEGETATION_PLAN_SUMMARY=1 npx vitest run src/city-vegetation-layer.test.ts` | wrote `validation/frontend-opus-20260930/visual/plan-summary.json` from the verified real source |

Logs: `validation/frontend-opus-20260930/visual/vitest-visual.log`, `typecheck-visual.log`,
`vitest-full.log`, `plan-summary.json`.

Note on tooling: no `tsx`/`vite-node`/`esbuild` binary is installed in this workspace, so the
plan-summary was produced by an env-gated vitest test (`AERO_VEGETATION_PLAN_SUMMARY=1`) inside
`city-vegetation-layer.test.ts` rather than a standalone `scripts/*.mjs` — it re-verifies the source
SHA-256 with node:crypto before parsing, so the report is still digest-checked.

## Files

- Modified: `frontend/src/city-environment.ts`, `frontend/src/city-environment.test.ts`
- Created: `frontend/src/city-vegetation-wind.ts` + `.test.ts`,
  `frontend/src/city-grass-clumps.ts` + `.test.ts`,
  `frontend/src/city-surface-wetness.ts` + `.test.ts`,
  `frontend/src/city-vegetation-layer.ts` + `.test.ts`
- Evidence: `validation/frontend-opus-20260930/visual/*`

## Integration pointer for the map.ts worker

```ts
import { loadCityVegetationLayer } from "./city-vegetation-layer";
import { applySurfaceWetness, createSurfaceWetnessUniforms, surfaceWetnessFromWeather } from "./city-surface-wetness";

const layer = await loadCityVegetationLayer({
  source: { url: "/city-presentation/shanghai-source-environment-v1.json",
    sha256: "c1277200aebf59c728cd4c5eb8cb91421cf3b65b2510dfadde8c592494f6f590", sizeBytes: 172205 },
  authority: { osmSha256: "6948a5a2611145a4c3f4283d756d580fbd8155b049379de91d80bccd8bc2465b",
    objectsSha256: "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc",
    origin: { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50.0 } },
  extent, // the extent actually covered by the loaded city geometry
});
scene.add(layer.group);
layer.setWeather(weather);          // any CityWeatherSettings
layer.update(camera, visualTimeS);  // per frame
// optional road/wetness material: applySurfaceWetness(groundMaterial, createSurfaceWetnessUniforms())
```

`layer.group.userData.environmentRoadGeometry` is `"pending-native-gate"` for the current default
scene — no roadbed/walkbed geometry exists, and road clearance is carried as a missing marker, not
fabricated.

## Open items / blockers

- **Native road gate (pre-existing, not owned here):** the default scene has no published road/walk
  geometry, so the plan correctly reports `"road-clearance-pending-native-gate"` and the renderer
  receives empty road arrays. When the native road builder lands, callers pass
  `roadGeometry: "published"` with real roadbed/walkbed and the planner automatically widens road-side
  placement.
- **Real FBX asset path is not exercised by tests:** `loadAssets` is injectable, so unit tests do not
  load the tree atlas/FBX files (per task constraints). The production default path
  (`loadCityEnvironmentAssets`) is unchanged existing code and is covered by its own existing checks.
- Full-suite baseline of 41 failures in 12 files (generated contract drift) predates this work and was
  not touched.
