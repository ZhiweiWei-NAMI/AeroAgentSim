# From a blank project to a region-backed run

[中文](blank-project-tutorial.zh-CN.md) · [Guide index](README.md) · [Install and acceptance checks](../../INSTALL.md)

This walkthrough uses the manual workbench: **Gallery → Configure → Space → Objects → Relations → Tasks → Components → Algorithms → Validate → Save version → Initialize → Start → causality/replay**. No LLM connection is required. Work on a small configured OSM extract first; use a short run so you can inspect the first event chain.

The fixed button names below describe the region/object authoring interface for this delivery. If an older frontend lacks one, or an API returns 404/422, keep the draft, read the error and check the frontend/server versions and configured capabilities. A missing action or preview field is not a successful authoring step. This guide does not certify an untested deployment.

## 1. Start blank and distinguish the pieces

In **Gallery**, choose **Skip, create manually** (start blank), then **Configure**. The new draft has no authored fleet or workload. You may return to any section; saving a draft is different from freezing a version or starting execution.

| Term | What you are configuring |
| --- | --- |
| Type contract | Allowed fields, units, relations and capabilities, supplied by the project's AeroGraph snapshot/catalog. An abstract type cannot be placed. [Contracts](../concepts/aerograph.md) |
| Instance | One object with a stable id, such as `courier-1`; selecting a type is not creating this object. |
| Visual model | A GLB mesh or procedural marker showing appearance, size and axes. It computes no battery, flight or delivery state. [Assets](../../ASSETS.md) |
| Physics/execution model | An engine that owns fields and executes commands: motion, energy, weather, traffic or tasks. A generic kinematic engine is not a complete delivery model. [Plugins](../concepts/plugins.md) |
| Algorithm | A method choosing a plan, such as allocation or routing. Its slot must be read by an actual engine. [Algorithms](algorithms.md) |
| LLM | An optional decision implementation proposing permitted actions. Manual setup, deterministic rules and replay do not require a model. [Agents](agents.md) |

Templates are examples: they prefill snapshots, Huangpu coordinates, objects or fleet generators, business engines, algorithms and rules. Those assumptions need review when reused. An arbitrary OSM region supplies source geometry; it does not automatically supply UAVs, charging capacity, orders, collision handling or a complete simulation.

## 2. Space → Region: preview, review, apply

Open **Configure → Space → Region**. For a new OSM region, clear the fixed `city: huangpu` choice (choose `none`) so that demo city and compiled region do not compete.

1. Select an extract from the configured catalog. `GET /v1/studio/catalog` exposes `extracts` with ids and bounds when known. It does not search place names or upload arbitrary host files. Without operator overrides the packaged extract is `wujiaochang`; Huangpu's display pack is a different source.
2. Fill **bounds** as `[west, south, east, north]` in WGS84 degrees, within that source's coverage. East must exceed west and north must exceed south. Keep the four values in this order, not ENU metres.
3. Set origin altitude `alt` in metres and `level_height_m` explicitly. For example, `0` and `3` are authoring assumptions, not measured terrain or building heights. The origin is computed from the selected bounds.
4. Click **Preview region**. Review bounds, new/previous ENU origin, origin shift, building and road counts, attribution, height source and every diagnostic. A preview writes nothing. Missing values remain unknown; a successful zero-building region is different from a failed request.
5. Review affected positioned entities, `network_invalidated` and `city_conflict`. Coordinates already in the draft are not automatically translated when the origin changes. The same ENU numbers can then refer to different geographic locations. Cancel or adjust positions before applying if this is unintended.
6. Click **Apply region** only after review. Local edits are saved before the server mutation, and the returned workspace becomes the draft. Application persists the compiled region and invalidates its prior road network; it does not start a run. Reopen Space to inspect the saved region.
7. If you intend to simulate road traffic, optionally click **Generate road network** after applying. The backend uses SUMO `netconvert`; it must actually be installed on the server. A network-generation error is not a usable network. Replacing the region requires regeneration. [SUMO](sumo.md)

Height tags and appearances are separate. OSM `height` is source-reported; `building:levels × level_height_m` is an explicit estimate. With neither, a building remains a footprint with missing height. The ENU ground is a planar coordinate surface for the declared extent, not surveyed terrain. Neither a rooftop-looking mesh nor a selected city certifies a landing site. Confirm positions against actual source data and assumptions.

### Region API behind the buttons

[HTTP API reference](../reference/http-api.md) and [map builder](../../tools/build_map.py) are the advanced paths. For workspace `WORKSPACE_ID`, the preview contract is:

```text
GET /v1/studio/catalog
POST /v1/studio/workspaces/WORKSPACE_ID/region
```

```json
{
  "extract": "wujiaochang",
  "bounds": [121.50, 31.29, 121.51, 31.30],
  "alt": 0,
  "level_height_m": 3,
  "dry_run": true
}
```

