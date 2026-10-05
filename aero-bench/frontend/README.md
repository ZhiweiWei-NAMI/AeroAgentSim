# AERO-BENCH City Operations and Evidence Console

A read-only TypeScript + Three.js OSM2World city console serves two separate formal paths. The main view is a metric ENU planar city without a globe, online basemap, or remote tiles; building heights, road topology, and vegetation come from offline OSM2World sources.

- **Live runs (default when `?view` is absent)** use the formal Control API (`GET /v1/catalog`,
  `POST /v1/runs`, `GET /v1/runs/{run_id}`,
  `POST /v1/runs/{run_id}/controls/{pause|resume|step|stop}`,
  and authenticated `GET /v1/runs/{run_id}/events` SSE) to select catalog runs and
  start / pause / resume / step / stop. `POST /v1/runs` startup responses carry projected
  `PublicScenario` for buildings, roads, and imagery; all request/response bodies use
  generated Ajv contracts in `src/generated/contract-validators.ts`.
- **Sealed replay (`?view=replay`)** accepts strict `aero-bench.public-trace/v3`
  documents only, from local files or same-origin relative endpoints, and validates them against generated contracts in full.

## Local Development

```text
npm install
npm test
npm run typecheck
npm run build
npm run dev
```

After running `npm run dev`:

- The default `/` loads the central Shanghai OSM extract shipped with the application (historical filename `public/osm2world/shanghai-hongqiao.osm.json`). Its bounds are latitude 31.2228–31.2371 and longitude 121.4636–121.4868, outside Hongqiao. The default page creates no run entities; entities come only from the loaded run's PublicScenario and SceneState.
- `/?scene=1` opens an interactive full-screen city view; the upper-right button returns to the console. After a Public Trace is loaded, buildings and roads are converted from that run's authoritative WGS84 coordinates. The default Shanghai map does not replace the run scene.
- `/asset-library.html` opens the local asset library. UAVs, vehicles, people, and street furniture use a two-level catalog. The right-hand preview supports drag rotation, wheel zoom, view reset, and full-screen preview; Esc exits. CAD ZIP display images are explicitly marked as non-3D. Source packages with converted models link to their 3D previews.

## City motion demo

Run `npm run dev`, then open `/?scene=1`. The default Shanghai scene automatically plays a 120-second engineering demo. Bottom controls pause, seek, select 0.5×/1×/2× speed, open the street view, follow either UAV, a bicycle, or a pedestrian, and show display collision boxes for buildings, vehicles, pedestrians, lamp posts, and UAVs. Models, flown paths, and counts change during playback. The street, bicycle-follow, and pedestrian-follow controls seek to 60 seconds; the X500-follow and camera-quadcopter-follow controls seek to 30 seconds, when both UAVs are flying.

The default preview shows a dense block in Shanghai's Huangpu district by day, with a night view for comparison. The lighting-block control moves the camera near billboards. Ground paving follows road and pedestrian surfaces; unclassified ground receives no invented sidewalks, and mapped grass and trees retain source coordinates. Ground-floor shop windows, warm road lighting, and billboards are display lighting. They do not represent real businesses or run data and do not enter the formal Public Trace.

The default Huangpu demo uses a ground-road variant. Source OSM bridge, tunnel, nonzero layer, and explicitly recorded tunnel-name rules exclude elevated, underground, and suspected tunnel roads; SUMO then rebuilds connections and traffic is recorded again. Source city-road meshes are filtered by the same source IDs, so flattened elevated-road triangles cannot remain on the ground. After elevated roads are removed, locations without ground-coverage data show the base color; no road is drawn from their former projected footprint. Original source packages and demo assets are preserved. The current data has no verifiable bridge-deck elevation, so the ground demo does not reproduce the complete 3D road network.

Road assets use `aero-bench.city-road-preview/v2`. The generation path is `OSM/city mesh package → SUMO lanes and junctions → street_layout road layout → Three.js`: SUMO supplies lane widths, directions, connections, walking areas, and signal references; the layout layer adds display cross-sections and markings. Vehicle surfaces combine external and internal lanes, junction polygons, and asphalt/concrete outlines from the verified city package. Source materials and textures are bound by package paths and hashes. The generator clips building footprints, resolves overlaps on a 1-millimeter precision grid, and divides the result into 60-meter blocks for browser triangulation.

