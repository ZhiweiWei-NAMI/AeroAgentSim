# City Visual Pipeline and Reference Images

## Conclusions Supported by the Reference Images

The supplied images show repeated window grids, rooftop equipment, sunlight shadows, distant haze, and buildings of different heights. They do not establish which post-processing, reflection, or LOD implementation the creator used. Statements about Blender, Three.js, and production duration come from quotations supplied by the user; the social page was not independently accessed in this iteration.

The browser city can use the following pipeline:

| Stage | Technology and project implementation |
| --- | --- |
| Spatial data | Project OSM into metric east/up/south coordinates, retaining building orientations and heights; the SUMO network and existing vehicle/pedestrian trajectories share those coordinates. |
| Asset creation | Create modular walls, entrances, roofs, and characters in Blender and export GLB; existing FBX/GLB assets remain reusable. Facades in this iteration sample the supplied BigCity atlas. |
| Building generation | Buildings that pass road, sidewalk, and neighboring-building clearance checks use complete source triangle meshes and the BigCity atlas. Others retain clipped parts: low and narrow sections use approximately 3 m window grids and 3.5 m floors, with supplied models for other towers. Stone facades add sills and floor lines; glass curtain walls do not add stone sills. Building IDs determine rectangular rooftop equipment layouts. |
| Materials and reflections | Three.js MeshStandardMaterial, color/normal/roughness/environment maps; local block cubemap reflections are captured on configuration changes rather than every frame. |
| Lighting and atmosphere | Directional light/shadows, ACES tone mapping, Three.js Sky atmospheric sky, and display fog; sky output and building exposure are calibrated separately, retaining the existing HDRI for environment lighting. Configurable weather and sky centered on the actual rendering camera support UAV views and local reflection probes. These parameters do not establish Weather Provider measurements. |
| Animation and cameras | Existing GLB skeletal animation and SUMO background records; authoring UAVs sample takeoff/landing or keyframes at given instants and follow the selected airframe. Authoring trajectories are not PX4 physical results. |
| Performance | Rooftop parts share three instance batches; two authoring UAVs use original models and LOD1, retaining detailed models nearby or when followed. CPU submission duration and actual frame intervals still require measurement. |

