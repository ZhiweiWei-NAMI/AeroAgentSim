# P09 v13 derived-metadata review

This packet supplements the frozen native-execution packet `65423f411d2f51e3ccbec758ee38b843c5f3ebf6`. It does not replace that packet, publish a new 210-episode corpus, run ARM, or authorize UE capture. Actual network receipts and physical motion remain v12. The adopted source/derived annotations in this packet are v13-metadata.

## Changes requiring native review

- All three L2-1_v2 seeds now derive current scene route, planned route, enabled corridor placements and script corridor records from the same existing action route. Route points total 9; total scenario corridor segments are 31. Only owner corridor03/04 placements move in this revision. Full corrected trajectories remain on the server under `linked_native_v13_metadata_artifacts/`; boundary and terminal rows are included here. UAV/vehicle/other physical poses, velocities, states and motion actions were checked unchanged. Logical corridor geometry changes from tick 0, so UE geometry dependencies still require review.
- The three modified contact segments pass the executed existing fitted-map/building checker. The profile records checker inputs, fit, clearances and height assumptions. This replaces the unsupported hardcoded all-clear assertion. It is a check of fitted authored source geometry, not surveyed physical clearance.
- The 27 late dispatch bounds across 23 episodes are listed in `terminal_planning_bound_resolution.json`. Old planning certificates are retained under `p09_original_terminal_feasibility`, with original source reference/version, and excluded from current feasibility claims. Current annotations retain landing reference, origin altitude, touchdown tolerance/dwell and max speed. Actual dispatch/terminal schedules are separate execution receipts. No new planning certificate is claimed.
- The frozen 65423f4 packet already correctly reports the two L6 seed02 unfired-event lists as 11 and 7; it has not been edited. `outer_batch_persisted_status.json` and `source/batch_status.py` distinguish process completion from semantic completion by reading the persisted summary. Both failed episodes remain blocked. Other episodes remain pending independent review, rather than accepted merely because the process completed.

## Exact evidence locations

- `L2-1_v2*/cached_clearance_provenance.json`: the two cached v1 hardcoded clearance annotations are historical and excluded from current proof; links point to the actual v2-metadata geometry receipt. Cached action bytes and native receipts are unchanged by this sidecar.
- `30_metadata_resolution.json`: per-episode old execution revision, new annotation revision, trajectory references/hashes, changed rows, preserved physical rows, actual landing receipts and adoption status.
- `<episode>/scene_setup.json`, `event_script.json`, `adoption_profile.json`, `actions.json`: exact revised artifacts, not a prose substitute.
- `<episode>/causal_boundary_poses.json`: actual cached poses around event/availability boundaries; contains L2 arrival and X1/X5 nearby actors.
- `<episode>/whole_scene_last_five_ticks.json`: all scene actors, including L5 vehicles/X1 pedestrians, rather than only the second UAV.
- `L6-4*/whole_scene_extended_suffix.json`: all scene actors after tick 900 where the actual execution extended.
- `<episode>/native_raw/heartbeat_native_packets.jsonl`: all persisted heartbeat packet rows for that execution; failed L6 seed02 owner/time/flow lineage is directly inspectable. Receiver observations, event admission, radio actions and final run configuration are alongside them.
- `l2_existing_phy_arp_diagnostics/`: exact existing v10/v11/v12 small raw packet/event/PHY diagnostic artifacts and inventory. v10 did not persist `network_diagnostics.jsonl`; its backend callback declaration is not raw ARP/PHY evidence. No missing old rows were invented or regenerated for this export.
- `actual_scene_sensor_channel_clock_index.json`: exact scene cameras, actor/asset placement, weather and native radio references. Tick duration is 100 ms. Actual UE/ARM capture-clock/extrinsic mapping remains unverified.
- `source/`: changed modules, focused tests and export script. Unchanged evaluator/transport modules are in the pinned v12 packets. `manifest.json` fixes every exported byte after JSON whitespace compaction.

## Validation and outstanding dependencies

`focused_validation.json` records 7 coordinator tests and an independent source review with 6 backup tests. Both pass. No new native simulation, training or UE capture was run for this metadata repair.

The two L6 seed02 runs still fail their prescribed story with 11/7 missing events. The original watchdog threshold remains unchanged. A preloaded rendezvous is not a local backup observation, and stationary airborne entities are not certified landed. Resolve the fault-profile/story mismatch with actual ns-3 evidence before adoption. This packet does not resolve that mechanism, certify all 30 episodes, finalize the remaining 174, or establish ARM recapture dependencies.

Run the focused source tests from the AERO_WORLD root using the recorded command. A fresh export uses the exact existing v12 folders; output directories must be new. Review source overlays in an isolated integration copy, preserving the original v12 snapshots.
