# Ground Unit Diagnosis — A1 Native Road Geometry Defects

Unit: `ground-diagnosis` (read-only). Branch: `repair/inspection-v1-r5`.
Network under test: `validation/stage1-continuation-20260930/native-refinement/network.net.xml`
(sha256 `4c5463ec6de3d8818e65c4bcb39676792c46141b1ce097a121123bc6b13603c3`), produced by
`refine-ground-network-physical.py` (3 iterations) over the `author-ground-city-streets.py`
round-2 authored network over `base-sidewalk-v1`.

Machine-readable companion: `validation/frontend-opus-20260930/ground/a1-diagnosis.json`
(one entry per defect, 13 walking areas + 1 collapsed lane + 2 building contacts + Q4/Q5 blocks).

## Method and evidence

- `reproduce_audit.py` re-ran `audit_native_road_physical.audit()` and
  `lane_building_conflicts()` on the final network; the gate output matches
  `validation/stage1-continuation-20260930/native-refinement/final-physical-audit.json` exactly
  (1 collapsed native lane, 2 lane–building contacts totaling 0.5717960719757496 m²,
  13 diagnostic-only road-end carriageway crossings).
- `probe_net_xml.py`, `probe2.py`, `probe3.py`, `probe4.py` (this directory) measured raw
  net.xml shapes, junctions, connections, OSM way tags, cross-network comparisons, and the
  walking-area validity census. Outputs: `*.out` files alongside.
- No netconvert or Docker runs were performed by this unit; all geometry is read from the
  existing network files and the canonical building-footprint pipeline.
- Coordinates are netconvert native frame unless a field name says `_enu` (canonical ENU from
  `CityEnuProjection`, which also rounds to 0.01 m at `city_preview_coordinates.py:395-396`).

## 1. The 13 undrawable walking areas

Gate rejection (identical for all 13):

- Rejecting code: `frontend/scripts/city_road_physical_clearance.py:63-69`. Line 63 builds the
  ribbon as `Polygon(shape)` for a walkingarea lane; line 66 `if not ribbon.is_valid:` sets
  reason `source-generated-geometry-is-invalid` (line 67). The area epsilon
  (`NUMERICAL_AREA_EPSILON_M2 = 1e-8`) is irrelevant here — validity, not area, is what rejects.
- Failing property: each walkingarea lane shape is a 4-vertex self-intersecting ("bowtie")
  quadrilateral. The vertices are ordered [A, B, C, D] with A,B on one side of the road axis and
  C,D on the other side, so edges A–B and C–D cross. Shoelace area is 0.000–0.039 m² while the
  real pedestrian pad spans the ~8.4 m gap between the two opposite pedestrian-lane end
  cross-sections (≈21 m² at 2.5 m half-width sum).
- Shared pattern: all 13 sit at junctions carrying exactly one road (an edge plus its reverse).
  There, netconvert builds the walkingarea directly between the two opposite pedestrian-lane end
  cross-sections and orders them into a bowtie. The 249 valid walkingareas (census in
  `probe3.out`: 262 total, 13 invalid) sit at multi-road junctions, or at single-road junctions
  with different geometry (e.g. `:-3445726938607203646_w0`, valid, area 0.0407 m²).

The 13 items (lane ID, junction, junction type, position, shape, shoelace area, adjacent road
edges, adjacent ped lanes, ped-end gap):