The bounds above are an illustrative area, not a guarantee of source coverage; use bounds from your actual catalog and chosen study area. The response is `{"preview": {...}}` with `extract`, `bounds`, `origin`, `previous_origin`, `origin_shift_m`, `buildings`, `roads`, `attribution`, `height_source`, `diagnostics`, `affected_entities`, `network_invalidated`, `city_conflict`. Some origin values may be null. To apply, save draft edits first and POST the same source/bounds/assumptions without `dry_run`; this returns the updated workspace. This region contract does not accept a revision field. A 404/422 or absent preview is an unavailable/invalid operation, not permission to apply blindly.

The existing road-network endpoint is `POST /v1/studio/workspaces/WORKSPACE_ID/network`; geometry can be inspected at `GET /v1/studio/workspaces/WORKSPACE_ID/buildings.geojson`.

### Adding another extract (operator step)

The region API compiles **server-configured local OSM XML**, not an online fetch on every click. The operator can prepare input with [tools/build_map.py](../../tools/build_map.py), from the installed repository root:

```sh
python tools/build_map.py --osm src/aeroagentsim/authoring/assets/wujiaochang.osm.xml --out generated/my-region --alt-m 0 --level-height-m 3 --public-url https://your-host.example/maps/my-region/city.geojson
```

This copyable offline example compiles the packaged XML. For your area, replace `--osm` with a real local file. Alternatively replace that input option with `--fetch-bounds W S E N` using four actual numbers, and optionally `--endpoint URL`. The default endpoint is `https://api.openstreetmap.org/api/0.6/map`; it receives `?bbox=west,south,east,north`. Choose a small extent and check the endpoint, ODbL terms and output location before fetching. The tool does not estimate download size or ask for confirmation.

The output is `source.osm.xml`, `city.geojson`, `scene.json`, `presentation.json`, `provenance.json` and `ATTRIBUTION.txt`. Inspect diagnostics, source, bounds and altitude/level-height assumptions. Keep the source and provenance. `--public-url` is an authored URL; the script does not host it, register the extract, generate a Release ZIP, produce asset hashes or create a SUMO network. Serve that URL yourself if using the standalone presentation; the workspace API serves its own compiled geometry.

Configure the server's `AEROAGENTSIM_OSM_EXTRACTS` as a JSON id-to-local-file mapping before starting it, for example `{"my-region":"/absolute/path/to/generated/my-region/source.osm.xml"}`. This replaces the packaged extract list; include every extract you want to retain. Restart with this setting, confirm `my-region` in `/v1/studio/catalog`, then select it in Region. The API recompiles the selected bounds from XML; it does not import a `scene.json` path or treat a generated directory as an installed Huangpu pack.

## 3. Objects → Add objects

Click **Add objects**. Choose an instantiable type present in this project's contract catalog, an id/prefix, count, placement kind and execution component. A small teaching setup could contain two UAV instances, a pickup facility and a destination facility; these are your authored experiment inputs, not objects inferred from OSM buildings.

Enter positions as **ENU `[E, N, U]` in metres** relative to the region origin: east, north, up. In the 3D renderer east maps to X, north to negative Z, up to Y; enter ENU values, not renderer coordinates. Choose real source locations or label hand-entered positions as assumptions. A count greater than one must produce distinct instance ids; inspect every resulting position rather than assuming distinct rooftop sites.

Review initial facts, units and field owners. Generic placement supports `entity`/`facility`/`airspace` and `kinematic`/`workflow`. Kinematic placement authors initial velocity and energy assumptions; these are editable model inputs, not measurements. Other plugins require explicit engine configuration and bindings. In particular, placing a generic UAV does not attach the low-altitude delivery task model. If the necessary concrete type or executor is absent, finish that configuration before claiming a runnable courier experiment.

The instance rows and draft preview should agree. Fleet generator count is separate from explicitly listed instance count; runtime expansion is visible after initialization. Use per-row **Remove** to inspect dependent references before removal. Cancelling preserves the object; confirmed removal must remove its dependent references, not leave broken tasks behind.

## 4. Relations → Add relation

Click **Add relation**, choose a relation defined by the active contract, and select real source/target instance ids. Check endpoint types and direction. Review facility capacity, open state, charging resources or service ownership separately; naming a charging pad does not create charging power or an energy executor. Nonspatial orders and ownership relations belong in the list/graph, not at invented map coordinates.

If the relation or endpoint is unavailable, fix the contract or Objects first. You can return to those sections without starting execution. [Relations and contracts](../concepts/aerograph.md)

## 5. Tasks → Add order stream, then rules

