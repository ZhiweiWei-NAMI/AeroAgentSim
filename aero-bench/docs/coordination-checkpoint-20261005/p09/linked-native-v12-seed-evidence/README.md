# P09 same-v12 scoped seed receipts

This packet contains the exact outputs of 29 new bounded executions and the already reviewed X5 seed00 representative, using the 15 source files frozen at `fd407a065d4eb6f4ff5863c3bfb175eb64d17515`. The earlier packet `../linked-native-v12-review/` contains those source files, focused tests, and the representative policy probes. No source or RF-profile changes were made for these additional seeds.

Generation is not dataset adoption. The two L6-4 seed02 outputs have unresolved authored successors: v1 has no accepted command chain and both aircraft remain airborne; v2 completes the secondary aircraft chain but not the primary chain. These exceptions must be resolved before adoption. Actual `summary.json` lists the missing events. The outer batch launcher returned an empty missing-event list for these runs; this packet uses the persisted summaries, which take precedence.

The other 28 rows have no reported missing authored events. This fact alone does not grant parent acceptance. The independent numerical reviewer checks receiver-prefix availability, owner/epoch binding, weather evidence, timer policy, and terminal trajectories separately. Parent acceptance at publication remains limited to X5 seed00 and the four L6 metadata changes.

The executions use native shared-Yans ns-3 UDP receipts and the existing authored waypoint executor. They do not prove PX4 aerodynamic feedback. Application-result payloads are immutable submission-ledger records tied to actual RX; they are not a wire-JSON decoding or hardware-ACK claim. Audit `ok` counts refer to executed handlers; only `move_entity` handlers count as motion dispatches, and omitted handlers are reported separately.

Each episode folder contains the actual adopted script/scene/profile, event and weather traces, admitted event rows, actual native packet rows for every command attempt, full move-dispatch rows, bounded actor end trajectories, and a scoped receipt. `30_episode_resolution.csv` is the compact index. Trajectory differences compare current authored same-seed source replay against this new version; the historical captured trajectory hash is unavailable. The packet does not establish a first changed pixel or formal camera-channel identity.

All 30 rows propose A because observed motion/state/weather capture inputs differ. The two unresolved rows remain blocked. This is a proposed dependency classification, not an instruction to recapture now. Existing published210 and original ARM products are unchanged, and no UE/WZW407 capture was started.

`arm_source_lineage_review_scope.json` contains 143 existing ARM packages descended from these 30 episodes plus the six separately reviewed L6-2 episodes. It records 9,815,183,036 bytes of existing UE-input metadata scope (not image/LiDAR recapture size). These packages need source-version dependency review/replay; lineage alone does not mean every package requires new imagery. No ARM branch was regenerated here.

The final210+ARM freeze remains open: the two L6-4 exceptions, parent review of remaining same-version seeds, ARM render-input dependency resolution, and adoption of justified compute task profiles must finish before one combined A/B/C collection manifest. The other174 episodes are not automatically classified C.

## Episode index

| Episode | Seed | Accepted messages | OK / omitted handlers | Motion dispatches | Horizon ticks | Missing events | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| X5_comm_failure_to_pad_contention__seed00 | 0 | 6 | 18 / 5 | 7 | 900 | 0 | PARENT_ACCEPTED_REPRESENTATIVE_ONLY |
| X5_comm_failure_to_pad_contention__seed01 | 1 | 6 | 18 / 5 | 7 | 900 | 0 | PENDING |
| X5_comm_failure_to_pad_contention__seed02 | 2 | 6 | 18 / 5 | 7 | 900 | 0 | PENDING |
| L2-1_v2__seed00 | 0 | 2 | 10 / 4 | 4 | 900 | 0 | PENDING |
| L2-1_v2__seed01 | 1 | 2 | 10 / 4 | 4 | 900 | 0 | PENDING |
| L2-1_v2__seed02 | 2 | 2 | 10 / 4 | 4 | 900 | 0 | PENDING |
| L2-1_v1__seed00 | 0 | 2 | 10 / 3 | 4 | 900 | 0 | PENDING |
| L2-1_v1__seed01 | 1 | 2 | 10 / 3 | 4 | 900 | 0 | PENDING |
| L2-1_v1__seed02 | 2 | 2 | 10 / 3 | 4 | 900 | 0 | PENDING |
| L6-1_v1__seed00 | 0 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L6-1_v1__seed01 | 1 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L6-1_v1__seed02 | 2 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L6-1_v2__seed00 | 0 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L6-1_v2__seed01 | 1 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L6-1_v2__seed02 | 2 | 0 | 10 / 1 | 4 | 900 | 0 | PENDING |
| L5-1_v1__seed00 | 0 | 0 | 18 / 0 | 9 | 900 | 0 | PENDING |
| L5-1_v1__seed01 | 1 | 0 | 18 / 0 | 9 | 900 | 0 | PENDING |
| L5-1_v1__seed02 | 2 | 0 | 18 / 0 | 9 | 900 | 0 | PENDING |
| L5-1_v2__seed00 | 0 | 0 | 19 / 0 | 10 | 900 | 0 | PENDING |
| L5-1_v2__seed01 | 1 | 0 | 19 / 0 | 10 | 900 | 0 | PENDING |
| L5-1_v2__seed02 | 2 | 0 | 19 / 0 | 10 | 900 | 0 | PENDING |
| X1_rain_to_c2loss_to_forced_landing__seed00 | 0 | 0 | 36 / 0 | 14 | 900 | 0 | PENDING |
| X1_rain_to_c2loss_to_forced_landing__seed01 | 1 | 0 | 36 / 0 | 14 | 900 | 0 | PENDING |
| X1_rain_to_c2loss_to_forced_landing__seed02 | 2 | 0 | 36 / 0 | 14 | 900 | 0 | PENDING |
| L6-4_v1__seed00 | 0 | 8 | 16 / 2 | 8 | 982 | 0 | PENDING |
| L6-4_v1__seed01 | 1 | 8 | 16 / 2 | 8 | 900 | 0 | PENDING |
| L6-4_v1__seed02 | 2 | 0 | 6 / 2 | 2 | 900 | 11 | BLOCKED_UNFIRED_AUTHORED_CHAIN |
| L6-4_v2__seed00 | 0 | 8 | 16 / 2 | 8 | 928 | 0 | PENDING |
| L6-4_v2__seed01 | 1 | 8 | 16 / 2 | 8 | 900 | 0 | PENDING |
| L6-4_v2__seed02 | 2 | 4 | 11 / 2 | 5 | 900 | 7 | BLOCKED_UNFIRED_AUTHORED_CHAIN |
