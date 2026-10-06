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
- `l2_existing_phy_arp_diagnostics/`: exact existing v10/v11/v12 small raw packet/event/PHY diagnostic artifacts and inventory. v10 did not persist `network_diagnostics.jsonl`; its backend callback declaration is not raw ARP/PHY evidence. Existing null-packet preamble aggregates precede the fault/request window, and the eight saved diagnostics contain no ARP rows. They cannot certify the old request-failure cause. The profile basis labels that historical attribution unverified; `diagnostic_attribution_limits.json` records the limits and distinguishes TTL lateness from final non-receipt. No missing old rows were invented or regenerated for this export.
- `actual_scene_sensor_channel_clock_index.json`: exact scene cameras, actor/asset placement, weather and native radio references. Tick duration is 100 ms. Actual UE/ARM capture-clock/extrinsic mapping remains unverified.
- `source/`: changed modules, focused tests and export script. Unchanged evaluator/transport modules are in the pinned v12 packets. `manifest.json` fixes every exported byte after JSON whitespace compaction.

## Validation and outstanding dependencies

`focused_validation.json` records 7 coordinator tests and an independent source review with 6 backup tests. Both pass. No new native simulation, training or UE capture was run for this metadata repair.

The two L6 seed02 runs still fail their prescribed story with 11/7 missing events. The original watchdog threshold remains unchanged. A preloaded rendezvous is not a local backup observation, and stationary airborne entities are not certified landed. Resolve the fault-profile/story mismatch with actual ns-3 evidence before adoption. This packet does not resolve that mechanism, certify all 30 episodes, finalize the remaining 174, or establish ARM recapture dependencies.

Run the focused source tests from the AERO_WORLD root using the recorded command. A fresh export uses the exact existing v12 folders; output directories must be new. Review source overlays in an isolated integration copy, preserving the original v12 snapshots.

## Byte-preserving X1 operands and trajectory delta

Each X1 seed now includes `first_permission_source_poses_450_452.raw.jsonl` with the exact six original JSONL lines, plus `.raw-index.json` with original line numbers, byte offsets and per-line hashes. This adds all 18 source rows at ticks 450–452 for the two bound actors. The tick-to-nanosecond mapping is index metadata; original rows remain unchanged.

`full_trajectory_delta_comparison.json` compares every field of all 45,951 rows in the three transformed L2v2 files. Each seed has 901 route-metadata rows and 1,802 logical-corridor placement rows; every physical actor position, velocity, yaw, state and activity is unchanged. The other 27 current trajectories directly reference their exact immutable v12 source files. This is full comparison of the three transformed files and exact file identity for the other 27, not an inference from partial boundary poses. No simulator was rerun.

## Whole L2v2 old/new trajectory pair

`whole-trajectory-pair/old-v12.jsonl.gz` and `corrected-v13.jsonl.gz` contain the exact original JSONL bytes, with no redaction or reserialization. All three L2v2 seeds were SHA-checked and share this single pair. Each file has 15,317 rows.

| File | Original bytes | Gzip bytes | Original SHA256 |
| --- | ---: | ---: | --- |
| old-v12.jsonl.gz | 22,977,395 | 258,633 | 97462a9e44735879541fa58724fff0b5916350a6d6807bf9036c76f89e3ccf25 |
| corrected-v13.jsonl.gz | 22,928,741 | 258,394 | 20d82c462da5412422805e10577d6c044689e1f4b448044780a012f7b86d9efb |

The sibling `manifest.json` records compressed hashes, source paths for all three seeds, deterministic gzip settings and successful decompression checks. Reconstruct the files locally using Python stdlib `gzip`; compare original hashes before reviewing rows. This export closes access to the whole relevant trajectory pair. It does not certify the two failed L6 stories or authorize capture.