| # | Walking-area lane | Junction (type) | Junction pos | Shape (native) | Shoelace m² | Adjacent road edges | Ped lanes | Ped gap m |
|---|---|---|---|---|---|---|---|---|
| 1 | `:-1003308918772823992_w0_0` | `-1003308918772823992` (priority) | 500.00, 287.56 | 497.29,289.28 495.61,290.36 504.38,284.77 502.70,285.85 | ~0 | `∓1595760198435336960` | `∓1595760198435336960#0_0` | 8.40 |
| 2 | `:-1165150142818133656_w0_0` | `-1165150142818133656` (priority) | −8.28, −500.00 | −5.30,−498.84 −3.91,−498.28 −12.63,−501.77 −11.24,−501.20 | 0.0367 | `∓1247804031848679839` | `∓1247804031848679839_0` | 7.89 |
| 3 | `:-1527976614960479335_w0_0` | `-1527976614960479335` (priority) | 490.44, 500.00 | 487.11,499.81 485.23,499.12 494.97,502.76 493.09,502.06 | 0.0393 | `∓2074367341460392823#0` | `∓2074367341460392823#0_0` | 8.39 |
| 4 | `:-1847048955233315643_w0_0` | `-1847048955233315643` (priority) | −41.94, −500.00 | −40.00,−502.53 −39.08,−503.73 −44.80,−496.27 −43.89,−497.46 | 0.0073 | `∓1030573598546369058#0` | `∓1030573598546369058#0_0` | 7.90 |
| 5 | `:-2091254908104642748_w0_0` | `-2091254908104642748` (priority) | −500.00, −360.00 | −503.05,−360.96 −504.95,−361.56 −495.04,−358.44 −496.94,−359.04 | 0 | `∓2793684035288066258` | `∓2793684035288066258_0` | 8.40 |
| 6 | `:-2603642219458404469_w0_0` | `-2603642219458404469` (priority) | 500.00, −191.88 | 498.96,−188.86 498.30,−186.96 501.70,−196.80 501.04,−194.90 | 0 | `∓908585916018614659` | `∓908585916018614659#0_0` | 8.40 |
| 7 | `:-3036205147719610591_w0_0` | `-3036205147719610591` (priority) | 187.66, −500.00 | 189.04,−502.88 189.90,−504.68 185.41,−495.31 186.27,−497.11 | 0 | `∓3740192274833699988` | `∓3740192274833699988_0` | 8.40 |
| 8 | `:-4005467982457213810_w0_0` | `-4005467982457213810` (priority) | −500.00, −496.13 | −499.57,−499.30 −499.29,−501.28 −500.71,−490.98 −500.43,−492.96 | ~0 | `∓4142023894684515987` | `∓4142023894684515987_0` | 8.40 |
| 9 | `:-4175503326203822200_w0_0` | `-4175503326203822200` (priority) | 255.31, −500.00 | 258.26,−498.77 260.10,−497.99 250.51,−502.02 252.35,−501.24 | ~0 | `∓2934865167392359321#4` | `∓2934865167392359321#4_0` | 8.40 |
| 10 | `:-471448697727590542_w0_0` | `-471448697727590542` (dead_end) | −500.00, −241.91 | −500.00,−241.91 −498.65,−244.81 −501.35,−239.01 −500.00,−241.91 (closed) | 0 | `∓1741003380043482228` | `∓1741003380043482228_0` | 3.19 |
| 11 | `:-640328710051389646_w0_0` | `-640328710051389646` (priority) | 500.00, 464.21 | 503.09,465.01 505.03,465.51 494.96,462.91 496.90,463.41 | 0 | `∓2074367341460392823#1` | `∓2074367341460392823#1_0` | 8.40 |
| 12 | `:10740120102_w0_0` | `10740120102` (priority) | 49.73, 215.89 | 52.76,216.92 54.66,217.56 44.81,214.22 46.71,214.86 | 0 | `∓4153330892299821064` | `∓4153330892299821064_0` | 8.40 |
| 13 | `:1316694021_w0_0` | `1316694021` (priority) | 470.36, −409.61 | 470.79,−412.78 471.07,−414.76 469.64,−404.46 469.92,−406.44 | ~0 | `∓3207781838701128933` | `∓3207781838701128933_0` | 8.40 |

Per-item ped-lane end cross-sections, exact areas, and junction shapes are in
`a1-diagnosis.json` → `walkingarea_bowties.items`. The junction polygons at these nodes are
slivers (0–0.24 m²), and the ped-lane ends are 8.4 m apart (3.19 m at the dead-end) — the
walkingarea lane is the only structure meant to span that gap, and its vertex ordering is what
makes it undrawable.

## 2. Collapsed native lane `:848690530_0_0`

- Lane/edge: internal edge `:848690530_0`, lane `:848690530_0_0`, shape
  `-91.98,121.05 -91.98,121.05` (two identical points), declared length 0.10 m, width 3.20 m,
  at priority junction `848690530` (position −91.98, 121.05; junction shape
  `-92.91,122.36 -90.97,119.80 -93.06,122.23`).
- Audit reason: `native-lane-has-fewer-than-two-distinct-displayed-vertices`
  (`city_road_physical_clearance.py:57` — fewer than two distinct projected points after
  0.01 m rounding; classified `straight_abutments` by `audit_native_road_physical.py`).
- Producing movement: connection from `-2208697673360521294` lane 0 to
  `-3796132141175297696` lane 0, `dir="s"`, via `:848690530_0_0`. The movement is a
  near-straight continuation (turn angle ≈ 176.5°) of the same through-lane at OSM node
  848690530; netconvert connects the lane's two end cross-sections, and both endpoints land on
  the same rounded point, so the internal shape degenerates to `X,Y X,Y`.
