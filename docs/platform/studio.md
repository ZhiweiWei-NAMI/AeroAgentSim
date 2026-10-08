# City Studio

City Studio authors `aeroagentsim.scenario/v1` YAML on the new platform. It uses
AeroGraph's registry compiler and the platform loader, explicit instance-field
writers, and the existing `/v1/runs` execution/replay service. Drafts remain
editable on disk; there is no sealing, credential, or publication step.

## Serve

Use the existing server extra (`fastapi`, `uvicorn`) and runtime dependency
`pyyaml`; no new Python or frontend package dependency is required.

```bash
AEROAGENTSIM_STUDIO_ROOT=/path/to/writable/studio-drafts \
AEROAGENTSIM_AEROGRAPH_ROOT=/path/to/AeroGraph \
aeroagentsim serve --port 8000 --out /path/to/runs --frontend frontend/dist
```

Set the two environment variables when starting the normal `aeroagentsim serve`
command. The minimal additive hook in `services/app.py` mounts `/v1/studio` when
`AEROAGENTSIM_STUDIO_ROOT` is set; `create_app(studio_root=Path(...))` also enables
it. Without that setting, the service retains its existing API. Open `/studio`
in the frontend, with `?api=http://127.0.0.1:8000` for a separate backend.
`/runs/...&mode=replay` uses the platform's existing journal replay viewer.

## Draft and source contracts

Each workspace stores `draft.json`, containing a separate scenario mapping and
optional city preview metadata. The YAML export contains only keys accepted by
the scenario loader. Maps and preview layers remain authoring metadata; a scene
preview does not configure an external simulator or prove collision coupling.

Local extract IDs are configured server-side. The default sources are the
read-only main checkout's `sumo_wujiaochang/osm_bbox.osm.xml` and
`sumo_berlin/map.osm`. No map request downloads or changes the source extracts.
The region is a WGS84 rectangle with an ENU anchor at its center. The user
supplies anchor altitude and metres per building level. Missing building height
must remain a diagnostic, and an estimate from `building:levels` records its
assumption. Attribution is © OpenStreetMap contributors, ODbL.

Type search reads persisted definitions across AeroGraph's seven browsing
directories. Type details compile real inheritance and effective fields, showing
schemas, units, frames and review provenance. Source proposals retain their
review state. Abstract types are rejected for placement; the facility and
spatial airspace templates explicitly declare `aas:StudioFacility` and
`aas:StudioAirspace` as concrete `oo:ModelObject` subtypes.

The point-mass template declares editable initial velocity, energy, speed,
acceleration and consumption parameters. These are scenario model assumptions,
not observations from a physical vehicle. The passive workflow template owns
configured fields and a `placed` state. Airspace polygons record geometry and
vertical extent; they do not assert an authority decision or automatically
install flight enforcement. Use the scenario editor for relations, predicates,
commands, heterogeneous partitions and pack-specific configurations.

Engine availability is measured from installed entry points and platform
built-ins. Selecting PX4/Gazebo, SUMO or ns-3 requires their real engine plugin,
configuration and instance-field bindings. Writer rows are explicit. Validation
calls `load_scenario`, constructs `Simulation`, and checks bootstrap; errors
remain visible. Run now posts the exact scenario to `/v1/runs`.

## SUMO

`POST /v1/studio/workspaces/{id}/network` runs local `netconvert` against the
cropped region OSM and checks the actual generated network. It requires SUMO
on the backend host. The `aeroagentsim/sumo:dev-p3a-5` image may supply
`netconvert`; mount the workspace directory and run the same command there,
then retain its generated `network.net.xml`. Tests use source/compiled fixtures
and a controlled executable; they do not require Docker. Network coordinates
and `netOffset` must be checked before binding traffic to the shared ENU frame.

## API

- `GET /catalog`, `/types?q=...`, `/types/{type_id}`: sources, plugins, definitions.
- `GET/POST /workspaces`, `GET/POST /workspaces/{id}`: disk drafts.
- `POST /workspaces/{id}/region`, `/place`: map and typed placement.
- `POST /workspaces/{id}/validate`: loader, engine and bootstrap diagnostics.
- `GET /workspaces/{id}/export`, `POST .../import`: versioned YAML round trip.
- `GET .../buildings.geojson`, `POST .../network`, `GET .../network.net.xml`: viewer sources.

