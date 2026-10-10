# Ground streets A1–A3: native road geometry, fixtures, recording and viewer integration

Scope: Track A of the project delivery-status record (session record, omitted
from this export) on the current new city
(`shanghai-huangpu-east-v1`, 414 rendered buildings). Frontend-owned scripts under
`frontend/scripts/` and viewer modules under `frontend/src/`. No backend package,
container or formal-execution file was changed. Evidence is under
`validation/frontend-opus-20260930/ground/`.

Every number below was measured in this task unless marked otherwise. No reference-fidelity
claim is made: sidewalks, paving, markings and fixture placement are derived designs over the
SUMO topology, not surveyed geometry.

## 1. Native road geometry (A1)

Toolchain: SUMO netconvert 1.27.1 in image
`sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd`, run with
`docker --network none`, `--precision 4`.

### 1.1 Rules added or changed

| Rule | Location | Effect |
| --- | --- | --- |
| Netconvert precision 4 decimals | `author-ground-city-streets.py`, `refine-ground-network-physical.py`, `audit_native_road_physical.NETCONVERT_PRECISION_OPTIONS` | Native lane shapes carry 0.1 mm; 2-decimal output produced sub-centimetre lane gaps that the coverage check correctly rejected. |
| Lane centre lines at 0.1 mm | `city_road_physical_clearance.LANE_CENTRE_LINE_DECIMALS = 4`; used by the road builder, the recording gate and the canonical audit | Polygons (walking areas, crossings, junctions) stay at 1 mm; signals and actors keep the shared 0.01 m ENU projection. Rounding centre lines to 1 mm had made builder-frame overlaps 10× the native value. |
| `lane_kind` "closed" | `city_road_topology.lane_kind` | `disallow="all"` lanes are neither vehicle nor walk lanes. |
| Internal-lane ribbon aligned to adjoining lane ends | `city_road_physical_clearance.lane_ribbon`, `internal_lane_neighbours` | An internal lane's flat cap is trimmed where it crosses the entering lane's end line and the next lane's start line, limited to a one-lane-width box around each end. Used identically by the builder, `surface_lane_coverage` and `city_motion_obstacles.native_road_geometry_gate`. |
| Sidewalk/vehicle overlap escalation | `city_road_physical_clearance.walkway_vehicle_overlaps`, `build-ground-streets-closed-loop.py` | A normal walk lane (eroded by half the 3 mm seam) overlapping a vehicle lane escalates that edge's sidewalk level (1 minimum width, 2 no separate sidewalk, 3 no pedestrian access). A round passes only with the gate PASS and zero overlaps. |
| Ground-scope exclusion receipt | `refine-ground-network-physical.ground_scope_exclusions` | The refined receipt writes `ground-filter-report.json` (`aero-bench.ground-scope-exclusions/v1`) with removed edge ids by stage and fails if an excluded edge is still present. |
| Turnaround over walkway, generalized collapsed pads, level-3 escalation | earlier in this task (`city_ground_junction_completion.py`, `city_authored_ground_streets.py`) | Unchanged in this batch. |

No tolerance was relaxed: seam tolerance stays 0.003 m, area epsilon 1e-8 m², surface grid
0.001 m, building clearance and vehicle clearance unchanged.

### 1.2 Result

Closed loop `closed-loop-v6` passed in round 3 (`closed-loop-summary.json`):

- Gate: 0 collapsed native lanes, 0 collapsed turn lanes, 0 lane–building contact pairs
  (0 m²), 0 undrawable walking areas, 0 road-end carriageway crossings, 0 straight abutments.
- Sidewalk/vehicle overlaps: 7 → 7 → 0 → 0 across rounds 0–3; walking-area defects
  4 → 4 → 2 → 0; 15 edges carry sidewalk overrides in the final round.