- Producer trace: the collapsed lane is byte-identical in the base network
  (`validation/stage1-roads-20260930/base-sidewalk-v1/network.net.xml`), the authored round-2
  network, and the final network — it pre-exists the authored cross-section work. The authored
  profile for both ways is `authored-ground-streets/v2` with
  `aero_bench:authored_ground_surveyed="no"` (design widths, not survey), and the movement
  carries the plain-secondary cross-section; nothing in
  `validation/stage1-roads-20260930/closed-loop/round-2/authored/authored-streets.edg.xml`
  introduces it (internal edges are netconvert-generated).
- OSM ways: `-2208697673360521294` ← source way `1323097578` 人民路
  (`highway=secondary, oneway=yes, cycleway:right=lane`) into node 848690530;
  `-3796132141175297696` ← source way `1292578228` 人民路 (same tags) out of it. Both carry
  `aero_bench:authored_ground_profile=authored-ground-streets/v2`.

## 3. The 2 building contacts (footprint `building.way.505649231.component.0`)

Building footprint source of truth: the canonical footprint loaded through
`load_canonical_city_geometry()` — `CanonicalBuildingFootprint` with fields `outline`/`holes`
and the `rendered_polygon` property (`frontend/scripts/city_preview_coordinates.py:68-87`),
from objects.json, cross-verified byte-identical against the osm2world pack GLB footprints via
the building-render manifest. Footprint: 6-vertex outline, area 200.1026 m², ENU bounds
[-341.219, 488.441, −316.999, 500.0].

### Contact 1 — internal turn lane `:1315785608_1_0` (0.4630875290466036 m²)

- Movement: `--3388259699730519464#6_0 → :1315785608_1_0 → -345235722812324524#1_0`, `dir="l"`
  (left turn, −90.7°), at right-before-left junction `1315785608` (−314.51, −499.65).
- Lane shape (native, 5 vertices, length 6.99 m, width 3.2 m): `-312.41,-501.67 -313.14,-500.00
  -314.29,-498.97 -315.86,-498.57 -317.85,-498.80`.
- Overlap (ENU): bounds [-318.03370172684424, 497.1014271520596, −316.9989648903296,
  497.7506431406612]; area 0.4630875290466036 m²; ribbon valid; spine metrics
  `native_spine_inside_building_m = 0.0`, `native_spine_boundary_contact_m = 0.0`
  (classification `native-generated-geometry-contact`).
- Cause: internal-lane turn shape. The overlap bounds touch the footprint's
  max-x/max-y corner exactly; the 3.2 m ribbon swept along the 5-vertex turn arc crosses that
  corner while the arc's axis stays ~6.1 m parallel-separated (6.3 m tangent distance) from it —
  far beyond the 1.6 m half-width, so lane width alone cannot explain it. Junction-corner
  rounding is excluded: the junction polygon is a 6-vertex (not round) shape, the lane starts
  2.91 m / ends 3.45 m from the junction center (outside the junction polygon), and the lane's
  endpoints coincide exactly with the adjacent normal-lane endpoints
  (`perpendicular_corner_offset_m = 0.0`, `probe4.out`).
- Note: the authored junction shape had 12 vertices; the final has 6 after
  `refine-ground-network-physical.py` deleted 7 movements + 3 edges. This turn survived because
  deleting it was not connectivity-neutral per `_not_worse` (absolute
  boundary_connected_edges + interior_dead_end_edges, `refine-ground-network-physical.py:35`).
- OSM ways: `-3388259699730519464` ← `116783909` (`highway=service`);
  `-345235722812324524` ← `116783834` (`highway=service`); both
  `aero_bench:authored_ground_profile=authored-ground-streets/v2`, `authored_ground_surveyed=no`.

### Contact 2 — normal stub lane `-345235722812324524#1_0` (0.10870854292914595 m²)

- Edge `-345235722812324524#1` (normal, `spreadType="center"`), junction `1315785608` →
  dead_end `-1489314128297898851`. Lane shape (native): `-317.85,-498.80 -318.04,-498.85`
  (0.196 m long, width 3.2 m).
- Overlap (ENU): bounds [-318.44718927884753, 497.2526807403798, −318.1162726628764,
  497.83738532800754]; area 0.10870854292914595 m²; ribbon valid; spine fully outside the
  building (`native_spine_inside_building_m = 0.0`; classification `lane-ribbon-only-contact`).
- Cause: authoring placed the designed lane axis within the 1.6 m half-width of the footprint
  corner, so the 3.2 m ribbon of the 0.20 m stub crosses the corner. The authored shape was
  `[-314.88479542,-498.09071844 -316.33601734,-498.44065390]`; netconvert only truncated it at
  the junction boundary — no junction-corner rounding artifact created the overlap.