`city_street_surfaces.py` restores SUMO walking areas and adds pedestrian paving and narrow enclosed traffic islands in corridors supported by road-class data. Pedestrian surfaces sit 15 centimeters above vehicle surfaces, with vertical sides derived from the published top-surface boundaries. Pedestrian foot height is sampled from the same top surface. Added areas avoid buildings and vehicle-trajectory corridors and are marked as design-generated, not field-surveyed. Accessible ramps are not generated yet.

`city_street_markings.py` generates white edge lines, same-direction dashed lines, solid lines before controlled junctions, opposing-direction yellow lines, stop lines, and turn arrows supported by SUMO connection data. Dashed lines are explicit segments and are not split again by the frontend. Zebra crossings appear only at OSM locations explicitly tagged zebra or SUMO signal-controlled junctions with at least three connecting motor-vehicle roads. Actual marking rules missing from the source map cannot be uniquely inferred from topology; the current configuration is a design completion. Abnormally narrow source lanes are not widened independently, which would misalign existing trajectories.

Ground-network generation has two steps: `build-ground-city-network.py` preserves provenance and exclusion lists; `complete-ground-city-crossings.py` uses `city_crossing_groups.py` to produce SUMO plain-XML corrections. It adds 2-meter sidewalks only to crossing-road groups that need expansion, separates pedestrian and vehicle permissions, replaces the original short crossing paths, and validates all crossing groups, walking-area connections, and signal phases. Six paths that previously crossed only opposing bus/bicycle lanes are extended to approximately 9.5–16 meters. This is a demo design completion, not a field survey of road markings. The frontend draws zebra stripes by cumulative distance along each complete path; both initial and runtime lamp placement avoid crossing corridors.

Historical inputs and reports are in `../validation/city-ground-roads-20260927/`.
The ground/walkable scene manifests in this example retain their historical pack
pins and are not current acceptance targets. Rebuild and audit their complete
input chain before publication; do not hand-repin them. The current accepted
preview inputs are documented in `../docs/frontend-worker-ground-a1-20260930.en.md`.
For a rebuilt historical candidate, run these commands in order (new output
directories must not already contain a network with the same name):

```text
python scripts/build-ground-city-network.py --network ../validation/scene-compiler-shanghai-huangpu-east-v1/sumo/network.net.xml --source-osm ../validation/scene-compiler-shanghai-huangpu-east-v1/osm/sumo-network.osm --output-dir ../validation/my-ground/sumo-base
python scripts/complete-ground-city-crossings.py --network ../validation/my-ground/sumo-base/network.net.xml --output-dir ../validation/my-ground/sumo
python scripts/build-sumo-city-preview.py --scene public/city-presentation/huangpu-ground-scene-v1.json --network ../validation/my-ground/sumo/network.net.xml
python scripts/refresh-city-road-preview.py --scene public/city-presentation/huangpu-ground-scene-v1.json --network ../validation/my-ground/sumo/network.net.xml --building-objects ../validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json --building-render public/building-renders/shanghai-huangpu-east-v1/manifest.json --report ../validation/my-ground/road-audit.json
python scripts/build-city-building-placements.py --scene public/city-presentation/huangpu-ground-scene-v1.json
python scripts/build-planned-city-flight.py --scene public/city-presentation/huangpu-ground-scene-v1.json
python scripts/audit-city-collisions.py --scene public/city-presentation/huangpu-ground-scene-v1.json --report ../validation/my-ground/collision-audit.json
```

Bind buildings to the new road surfaces after generating those surfaces. Existing building footprints first clip traffic and road geometry; the separate ground-version building manifest is then bound to the new road-surface hash. Formal runs and sealed Public Traces do not use these display-layer design data.

3D window glass and frames are separate from wall atlases. Close views use environment reflections and local reflection probes. Probes update after an explicit camera change and reuse render targets rather than capturing the whole city every frame. Fully imported FBX buildings retain their original materials. These features belong to the engineering display layer; formal Public Traces retain their own authoritative data.