[Three.js material documentation](https://threejs.org/docs/pages/MeshStandardMaterial.html) describes PBR metalness, roughness, normals, and environment maps. Wider shadows could use [CSM](https://threejs.org/docs/pages/CSM.html); this iteration retains the existing single local shadow map without a cascaded-shadow pass.

## Current Boundaries

The source map is a 1 km × 1 km OSM-derived crop centered on Shanghai Huangpu coordinates. Internally generated building IDs retain original OSM ID tags; negative IDs must not be treated as original OSM way IDs. Of 411 source buildings, 302 lack original heights; the compiler marks them `assumed_missing_osm_height` and estimates 12 m. Uniform heights limit skyline realism. Actual heights require traceable building data and cannot be recovered by replacing materials.

The union of source building footprints is 218,145 m², versus 175,636 m² for the original rectangular replacements, a difference of approximately 19.5%. The source also includes parks, grass, and construction sites; empty ground cannot all be attributed to omitted rendering. Sixty-four source buildings intersect paved ground meshes, which include roads and other paving; this alone is not evidence that SUMO vehicles pass through buildings. See `validation/city-reference-20260927/iteration2-audit.json` for measurements and sources.

- The Huangpu scene restores complete source meshes for 138 buildings, raising footprint union coverage from 80.51% to 87.24%; another Shanghai scene restores 90 buildings with 83.19% coverage. Buildings that fail clearance retain clipped parts. Both scenes preserve original part arrays, occlusion inventories, SUMO records, and flight records; the same generator can rerun for another map.
- Complete buildings use oriented bounding boxes that may still close courtyards. Their union in Huangpu exceeds corresponding actual footprints by about 12,168 m²; the 2D placement view shows the same collision extent. This conservative check cannot replace exact polygons or physical contact detection.
- Facades use the existing atlas; textures and normals still represent most windows. Close views lack complete entrances, rooftop access, and interior depth. Source polygon roofs retain original geometry; new equipment currently serves rectangular roofs. The skyline and plot density still differ from the reference.
- Placement and flight-segment preflight cover buildings, facilities, no-fly time windows, airframe relationships, and conservative static boxes for lamps, signals, and trees. Moving-traffic contact response, actual energy consumption, and charging depend on backend Providers.
- All scale calibration distinguishes display estimates from measurements. For example, the X500's 0.65 m longest horizontal edge is source-model display calibration, not a manufacturer measurement of the complete vehicle envelope.

## Asset Purchases

Existing buildings, vehicles, pedestrians, UAVs, and shelters remain reusable. Basic rooftop equipment can be generated procedurally; this prototype needs no new whole-city asset purchase. If purchasing, prioritize dedicated automated UAV charging hangars and logistics parcel lockers. Close-view building parts should provide GLB, metric dimensions, PBR textures, independent pivots, LODs, and explicit licensing.

## Measured Evidence

Before/after screenshots at fixed cameras and the same SUMO simulation instant, plus GPU information, are stored in `validation/city-reference-20260927/`. `frontend/scripts/capture-city-reference.mjs` reproduces captures, `verify-city-workspace.mjs` checks configuration interactions, and `benchmark-city-playback.mjs` measures playback submission duration. Neither one screenshot nor draw-call counts establishes fixed FPS.

This iteration's independent geometry audit is `iteration2-final-audit/report.json`. Source-building eligibility binds roadbed/walkbed binary digests; the Python generator and browser use the same little-endian Float64 encoding, and the browser recomputes digests of actual road data. Current road-configuration digests and historical traffic-record digests are stored separately; new configuration digests cannot overwrite recorded SUMO provenance.

### Acceptance for the 2026-09-27 Iteration

Build and type checks passed, as did 118 tests across 25 city-module files, three Python surface-digest tests, and five building-generator tests. After two cross-review rounds, an independent read-only review rechecked all 138 / 90 complete source buildings. A loading error that misclassified low-slope roofs as missing was also fixed; roof checks use source material identity and upward projected area without requiring flat roofs.

`iteration2-final/` stores four fixed views each for daytime and evening. At 1600 × 1000, the daytime overview has 2,884 draws and 2,548,526 triangles; capture loading took about 8.80 s without browser errors. Manual inspection still shows repeated windows, plain roofs, and map boundaries, so these captures do not establish reference-image or UE quality.

`iteration2-performance-final/report.json` uses the server RTX 3090, Chromium Vulkan, and 1280 × 800. It visits 240 samples over a 120 s recording, then measures 120 consecutive frames. Results follow; this is one before/after comparison in the same environment, not a prediction for user devices or remote networks.

| Measurement | Before | This iteration |
| --- | ---: | ---: |
| Loading duration | 9.06 s | 8.92 s |
| Median consecutive-frame CPU / WebGL submission duration | 13.9 ms | 12.9 ms |
| 95th percentile consecutive-frame submission duration | 20.6 ms | 16.5 ms |
| Median RAF frame interval | 16.7 ms | 16.7 ms |
| 95th percentile RAF frame interval | 33.4 ms | 33.4 ms |

Submission duration excludes GPU completion of all draws; RAF includes scheduling intervals. No new shader-compilation frames or browser errors occurred. All 12 interface checks in `iteration2-workspace-final/report.json` passed, covering 2D selection/3D facility synchronization, overlap rejection, no-fly-zone import, takeoff/landing previews for two airframes, weather, algorithm drafts, event rules, and configuration import/export. They do not establish backend dispatch, physical flight, or energy Provider execution.

### Lighting Corrections

The previous runtime inventory found 24 AmbientLights, of which 23 were created by FBXLoader from asset GlobalSettings (intensity 1, color `#646464`). Cloning accumulated these lights across the city, making individual sun/fill adjustments weak. City FBX loading now removes asset AmbientLights before cloning, retaining unified scene ambient light, streetlamps, and vehicle lights. Capture scripts require exactly one AmbientLight in the actual loaded scene; module tests verify that cloning 30 models neither adds global ambient lights nor removes models/SpotLights.

`city-lighting.ts` centralizes day/night key light, fill, exposure, and building reflection intensity. Three.js ignores scene.environmentIntensity when a building specifies envMap explicitly, so material intensity must be synchronized; local probes copy those parameters on refresh. The ground now uses high-roughness MeshStandardMaterial, fixing the previous Basic material's failure to receive shadows despite receiveShadow.

After asynchronous sky images replace placeholders, the old equirectangular cube / PMREM caches are explicitly evicted before upload; CanvasTexture needsUpdate alone does not rebuild cached Three r185 environment conversions. The sun retains its existing single 2048² shadow map, with no added lights or post-processing passes. The new ground adds PBR/shadow-sampling fragment cost. Lighting captures and diagnostics are in `validation/city-lighting-20260927/`.

Server Chromium Vulkan diagnostics also found a transparent night-sky canvas, with center pixel `[0,0,0,0]`. Requesting `willReadFrequently` during canvas creation while retaining environment-cache invalidation produced `[19,29,54,255]` in all final eight views, restoring dark-blue skies. The underlying driver cause remains unconfirmed; capture scripts now check canvas alpha rather than accepting loading markers alone.

Final captures are in `accepted/`, with one runtime ambient light in every view. All 21 tests across six related modules, build, and type checks passed. On the same RTX 3090 at 1280 × 800, `performance-accepted/report.json` measured 8.97 s loading, 13.3 ms median and 16.5 ms 95th-percentile consecutive-frame submission, and 16.7 ms median and 33.4 ms 95th-percentile RAF intervals, without browser errors or new shader compilation during playback. Frame intervals match the previous measurement; this single run makes no fixed-frame-rate claim.

All 12 workspace configuration regressions also passed; see `workspace/report.json`. Capture acceptance waits for both sceneReady and skyReady, then checks night-sky canvas and light counts to avoid accepting placeholder skies.