- OSM way: `-345235722812324524` ← `116783834` (`highway=service`).

Width context: 3.2 m is the authored motor design width (`city_authored_ground_streets.py:20-31`,
`width_basis: "authored-design-not-surveyed"`). Width is the *vehicle* of both overlaps (the
ribbon is what touches), but the *cause* differs per lane: turn-arc sweep (contact 1) vs axis
placed too close to the corner (contact 2). Neither is a junction-corner-rounding artifact.

## 4. Netconvert option inventory and candidate native levers (listed only, nothing applied)

Options that control the relevant generation surfaces:

| Surface | Options |
|---|---|
| Walking-area generation | `--walkingareas` (currently on), `--no-internal-links` (suppresses internal links incl. walkingarea lanes as separate edges) |
| Junction corner radius / shape detail | `--junctions.corner-detail`, `--junctions.detail`, `--rectcorner-lane-length` |
| Internal lane shapes / turn geometry | `--junctions.limit-turn-speed` (+ `.minimal-shape`), `--no-internal-links` |
| Junction & geometry simplification | `--geometry.remove` (+ `--geometry.min-radius`, `--geometry.min-radius.fix`, `--geometry.remove.keep-edges.explicit`), `--junctions.join`, `--junctions.join-dist` |

Candidate native levers per defect class (candidates only — no threshold changes, no building
carving, no polygon padding, no skipped connections, no ID-specific rules):

- **Walking-area bowties**: a lever must change how netconvert orders the two opposite
  pedestrian-lane end cross-sections when constructing the walking-area quad at one-road
  junctions (walking-area generation / internal-link construction options above). Junction
  detail options act on the junction polygon, which is a separate structure from the
  walking-area lane shape — measured evidence places the defect in the lane shape.
- **Collapsed internal lane**: native levers on junction/geometry simplification
  (`--geometry.remove` family, `--junctions.join-dist`) or internal-lane shape generation
  (`--junctions.limit-turn-speed` family, `--no-internal-links`); an authoring-level lever is
  via-node handling in `author-ground-city-streets.py` (the collapsed lane pre-exists in the
  base build, so any fix likely lands before or at base generation, not in the authored layer).
- **Building contacts**: authoring-side axis placement relative to canonical footprints
  (clearance semantics of the offset in `city_authored_ground_streets.py`, cross-section
  preference list) and the connectivity-neutral deletion rules in
  `refine-ground-network-physical.py`. Netconvert junction-detail options are listed for
  completeness but the evidence already excludes junction-corner geometry as the cause.

## 5. Baseline vitest failures (diagnostic only; nothing edited)

Command: `cd frontend && npx vitest run src/city-road-assets.test.ts src/city-workspace-geometry.test.ts`
→ exit 1: **2 test files failed, 12 failed / 20 passed of 32**. Full log:
`vitest-ground-baseline.out` in this directory.

### `src/city-road-assets.test.ts` — 11 failures, one root cause

All 11 fail with `Error: City road assets and building render do not share the verified mesh
pack source` thrown from `city-road-assets.ts:91` (condition block `:85-91`), before each test
reaches its own assertion:

