# Six receipt-causal episode versions planned for adoption

All six independently pass PLAN_ADOPTED_RECAPTURE_REQUIRED. Final publication waits the combined necessary-enhancement freeze; original210 and ARM remain preserved. No UE/ARM capture has started.

| Episode | Actor | First pose/velocity difference | First activity difference | Changed pose/activity ticks |
|---|---|---:|---:|---:|
|L6-2_v1__seed00|uav_digital_l6_2_v1|43.1s|45.6s|359/82|
|L6-2_v1__seed01|uav_digital_l6_2_v1|43.1s|45.6s|359/82|
|L6-2_v1__seed02|uav_digital_l6_2_v1|43.1s|45.6s|359/82|
|L6-2_v2__seed00|uav_digital_l6_2_v2|43.1s|45.6s|314/81|
|L6-2_v2__seed01|uav_digital_l6_2_v2|43.1s|45.6s|314/81|
|L6-2_v2__seed02|uav_digital_l6_2_v2|43.1s|45.6s|314/81|

Original planned hold is preserved: v1 starts22.7s, v2 starts19.7s, both release43s. First0.5s capture-grid changed pose is43.5s; this is not a claim of the first changed pixel. Visual/runtime event materialization could affect an earlier frame. The final capture order must resolve all render dependencies, not use this pose bound alone.

Exact changed owners are uav_digital_l6_2_v1/v2. Scene bottom_center inspection camera is attached to u_inspect_l6_2_v1/v2 (unchanged actor). The changed mission aircraft may appear in RGB/depth/segmentation or LiDAR; scope does not establish formal sensor/channel instance IDs or every camera frustum. World-pose demo_high_overview has no proven formal capture role. No sensor mount, camera calibration, building geometry or entity catalog was changed by this receipt repair.

The change uses native ns-3 UDP receipts and a numerical waypoint executor, not PX4 control or a new alternate-radio/handover implementation. Gateway scheduled-sequence evidence is versioned separately from mature accepted-TX TTL loss. Commands carry exact sender/receiver epoch and accepted receipt before execution.

Evidence: `design/p09/receipt_repair/six_episode_adoption_review.json`; complete per-episode old/source and new/trajectory paths, changed-value examples, command timestamps, hold and sensor details are in that report. `current_episode_change_table.csv` includes the six planned A decisions. Other ten communication groups and other174 episodes are not automatically adopted.

The first43.1s pose/velocity difference comes from the new admission gate waiting after the43s planned release while the original script moves. Actual received-command motion starts later (v1:44.5s; v2:45.5s). The45.6s activity difference compares command_delay with airborne, not another command. See `receipt_onset_explanation.md/.json` for the existing trace and exact versions.