The default scene uses `public/city-presentation/default-scene-v1.json` to specify the city mesh package and its SHA-256, road materials, building placements, SUMO traffic, road surfaces, and flight-demo URLs. The previous Shanghai scene remains in `public/city-presentation/shanghai-day-scene-v1.json` and opens separately with `?city=/city-presentation/shanghai-day-scene-v1.json`. To use another prepared city, create a manifest with the same structure under `public/city-presentation/` and point these entries to that city's assets. Once the city package is ready, generate building placements with `python scripts/build-city-building-placements.py --scene public/city-presentation/my-scene-v1.json`. After aligned SUMO traffic is ready, run the commands below (`engineering-inputs.json` and the source OSM hash must be in the `network.net.xml` directory; pass `--source-osm` if the source OSM has moved):

```text
python scripts/refresh-city-road-preview.py --scene public/city-presentation/my-scene-v1.json --network ../path/to/network.net.xml --building-objects ../validation/my-scene/metadata/objects.json --building-render public/building-renders/my-scene/manifest.json --report validation/my-city-road-audit.json
npm run dev
```

Open `/?scene=1&city=/city-presentation/my-scene-v1.json` in the browser. The refresh command writes new road surfaces to a temporary file, then validates the city-package hash, network hash, building placements, traffic-sample coverage, and browser-triangulated area. It replaces the manifest's road asset only after validation passes. `--tile-size-m` adjusts triangulation block size; the algorithm contains no Shanghai coordinates or road shapes. Run `npx vitest run src/city-roads.test.ts src/city-scene-config.test.ts src/osm2world/pack.test.ts` to check triangle orientation, block area, and city-package checksums in the manifest. Use `node scripts/verify-city-lighting-demo.mjs http://127.0.0.1:5173 validation/city-lighting-demo` to check full-screen street views, road surfaces, day/night switching, and billboards.

The manifest connects already generated data. A new OSM2World city package and aligned SUMO network and traffic must first be generated and pass provenance checks. Building placements, road geometry, and visual flight routes are calculated from the manifest and geometry. SUMO demo demand still fixes vehicle types and counts; replacing the manifest alone cannot generate a complete demo from any map. Formal runs load their own sealed mesh packages and Public Traces, not the demo manifest.

Default-scene ground trajectories and traffic-light states come from an independently run SUMO engineering preview. The original record contains 70 vehicles (including 12 bicycles), 36 pedestrians, and 171 signal locations; vehicle appearances cover cars, taxis, police cars, buses, trucks, and bicycles. A display collision-box audit excludes 7 conflicting trajectories, leaving 63 vehicles in playback. The two UAVs follow closed visual-demo routes planned above the highest rooftop. Their maximum speeds are 7.33 and 5.28 meters/second, with minimum rooftop clearance of 24.65 meters. These are not PX4/Gazebo flight records for this block and are not used for scoring or physical flight validation. The previous scene still provides the original PX4 engineering replay.

Citizens pedestrian models are uniformly scaled to 1.72 meters tall and move along SUMO trajectories. Demo pedestrians select routes only on dedicated SUMO pedestrian lanes. The source GLB walk-animation root axes differ from the static skeleton; the display layer retains limb animation tracks and uses the model's original upright root pose. The Bicycle_Man_34 display bicycle follows SUMO bicycle trajectories with an assembled Citizens rider. Pedaling and wheel rotation follow recorded displacement and stop when displacement stops. Bicycle demand routing requires paving verified against the city package, avoids building collision boxes, and allows at most three lanes per road segment. The road audit checks actual TraCI lane records and surface coverage. Bicycles may use shared motor-vehicle/bicycle lanes or dedicated bicycle lanes; they are not forced into motor-vehicle lanes.