All paths above are prefixed with `/v1/studio`. Source paths are configured by
the host; browser YAML cannot widen the configured ontology/workspace roots.
Draft updates invalidate prior validation. Invalid imports preserve the draft.

## Verification

P7b runs use the required Python 3.11 interpreter with `PYTHONPATH` pointing to
`src` and the platform's existing Python 3.11 site-packages for the existing
server/YAML dependencies. `PYTHONDONTWRITEBYTECODE=1` avoids touching modules
owned by concurrent jobs. Ruff, mypy, pytest and Vite caches/build outputs go
under the assigned authoring/Studio directories. The Playwright specification
uses real backend runs rather than mocked success responses.

Gate outcomes and reproducible commands are recorded after verification below.

The serve hook also freezes optional Studio GeoJSON with each exact matching
scenario and adds that source to the run header. Replay geometry is immutable
when the draft is edited later. SUMO lane geometry is retained as a separate
artifact; it is displayed only when its native projection/offset is explicitly
mapped to the shared frame. Authoring preview records missing heights rather
than inventing a building volume.

Studio run requests carry `{scenario, studio_workspace}`. The optional service
hook uses that workspace's configured source scope and schema-aware numeric
transport conversion. JavaScript serializes `0.0` as `0`; only supplied numeric
integers in declared floating `number` slots are restored to floats. Declared
integer slots remain integers; missing/null/Boolean/string values are preserved
for rejection by the loader. Ordinary run requests without the Studio marker
retain the existing run API behavior.

## Final gate results

- `pytest tests/authoring`: **38 passed** (Python 3.11.12; one upstream Starlette TestClient deprecation warning).
- Ruff: **passed** on authoring sources/tests and the additive service hook.
- `mypy --strict`: **passed**, all eight authoring modules.
- Frontend Vitest: **68 passed in 16 files**, including four new API tests.
- Frontend `tsc --noEmit`: **passed**.
- Frontend production build: **passed** (3155 modules).
- Playwright: **1 passed**, real offline region → two UAVs → facility → validation → completed kernel run → journal replay, without mock run responses or page errors.
- Native host `netconvert`: **generated 1 edge and 2 lanes** from the genuine OSM test sample. Tests themselves use the precompiled source network and do not require Docker.

Screenshots: `frontend/test-results/p7b-studio.png`, `frontend/test-results/p7b-replay.png`.
The API and geometry test drafts from two concurrent GLM sessions were reviewed,
corrected, and verified locally; `tests/authoring/glm-review.md` records the sessions.

Reproduce from the platform root:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:.venv/lib/python3.11/site-packages
export MYPYPATH=src:/mnt/data2/weizhiwei/aeroagentsim/aerokernel
export RUFF_CACHE_DIR=tests/authoring/.ruff_cache
AAS_PY=/mnt/data2/weizhiwei/aeroagentsim/aerokernel/.venv/bin/python
"$AAS_PY" -m pytest tests/authoring -q -o cache_dir=tests/authoring/.pytest_cache --basetemp=tests/authoring/.pytest_tmp
"$AAS_PY" -m ruff check src/aeroagentsim/authoring tests/authoring/test*.py src/aeroagentsim/services/app.py
"$AAS_PY" -m mypy --strict --follow-imports=silent --cache-dir tests/authoring/.mypy_cache src/aeroagentsim/authoring
```

From `frontend`:

```bash
npm run test -- --config src/studio/vite-gates.config.ts
npm run typecheck
npm run build -- --config src/studio/vite-gates.config.ts
npx playwright test --config src/studio/playwright.p7b.config.ts
```

The test configuration uses the same frontend Vite configuration while keeping
build/cache outputs inside `frontend/src/studio`. Playwright starts the real
backend on 8017 and preview on 4179. The existing browser cache supplies Chromium.

Integrator: refresh the platform installation metadata after merging P5-F/P8 so
its `px4_gazebo`, `sumo`, and `ns3` engine entry points appear in the installed
catalog. Their Docker images, adapter configurations and field bindings remain
explicit prerequisites to an external-simulator run. Studio does not substitute
kinematics for an unavailable engine. OSM selection retains whole intersecting
ways at the boundary and reports unknown heights; SUMO's native projection and
network offset are retained for an explicit adapter transform.

Position fields describe simulation `state`, updated by their explicit writer; authored initial conditions remain model assumptions rather than physical observations.
