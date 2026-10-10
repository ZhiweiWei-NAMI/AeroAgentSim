The executed three-segment check returns clear/clear/clear for the L2-1_v2
contact-route actions. Owner and exact action/profile/receipt bindings are in
`bindings.json`; all three unchanged adoption profiles are included. The copied
Python helpers are their actual original bytes. Versions: Python 3.10.14,
Shapely 2.0.7, GEOS 3.11.4 (CAPI 1.17.4), NumPy 1.26.4.

Parameters are horizontal 6m, vertical 5m, fallback building height 18m,
floor height 3.2m. No extra aircraft body radius exists. Vertex Z is not used
for height. Height is the maximum positive height/altitude/floor-derived value
or the explicit fallback. The retained helper preserves polygon holes and
MultiPolygon processing. Coordinates use lon/lat to Web Mercator, subtraction
of the recorded center and the stored affine fit; action coordinates and
clearances are in meters. The declared ENU origin is (30.5609,114.3627,24m),
fixed UE origin [0,0,0]cm. The fit has a negative second-axis scale; this is a
record of implemented fitted geometry, not surveyed geodetic certification.

All 1,261 source building features load. All three segments have zero candidates
under inclusive XY bounding-box intersection after the actual 6m expansion.
`candidate-buildings.geojson` is therefore empty. It cannot alone certify the
complete exclusion of source buildings. Full building/map input publication is
not authorized, so those inputs and their complete expanded-bounds inventory
remain PRIVATE at `/mnt/data1/weizhiwei/AERO_WORLD_runtime/p09/l2-clearance-repro/`.
The private packet contains original map_package/context/fit/bounds/building
bytes and a runnable `reproduce.py`; its result matches the original full-map
receipt and all three profiles. The input paths/sizes/hashes are in
`source-index.json`. The precise independent-transfer gap is the retained
831,551-byte source building GeoJSON and accompanying private bounds/fit/map
inputs; no complete map, model assets, RGB or LiDAR were uploaded here.

This code/report export does not run ns-3 or regenerate trajectories. The
independent whole-trajectory review of commit 07c277ce is already closed;
final adopted-vs-original capture impact remains a separate review.