Click **Add order stream** for the business model you actually configured. Select task kind, pickup/destination instance ids, count/release schedule, payload, priority and delivery window where applicable. For a small courier example, choose a fixed two-order workload released inside a short run; these counts and timings are authored inputs. Record target count separately from released and completed counts.

A blank motion/workflow world does not yet contain the low-altitude task contracts, generators, site facts, dispatch, energy and command bindings used by delivery templates. The editor must identify missing requirements. Importing a complete compatible scenario through **File** is an advanced route; it is not proof that missing manual authoring controls work. [Task model and generators](algorithms.md) · [Example templates](templates.md)

Open the Tasks rule editor. Configure a trigger, participants, typed predicate with units/thresholds, true/false or wait branches, actions and an available executor. For example, a weather/energy policy can hold or return a UAV only if its engine exposes those actions. Check every saved edge and entity reference. A diagram or a threshold alone does not schedule a command. [Predicates](predicates.md) · [Event chains](behaviours.md) · [Low-altitude rules](lowalt-rules.md)

For optional incident response click **Add cooperation**: select watcher ids, incident time/position, notice radius, handoff points, helpers, protected tasks and battery limits. Start with explicitly **prescribed** responder/helper modes for an offline experiment. Agent mode additionally requires the runtime agent engine and a real provider. Invalid or failed model decisions record `lowalt.incident.decision_failed`; responder state becomes `decision_failed`. A handoff without an eligible acceptance becomes `failed` when a helper decision failed, otherwise `declined`. After that, prescribed mode chooses the next allowed ordered option; agent mode makes a new decision request against current state. Failure is not acceptance. [Failure semantics](agents.md#cooperation-failure-and-re-decision)

## 6. Components → Weather & traffic

Open **Weather & traffic**. Set authored wind values; click **Add gust** and enter start/end time in seconds and speed in m/s. Keep the gust within the run duration and record it as an experiment input. For example, a 10–20 s gust during a 60 s run is a schedule, not observed weather. A weather engine must own and compute the relevant facts, and the flight/task model must actually consume them.

For traffic, bind the generated network to an available traffic component and set its supported density, timing and seed. Generating roads alone creates no moving vehicles. Other motion, energy, communications and task engines belong in Components with explicit bindings: each field has one writer, every action has an executor. Missing plugins or network-generation failure remain errors. [Weather](weather.md) · [SUMO](sumo.md) · [Field ownership](../concepts/plugins.md)

## 7. Algorithms: check the consumer

Choose allocation, routing, conflict and charging slots as applicable to your experiment. Inspect parameters and actual engine consumers. The descriptor contract includes `consumers: [{engine, plugin}]`; absent/empty consumers mean applicability is unknown, not proof that the chosen algorithm runs. If an engine does not consume a slot, its dropdown cannot establish a behavioral effect. Changes affect the next version/run, never silently hot-swap a live run. [Algorithms](algorithms.md)

## 8. Validate → Save version → Initialize at t=0 → Start

In **Run**, choose duration and seed explicitly. For a first inspection use a short scheduled run, for example 60 s. Then:

1. **Validate**. Follow each issue to its section/field and correct actual contract references, positions, engine ownership and event bindings. Resolve a `/city` conflict by choosing either the fixed demo city or your compiled region. Preview success does not mean scenario validation passed.
2. **Save version**. Freeze the validated configuration. Later edits create a draft; they do not alter this version or an active run.
3. **Initialize**. Inspect actual instances, origin, city/source and initial facts at **t=0**. If there are no objects, the world is empty; return to Objects. If city geometry failed to load, a ground plane is not the selected region.
4. **Start**. Only this advances the simulation. Watch status and simulation time, not the preview animation. Pause/resume/stop use run controls; browsing an earlier recorded cut uses playback controls.

## 9. Check causality and replay

Select one instance in the object list or 3D; the inspector and relation graph refer to the same identity. Select a recorded event and open its **Why?** trace. Check the trigger, cut-specific predicate inputs/result, decision source, submitted command, receipt and committed consequence. An allowed proposal is not a completed action; an untriggered condition, a refused action, an unknown input and a failed decision have different meanings.

Open **Records** for replay and use the timeline to inspect the same historical cut across views. **Back to live** (`L`, without Shift) returns to the active run. Replay reads the journal; it does not rerun physics, fetch a new agent reply or create new success events. Story, when present for an applied assistant plan, compares expectations with recorded events; a blank project has no automatically invented plan story.

If `results_final` is false, terminal run status does not mean KPI/Story aggregation finished; wait for final records. If that field is absent, finality is unknown. Compare target/released/completed counts from the same final cut; missing metrics are not zeros. To change the experiment, derive a new draft with **Modify & retry**, edit one assumption and repeat validation/version/initialization/start. Keep the original run for comparison. [Visualization](visualization.md) · [Runs and provenance](../concepts/runs.md)