Run `node scripts/verify-city-motion-demo.mjs http://127.0.0.1:5173 validation/city-motion-demo` to check both UAVs, street traffic, pause/seeking, and consecutive frames. `node scripts/verify-city-traffic-rules.mjs http://127.0.0.1:5173 validation/city-traffic-rules` compares street and cycling frames at 60–63 seconds. `python scripts/audit-city-road-preview.py` checks OSM/SUMO junction support for zebra crossings, actual bicycle lanes, and road-surface coverage. `python scripts/audit-city-collisions.py` checks published trajectories' display collision boxes at 961 sample instants. These checks do not replace field verification of road markings or continuous physics-engine collision validation.

The asset library's materials-and-textures category lists BigCity images, OSM2World style textures, and road signs individually. The all-assets category contains all indexed packages, images, and models, loading 80 items at a time in long lists. Paint trials in the 3D preview provide base coatings and 44 automatically discovered local PBR texture sets. Color, roughness, metalness, and texture density can be adjusted for the whole model or a clicked part, and the imported appearance can be restored. Paint trials affect only the current browser preview; they do not write back to source packages, GLBs, or formal run scenes.

The asset library reads 290 ZIPs and 292 source-package images from the [Quark UAV source directory](../validation/downloaded-assets/quark-drone-models/003%20%E6%97%A0%E4%BA%BA%E6%9C%BA/) and `validation/downloaded-assets/baidu-bigcity/BigCity.unitypackage` under `npm run dev` and `npm run preview`. Quark ZIPs and original images use local read-only endpoints and are not copied into `dist`; standalone static hosting of `dist` must provide the same directory endpoints. Per-package conversion status and failure reasons are in `public/models/uav/conversion-inventory.json`, which `python scripts/summarize-uav-conversions.py` can rebuild from verified model manifests and CAD reports. The library does not change formal-run entity models, trajectories, or dimensions.

Of the 290 UAV ZIPs, 209 have browser-viewable models; the remaining 81 need source-format export, meshing, or assembly verification. `conversion-inventory.json` records per-package status. Models include 24 existing display GLBs, STEP/IGES previews from independent CAD imports, and previews generated from complete or common-coordinate STL geometry. General CAD meshing and STL previews use a common aviation PBR coating, which does not establish factory colors or verified part-by-part assembly completeness. The separate Da Vinci H2 display GLB retains 20 face colors from successfully imported source STEP portions; source-geometry completeness remains unverified. The STEP inside Octocopter's embedded RAR also has a preview, with open surfaces and assembly completeness identified in the catalog. A Hellfire body STL is displayed as a neutral-gray component GLB; its ZIP remains among the 81 pending whole-aircraft source packages, and the body is not counted as a UAV airframe. Rebuild this component with `python scripts/export-hellfire-preview.py`. The 24 existing GLBs use a common coating with gray-white bodies, graphite undersides and frames, and amber identification areas; 23 come from STL, while ScanEagle comes from 3DXML with parts and UVs. Holybro X500's coating preview is a separate file; formal runs still use the original X500 GLB. Run `python scripts/build-uav-previews.py` to rebuild these 24 previews, then `node scripts/capture-uav-thumbnails.mjs <local Vite URL>` to update thumbnails. `python scripts/convert-quark-neutral-cad.py batch` processes neutral CAD batches. Native SolidWorks, Inventor, Creo, and similar assemblies require suitable import tools to verify references and export neutral formats; assembly positions cannot be invented from part names.

