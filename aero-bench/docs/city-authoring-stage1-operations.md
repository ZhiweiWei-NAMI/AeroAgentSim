# Stage 1: Startup, Dependencies, and Acceptance

This document explains startup of a complete static presentation. Actual selection, publication, and browser acceptance from 2026-09-28 are recorded below. Ready requires both pack and presentation; a mesh-only job must not appear as an available city.

## Required Inputs

- The registered source is currently `shanghai-central-osm-v1`. The server reads raw OSM from a local allowlist and accepts no arbitrary browser-supplied URL or host path.
- OSM2World runtime, `tools/osm2world/web-integration.patch`, and the complete style tree must match build identity. The tree has 1203 files and 31,301,278 bytes, including 1202 files currently untracked by Git. See `required-authoring-assets-final.json` in the same directory; SHA/size for its 1208 old inventory paths were recomputed against the current main worktree. It excludes later BigCity and four incoming visual assets.
- All 596 entries in `frontend/public/models/bigcity/` and four incoming road-texture/lamp/signal files must exist under trusted `frontend/public`. Build validates every file, copies it into the content-addressed presentation, and includes complete visual identity in job ID. Missing assets fail without old-model fallback.
- The local Docker daemon must access the digest-pinned SUMO 1.27.1 image; actual netconvert generates the selection network. Browser builds require Node dependencies matching `frontend/package-lock.json`. Supply paired TLS certificate/key paths to Vite preview.

## Startup Commands

Run from the repository root, replacing persistent directories and absolute certificate paths; keep both processes running separately:

```bash
python -m aero_bench.authoring.api \
  --host 127.0.0.1 --port 8124 \
  --output /absolute/persistent/authoring-scenes
```

```bash
cd frontend
npm ci
npm run build
AERO_PREVIEW_TLS_KEY=/absolute/path/key.pem \
AERO_PREVIEW_TLS_CERT=/absolute/path/cert.pem \
npm run preview -- --host 0.0.0.0 --port 5209 --strictPort
```

The tracked `frontend/vite.config.ts` connects same-origin `/authoring/v1` to loopback port 8124, while `/v1` targets the separate formal Control API on port 8123. Authoring preview does not require formal Control API as its static-build backend. Certificates/keys remain local to the server and are not stored in the repository. The measured entry point was `https://<authoring-host-address>:5209/city-studio.html?tab=region`; the page and `/authoring/v1/sources` both returned HTTP 200.

## Acceptance Steps

1. The API source catalog returns registered sources only; browser selection uses actual OSM, with bounds, SHA, and origin matching the server. Selections too close to declared bounds for a safe enclosure fail explicitly.
2. After selection submission, observe `queued → compiling → meshing → networking → surfaces → placing → auditing → ready`. Instantaneous display states may be skipped; `ready` requires both pack and presentation references. Invalid inputs, missing images/assets, and build failures return explicit `failed` or 4xx states, without displaying an old city, old roads, or the original pack.
3. Check presentation manifests and every downloaded asset SHA/size. `raw_source_sha256`, `effective_osm_sha256`, and `sumo_source_osm_sha256` must match the registered file, cropped OSM, and SUMO inputs respectively. Network receipts, netconvert commands, and logs stay in private evidence, unavailable through HTTP. Completed jobs queried after API restart must pass evidence revalidation first.
4. Accept browser captures and console checks together: the new selection displays BigCity buildings/glass reflections, actual-network-derived lanes/sidewalks/crossings/markings/lamps, and signals at actual topological positions. Check orientations, scale, and continuous road surfaces. Neither `sceneReady=true` nor the original OSM2World pack alone establishes acceptance.
5. Clearly indicate a complete static city presentation that has not run. The selection is valid for this session only and must not mix into old-draft saves/exports. The view contains no vehicle/pedestrian trajectories, UAV flights, signal phases, or formal Provider evidence.

Start module acceptance with `python -m pytest -q tests/authoring tests/world/test_scene_compiler.py`; for the frontend run `npm --prefix frontend run typecheck`, `npm --prefix frontend run build`, and related Vitest modules. Module tests do not replace the browser records below.

## Measurements for This Iteration

On 2026-09-28, dragging a region on this page selected east `[-210.754, 408.169]` m and north `[-335.284, 235.505]` m in ENU. Job `308bfa08844020b3bd8d2fd8554c426f6e98c35a1765d5fccfa6cabc1734e604` reached complete `ready`; browser receipts/captures are in `validation/city-authoring-stage1-20260928/browser-accepted/`. Submission-to-`ready` measured 119.627 seconds, including 18.548 seconds after requesting presentation; internal assembly stages totaled about 5 seconds. The test browser used software WebGL; capture/mouse-interaction duration does not establish user GPU-browser frame rate.

The view has 115 visible buildings, 148 visual building parts, 1,129 SUMO lanes, 90 crossings, 394 lamps, and 61 static signal positions from actual control sources. Loading requested 190 selection resources with no mutable `/models/` paths. Manifest tampering intercepted one actual request and caused rejection on declared Content-Length mismatch. Complete road audit and browser triangulation also passed. There are still no running vehicles, pedestrians, UAVs, or signal phases.

Unclassified ground was then restored to a neutral color and initial selection cameras changed to oblique aerial views. Rechecks in `validation/city-authoring-stage1-20260928/aerial-ground-accept/` include the same selection's formerly white initial ground and rotated views. Counts remain 115 buildings, 1,129 lanes, and 61 signals, with zero mutable `/models/` requests. New-job first-build-to-first-frame measured 126.555 seconds, including 23.030 seconds after presentation request; subsequent submission of the same published selection returned the identical `ready` job in 695 ms. Gray open ground still reflects current OSM building/surface coverage limitations; density remains below the reference.