- Network `round-3/refined/network.net.xml` sha256
  `38bd3d8a2609cecef7f0c98608e0d58eb9f5123448fd675a6e1d8dff6ccfeffc`; source OSM
  `refined-ground.osm` sha256 `0f985e5963b04b4096b4f93d0fd5523279ba7d850b8d64348ff99aa7b946e057`.
  (`closed-loop-v5` produced the same network except netconvert's timestamp.)
- Ground scope: 154 excluded edges (5 strict ground filter, 147 authored exclusions,
  2 physical refinement), listed in `round-3/refined/ground-filter-report.json`.
- Connectivity (largest strongly connected share of normal edges): passenger 0.7767,
  bicycle 0.7819, pedestrian 0.7521. Interior dead ends: passenger 50, bicycle 50,
  pedestrian 18. These are measured properties of the retained network, not a pass criterion.

## 2. Roads, sidewalks and fixtures (A2)

### 2.1 Road v3

`build-city-road-preview.py` over the v6 network produces `aero-bench.city-road-preview/v3`
in the canonical building ENU frame, embedding the rendered `source_context` and a
`physical_clearance` block (native lane clearance and lane surface coverage, both PASS).
Final build: `assets-v9/huangpu-ground-road-preview-v1.json` (sha256
`89ed7bfdb48d8a5d6628d4b5832725f28648ebbe16ce2870b53b494d17524702`), 1211 lanes, 1288 internal
lanes, 235 junctions, 39 crossings, 1084 street lamps.

### 2.2 Footprint precision model

`city_fixture_geometry` unions plan-view footprints and height-band clips on a declared
1 µm grid (`FOOTPRINT_PRECISION_M`): each triangle is snapped, collapsed triangles are dropped,
and the union is snap-rounded. The floating-point union of the 45 048-triangle signal model
raised GEOS `TopologyException` at real signal placements; the snapped union differs from the
floating union by about 6e-6 m² where both succeed. The same function feeds the builder's
building GLB audit, the effective-fixture build, the recording and the audit, so the chain
stays consistent (the road was rebuilt after the change because the audit values in
`source_context` changed).

### 2.3 Effective fixtures: two display-geometry defects found by the gate

The first complete effective-fixture build (`assets-v8`) omitted every fixture (0 of 154
signals, 0 of 1087 lamps). The gate was correct; the displayed geometry was not:

- Signals: the viewer scaled `traffic_light_4.glb` to 5.4 m, which put the arm's lower edge at
  3.04 m, inside the declared 0–3.41 m motor height band, reaching 2.93 m over the carriageway
  (120 omissions). The displayed height is now `SIGNAL_DISPLAY_HEIGHT_M = 6.4` in both
  `city_fixture_geometry.py` and `city-presentation.ts`; the arm's lower edge is 3.60 m, above
  the band by the gate's own 0.15 m clearance. Test:
  `test_displayed_signal_arm_clears_the_declared_motor_height_band`.
- Street lamps: the curb layout modelled each lamp as a 0.18 m pole circle 0.6 m from the curb,
  but the displayed `street_light_8.glb` has a bracket in the 2–3.41 m band reaching 0.586 m
  toward the road, 1.4 cm inside the curb (1087 omissions). The builder now derives the curb
  offset from the measured model: reach + 0.15 m pole clearance + 0.01 m placement safety =
  0.747 m, recorded in `street_lamp_layout.curb_offset_basis`. Test:
  `StreetLampCurbOffsetTest`.

The other 34 signal omissions in `assets-v8` were `measured-lower-pole-outside-dedicated-walkbed`:
native signal positions are not relocated, so a pole off the dedicated walkbed is omitted.

### 2.4 Effective fixtures after the fixes

`assets-v10/effective-fixtures-v1.json` (the v9 road is unchanged; build 41 min):

| Kind | Source | Effective | Omitted (reason) |
| --- | --- | --- | --- |
| Signals | 154 | 120 | 34 `measured-lower-pole-outside-dedicated-walkbed` |
| Street lamps | 1084 | 1023 | 60 `conservative-full-height-projected-effective-fixture-contact`, 1 `measured-model-in-declared-motor-height-band-over-roadbed` |

Every source signal and TLS stays in the SUMO network; omission affects only what is drawn and
what motion is checked against.

## 3. Recording prerequisites (A3)

Two defects in the frontend motion-obstacle modules blocked the first recordings; both were
measured before any change.

- **Road ring format drift.** Since `da99eda` the road builder serializes open rings (no
  repeated closing vertex). `native_road_geometry_gate` and the canonical audit parsed road parts
  with the fixture parser, which requires closed rings, so the recording stopped with
  "Fixture footprints require explicitly closed outer and hole rings". The gate's unit fixture used
  closed rings the builder never emits. New `city_motion_obstacles.road_surface_polygon` enforces
  the actual road format (open rings of ≥ 3 points, valid, positive area, no repair); the unit
  fixture now uses open rings and a test rejects closed rings. On the v9 road the independent
  gate then measured PASS: 2646 native ribbons, 0 model contacts, 0 missing surface ribbons,
  0 unrenderable ribbons (`private-native/*.native-road-geometry-gate-v2.json`).
- **Fixture motion obstacles used the full-height projection.** The recording then stopped with
  "SUMO provides fewer than six narrow, building-clear bicycle cycles". Measured on all 12
  candidate cycles (buffer 0.35 m around the bicycle lane): 0 building hits and 0 hits from
  fixture geometry within the declared 0–3.41 m motor band; every hit came from overhead parts
  (lamp heads, signal arms at 3.60–6.4 m). The obstacle model contradicted the height band the
  fixture gate already uses. Fixture motion obstacles are now the actual displayed triangles
  clipped to `[0, MOTOR_TOP_UP_M]`, stored per fixture as `motion_footprints` and re-derived from
  the GLB by `load_effective_fixtures` (tolerance 1e-8 m², unchanged). The declared band covers
  every actor (tallest: bus body 3.41 m). The full-height projection is still stored in `footprints` and
  still decides mutual fixture contact, and the motion basis records
  `route_fixture_geometry = actual-displayed-triangles-clipped-to-declared-motor-height-band`.
  Tests: `test_motion_obstacles_are_the_actual_motor_band_clip_not_overhead_parts` and the
  builder partition test.

A third defect was found by the audit itself: it required the substring "Version 1.27.1", but
the pinned image prints `Eclipse SUMO sumo 1.27.1` (as do all published recordings), and its unit
fixture used a string the binary never prints. The audit now requires that exact first line
(`SUMO_VERSION_LINE`), which is stricter; a test rejects 1.26.0, `-dev` and unprefixed strings.

## 4. Recording and continuous-motion audit (A3)

Recording: `build-sumo-city-preview.py` over the v9 road and v10 fixtures, 120 s, real SUMO
1.27.1 through TraCI in the digest-pinned image with `--network none`, read-only root, all
capabilities dropped and `no-new-privileges`. The pedestrian route filter converged on its first
run. Public output `aero-bench.city-sumo-preview/v2`, centre-referenced
(`center-derived-from-native-TraCI-front-bumper-and-length`); native records in a 0700 private
directory outside `frontend/public`.

| Measure | Value |
| --- | --- |
| Authored demand | sedan 26, taxi 9, truck 9, police 8, bus 8, bicycle 12; 36 persons |
| Observed by SUMO | 71 vehicles (sedan 26, taxi 8, truck 9, police 8, bus 8, bicycle 12), 36 persons |
| Displayed | 65 vehicles (all 12 bicycles), 36 persons; every declared fleet type present |
| Omitted whole actors | 6 (3 buses, 3 trucks): measured swept-body contact with fixture geometry inside the motor band, max 0.36 m², 20 private witnesses; 0 building contacts |
| Signals | 154 source signals / 64 TLS kept in the network; 295 transitions, 0 off-program states |
| Frames | 481 (0.25 s) |

Independent audit `audit-sumo-canonical-motion-v2.py`: **PASS**, 0 failures
(`assets-v10/private-native/canonical-motion-audit-v2.json`, 34 min, re-derives every fixture from
its GLB). Native trace samples checked: 20 818 vehicle, 8566 person, 30 784 TLS; route legality
0 lane/edge violations, 0 SUMO collisions; static penetration 0 offenders; surface coverage
0 off-surface (max person distance 0.0036 m); continuous public interpolation 20 753 vehicle and
8548 person intervals with 0 static contacts, 0 body-pair contacts, 0 unresolved.

## 5. Viewer integration

- Scene contract `aero-bench.city-road-assets/v2` (`city-scene-config.ts`): `road`,
  `effective_fixtures`, `traffic`, `flight`; the placement file is no longer part of the canonical
  path.
- `city-road-assets.ts` verifies the road's `source_context` against the verified building render
  and pack, the physical-clearance PASS and a rehash of the displayed surface, and the fixtures,
  traffic and flight source contexts. It also checks that the traffic obstacle basis names the
  exact fixture file bytes, and that the effective plus omitted fixtures cover each source inventory once.
  The viewer draws exactly the effective signals (by id) and street lamps (by index).
- Signals are displayed at `SIGNAL_DISPLAY_HEIGHT_M = 6.4` in `city-presentation.ts`, matching the
  Python geometry used by the gate.
- Published: `frontend/public/city-presentation/huangpu-canonical-{road-v3,effective-fixtures-v1,traffic-v2,flight-v2}.json`.
  `building-render-scene-v1.json` and `default-scene-v1.json` pin them. The default scene keeps
  its `ENGINEERING PREVIEW` label, now stating it is an offline SUMO road and traffic preview.
- Flight `huangpu-canonical-flight-v2.json` is a planned visual loop
  (`physical_simulation: false`): 414 rendered buildings, highest 120 m, lowest aircraft altitude
  145 m, minimum building clearance 24.65 m.

## 6. GPU browser acceptance

`frontend/scripts/capture-city-canonical-ground.mjs` on the production build (vite preview,
port 5209), Chromium with ANGLE/Vulkan on an NVIDIA RTX 3090. The script derives nine cameras
from the scene's own road, traffic and fixtures and saves them to `cameras.json`. Each camera is
captured at t = 34 s and 36 s, so each pair is a matched-camera motion comparison. Reusing the file
reproduces the views.

| Run | Scene | Load | Result |
| --- | --- | --- | --- |
| `browser-acceptance-v2` | `building-render-scene-v1.json` | 9.9 s | PASS: 120 signals and 1023 lamps drawn = effective inventory; 21–22 vehicles, 5 bicycles, 15–16 pedestrians, 2 UAVs active; 0 page errors |
| `browser-acceptance-default-v1` | `default-scene-v1.json` (same cameras) | 21.9 s | PASS, same counts, with the environment source (vegetation) |

Observed in the frames: signal arms stand above the carriageway and lamps sit behind the curb. At
t = 34 s and 36 s a car waits at a red signal's stop line while a cyclist moves. A pedestrian walks
on a paved footway. Vegetation stays in parks. Unverified observation: in `street-2` grass tufts at
the park edge appear near the sidewalk strip. The pixels have not been measured against the walkbed.

## 7. Limitations and open items

- Not reference fidelity: sidewalks are derived widths over SUMO topology, and broad flat walkbed
  areas remain at junction plazas.
- Vegetation is not yet road-cleared. `src/city-vegetation-layer.ts` still plans the
  default scene's vegetation with `roadGeometry: "pending-native-gate"` and empty road inputs
  (street trees disabled, park greens only). `validation/frontend-opus-20260930/visual/plan-summary.json`
  therefore still correctly records `road-clearance-pending-native-gate`. Binding the planner to the
  verified road v3 surfaces is the next integration step; it also resolves the unverified
  grass-near-sidewalk observation in section 6.
- Omissions stay omitted: 34 signals (pole off the walkbed), 61 lamps and 6 vehicles. No fixture
  is relocated, and no actor is edited.
- Connectivity of the retained ground network is 0.75–0.78 per class (section 1.2).
- The published effective-fixture file is 55.7 MB. The viewer downloads and hashes it to bind the
  traffic basis to exact bytes; this is included in the measured load times.
- Pre-existing HEAD drift, not changed here: the Stage 1 baseline commit `6dfe79a` re-froze the
  pack manifest (`d4d5…` → `8f70…`), while the legacy night, walkable and ground scenes and their
  placement files still pin `d4d5…`. Four Python tests fail on that pin. Re-pinning them would
  misstate their placement provenance. `city-workspace-geometry.test.ts` expects 510 roadbed
  polygons in the legacy night road but the committed file has 519; that failure appears in the
  session baseline.

## 8. Evidence

All under `validation/frontend-opus-20260930/ground/`:

- `closed-loop-v6/` native network rounds and `closed-loop-summary.json`.
- `assets-v8/` superseded diagnostic fixture build (all fixtures omitted), kept as the record of
  the two display defects.
- `assets-v9/`: road v3, flight v2, and their build logs. It also holds the fixture build without
  `motion_footprints` and the failed recording logs (`build-traffic.attempt-1-open-ring-parser.log`,
  `build-traffic.log` for the bicycle-cycle stop).
- `assets-v10/`: the published effective fixtures, public traffic, 0700 `private-native/`
  records, gate proofs and audit (with the first attempt's version-line failure kept), the full
  vitest log, and build logs.
- `browser-acceptance-v1/` (first capture, no actor views), `browser-acceptance-v2/`,
  `browser-acceptance-default-v1/`.