New assets come from the [incoming source directory](../validation/downloaded-assets/%E6%9E%81%E9%80%9F%E4%B8%8B%E8%BD%BD%E5%A5%BD%E7%9A%84%E6%96%87%E4%BB%B6/): 38 Urban Traffic vehicle FBXs, 121 Citizens character FBXs, textures and source project files from two Unity packages, and 100 C2239 street-furniture GLBs converted from OBJ/MTL. Vehicle categories distinguish passenger cars, buses/freight, emergency vehicles, and two-wheel micromobility. C2239 Blend, FBX, C4D, and Max ZIPs contain the same furniture in other formats; original packages remain intact. `python scripts/build-incoming-unity-library.py` fully rebuilds the index and external-texture mappings from Unity packages. With a complete index already present, use `python scripts/build-incoming-unity-library.py --refresh-reviewed` to synchronize catalog groups and reviewed model configurations. For street furniture, run `python scripts/build-c2239-library.py stage`, start Vite, run `node scripts/convert-c2239-browser.mjs <Vite URL>`, then `python scripts/build-c2239-library.py finalize`. After confirming outputs, `clean-stage` can remove intermediate OBJ/MTL files. Original vehicle FBX previews show only LOD0 and unlevelled parts; source FBXs do not automatically inherit all Unity Prefab materials and animation controllers. Verified per-model adjustments are recorded in `scripts/incoming-reviewed-models.json`. The standalone `Bicycle.FBX` matches two source MATs by the same main-texture path and adds normals, metalness, and alpha clipping for body and transparent parts; no generic Bicycle Prefab is available to verify complete material binding. Car_6's display GLB is rebuilt with source Prefab materials and eight source-package textures; with Vite running, re-export it using `node scripts/export-car6-prefab-glb.cjs`. Taxi_1 and Police_1 display GLBs use their respective Day Prefab LOD0 materials, source color textures, and normal maps. The separate Bicycle_Man_34 display GLB contains only the fully textured bicycle LOD0; the source rider skin fragments in the browser and an independent importer, so the city demo assembles a Citizens skeletal rider. The original FBX is preserved for verification and re-export from Unity. Re-export the bicycle display model with `node scripts/export-bicycle-man34-bike-only.mjs`.

`python scripts/build-bigcity-library.py` exports 158 FBXs, thumbnails, and browser images from the BigCity source package and generates `public/models/bigcity/manifest.json`. The page previews FBXs with source-package textures. The two `.unity` scenes show geometry through `Scene.FBX`; Unity lighting and post-processing are not ported to the page. The catalog shows embedded thumbnails for 69 EXR lightmaps; original EXRs remain in the source package.

- In live mode, enter the control-service origin in the left run-control panel (for example
  `http://127.0.0.1:8123`), the bootstrap operator token, and the bootstrap CSRF token
  (both 64 hexadecimal characters). Load the run catalog, select a run, and start it.
  Credentials remain in memory only. Inputs clear after each use and are never written to storage, logs, or page text.
- Replay mode (`?view=replay`) scans this workspace for `public-trace.json`; select
  a trace in the top-bar dropdown to load it. Only `public-trace/v3` is accepted; older versions
  are listed but rejected. Browser file upload is not required. Declared assets are resolved
  by SHA-256 from `assets/` / `artifacts/` in the trace directory.

## Asset ledger

`asset-library-ledger.json` records source references, original licence status,
current use, byte count and SHA-256 for every public model and published derived
building GLB. It stays outside `public/`; source archive paths are not browser
asset URLs. The independent audit rejects unlisted GLBs and changed bytes.
Run from the repository root:

```text
node frontend/scripts/build-city-asset-ledger.mjs
node frontend/scripts/audit-city-asset-ledger.mjs --report=validation/my-asset-ledger-audit.json
node --test frontend/scripts/city-asset-ledger.test.mjs
```

The supplied models are used under the user's declaration of mentor-authorized
internal research. Original licences remain unknown where source metadata does
not state them; the ledger makes no CC0 or public redistribution assertion.

## Trust boundaries (implemented behavior)

- The interface never invents positions, trajectories, buildings, vehicle/person ground truth, task success, verifier conclusions,
  observations, command results, messages, or terminal output. Missing data produces an explicit undeclared /
  unavailable state without completion.