- Root cause: `public/city-presentation/building-render-scene-v1.json`
  `road_assets.mesh_pack_manifest_sha256` pins
  `d4d5c6f9f662dd707ccbbb9ee822f980692b54494e2035262cf6ab7e30ca0449`, but the actual file
  `public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json` hashes to
  `8f70bc96be129d5458336464026070fdab2af22ef12fa7435ead255e0300bc98` (verified with `sha256sum`).
  `scene.mesh_pack.manifest.sha256` (line 89's other comparison) is already the fresh
  `8f70bc96…`; only the road_assets reference is stale.
- Git lineage: commit `8d70175` (Sep 25) introduced the pack manifest with digest
  `d4d5c6f9…`; commit `6dfe79a` (Sep 29, "Stage 1 baseline: frozen canonical pack and building
  render assets") regenerated the manifest file to `8f70bc96…` without updating the road_assets
  reference. The `dist/` copies still carry the stale digest, consistent with reference (not
  build) drift.
- Classification: **changed fixture** (stale digest reference inside the generated scene
  config). Not an intentional contract change — the code contract and both test sides agree
  with each other; not a code defect — `verifyCityRoadAssetBindings` behaves exactly as
  specified, and the 10 negative-path tests fail only because the shared upstream throw fires
  first. Fix belongs to the fixture owner (re-pin the reference from the regenerating
  pipeline); tests must not be weakened.

### `src/city-workspace-geometry.test.ts` — 1 failure

- `normalizes the actual default scene's placement and motor road records`:
  `city-workspace-geometry.test.ts:58` expects `normalizeRoadbed(roads.roadbed)` to have length
  510, received 519 from `public/city-presentation/huangpu-night-road-preview-v1.json`.
  `normalizeRoadbed` (`city-workspace-geometry.ts:177-194`) is a 1:1 map and is correct.
- Git lineage: commits `8d70175` and `26bddf0` carried 510 entries; commit `ae331dc` (Sep 27,
  "Build explicit city street layouts and reflective facade detail") regenerated the fixture to
  519. `source_network_sha256` (`b5fb0c98aabc…`) is unchanged across all three commits, so the
  +9 entries come from the explicit-sidewalk-layout work, not from a new SUMO network.
- Classification: **changed fixture** with a stale test constant (count-only drift after an
  intentional fixture regeneration). The test logic is sound; the hardcoded count is the stale
  side and must be reconciled by the fixture owner, not by loosening the assertion.

No missing fixtures were found; no code defect in `city-road-assets.ts` or
`city-workspace-geometry.ts`. These 12 failures are part of the documented pre-existing baseline
of 41 vitest failures in 12 files (generated public-trace contract drift).

## Measured facts / Inferences / Open questions

**Measured facts** (all from executed commands and file reads in this session):

1. The audit gate reproduces identically on the final network: 1 collapsed lane + 2 contacts
   (0.5717960719757496 m²) → BLOCKED; 13 road-end crossings are diagnostic-only.
2. All 13 walking-area lanes are invalid self-intersecting quads (shoelace 0–0.039 m²) at
   one-road junctions; 249 of 262 walking areas are valid (`probe3.out` census).
3. `city_road_physical_clearance.py:66` rejects on `ribbon.is_valid`, not on area.
4. `:848690530_0_0` is a 2-identical-point internal lane, declared length 0.10 m, on a 176.5°
   through-movement at node 848690530, identical in base/authored/final networks.
5. Contact 1 overlap = 0.4630875290466036 m², contact 2 overlap = 0.10870854292914595 m², both
   against footprint `building.way.505649231.component.0` (200.1026 m², 6 vertices); both spines
   stay outside the building (`native_spine_inside_building_m = 0.0`).
6. At junction 1315785608 the internal lane's endpoints coincide exactly with the adjacent
   normal-lane endpoints (offset 0.0 m) and lie outside the 6-vertex junction polygon; the turn
   angle is −90.7° (`probe4.out`).
7. Vitest: 12 failed / 20 passed of 32 in the two files; the 11 city-road-assets failures share
   the stale `mesh_pack_manifest_sha256` reference; the geometry failure is the 510→519 count.
8. Pack manifest digest lineage (8d70175 → 6dfe79a) and road fixture lineage (510 → 519 at
   ae331dc with unchanged source_network_sha256) verified through `git show`.

**Inferences** (reasoning from the measured facts, labeled as inference):

1. The bowtie ordering arises from netconvert's walking-area construction between two opposite
   ped-lane end cross-sections at one-road junctions (correlation: 13/13 invalid at one-road
   junctions, but 6 valid walking areas also exist at one-road junctions with different
   geometry).
2. Contact 1 is caused by the swept ribbon of the internal turn arc; contact 2 by the authored
   axis lying within half-width of the footprint corner. Junction-corner rounding is excluded
   for both by measurements 5–6.
3. The collapsed lane would likely persist under any authored-layer change, since it
   pre-exists in the base network build.
4. Both vitest failure groups are fixture-reference drift from concurrent upstream
   regeneration commits, not code defects and not intentional contract changes.

**Open questions**:

1. Whether a netconvert option (e.g. walking-area/internal-link generation variants) can
   eliminate the bowtie vertex ordering at one-road junctions without disabling walking areas —
   requires an actual netconvert run, which this unit was not authorized to perform.
2. Whether the base build (`base-sidewalk-v1`) can drop or re-shape the degenerate straight
   internal movement at node 848690530 via `--geometry.remove`-family options without breaking
   required connectivity.
3. Ownership and regeneration pipeline for `building-render-scene-v1.json` road_assets digest
   and the `huangpu-night-road-preview-v1.json` count constant — the fixes are one-line
   fixture re-pins, but they belong to the fixture owner's file scope, not this read-only unit.
4. Whether the building footprint's north-east corner region (ENU x ≈ −318…−317, y ≈ 497…498)
   was intended to be reachable by motor traffic at all (the authored service-way axis passes
   within 1.6 m of it); an authoring-intent question outside this unit's scope.