- The OSM2World source (`src/osm2world/source.ts`) accepts only standard OSM JSON 0.6 and `aero-bench.osm2world-source/v2`. Offline OSM nodes/ways/relations must be explicitly declared; missing nodes cause loading to fail.
- Public replay drives the visual playback clock with simulation-time differences. Original sample instants are hit exactly, and missing samples retain discontinuity semantics. The telemetry panel preserves authoritative coordinates; display positions for entities and geometry use the same OSM2World MetricMapProjection.
- OSM JSON types, values, duplicate IDs, way nodes, and relation-member closure are validated before loading. Bund and Yanzhong Park geometry relations are completed. Route and administrative-catalog relations are outside local city geometry; provenance records their handling. Current browser acceptance checks report no OSM conversion errors.
- The `osm_scene` layer explicitly references a WGS84 JSON asset. Replay loads it from a content-addressed path; live mode uses authenticated `GET /v1/runs/{run_id}/assets/{sha256}`. Both validate length and SHA-256 first. A failed declared asset does not trigger a replacement city.
- `tools/osm2world/web-integration.patch` pins official object-metadata export, JTS connector and interior-point fixes, and optional metric window textures. Windows are display styling, not measured floors. Original OSM height, outline, and use tags remain unchanged.
- Geometry and run overlays use OSM2World MetricMapProjection. Framing uses configured bounds, not the full railway-node bounds extending outside the area. Public telemetry coordinates are unchanged. The ground plane and sky are display layers, not simulation-terrain or weather evidence.
- All run changes use the formal Control API; there is no direct Provider/PX4 connection or arbitrary shell.
- Agent interactions show only server-authorized projections and explicit `agent.decision_summary.v1`
  payloads. Hidden model reasoning is neither requested nor displayed. Public payloads containing
  `process.stdout/stderr` interactions or backend-prohibited property names (such as
  `hidden_reasoning` or `token`) are rejected from rendering.
- The read-only terminal dock (agent, agent I/O, PX4/PXH, MAVLink, ns-3, SUMO,
  verifier, events) renders only received, authorized public records. Channels without records show
  a message that the control stream supplies no data for that channel; the dock accepts no input.

## Module index

- `run-control.ts`: Control client, bounded SSE frame parsing, cursor resume, and authentication flow.
- `run-store.ts`: Live-session state (exact SceneState tick ingestion and bounded buffers).
- `asset-resolver.ts`: Declared replay-asset resolution (SHA-256 and byte-count checks).
- `replay-loader.ts`: Sealed city-replay manifest/index/shard validation and tick-based loading; RGB and browser interpolation are not backend evidence.
- `event-channels.ts` / `terminal-dock.ts`: Public event-channel routing and read-only terminals.
- `agent-console.ts` / `interaction-timeline.ts`: Authorized interactions and causal chains.
- `telemetry-panel.ts`: Exact-tick telemetry / tasks / geofences / network / evidence.
- `telemetry-hud.ts`: Multi-UAV telemetry HUD cards driven by exact UAV StateSamples, with view switching, card selection, dragging, and minimizing.
- `osm2world/source.ts`: OSM2World source validation and PublicScenario-to-OSM-feature projection.
- `map.ts`: OSM2World Three.js renderer consuming triangle meshes from `O2WConverter`, official materials, and overlay entities.
- `trace.ts`: public-trace/v3 validation boundary (generated Ajv contracts).

Scene rendering uses the official OSM2World Web path: `loadO2WConfig(standard.properties)` + `O2WConverter.convertJson`. The browser reads only pinned offline OSM JSON and style textures; it requests no online basemaps, tiles, or OSM services. Buildings and roads in run scenes are projected from the verified PublicScenario into the same OSM JSON format before conversion.

The default map source is `public/osm2world/shanghai-hongqiao.osm.json`, including real surrounding-building context explicitly recorded in provenance; configured framing bounds remain unchanged. Run scenes use the PublicScenario-declared, SHA-256-verified `osm_scene` asset, not the default map or surrounding context. The old CityPlan, custom ENU scenes, GLB generator, hand-drawn city-basemap generator, and Blender frontend export path have been removed. The release builder now emits WGS84 OSM building JSON while retaining Gazebo collision models and simulation height grids. Existing sealed run directories have not been rewritten; old artifacts do not establish completion of a formal run for the new source-stage.

## Reproducible checks

```text
node scripts/verify-osm2world.mjs http://127.0.0.1:4176 ../validation/osm2world-current
```

Browser reports separately record page errors, HTTP failures, conversion diagnostics, texture completion, camera interactions, and mobile overflow. Any conversion error fails the check. Latest production evidence is in `../validation/osm2world-production/`; scene-asset-loading integration evidence is in `../validation/osm2world-scene-asset/`. A successful screenshot does not establish reference-image similarity; the 95% visual threshold remains unproven.
