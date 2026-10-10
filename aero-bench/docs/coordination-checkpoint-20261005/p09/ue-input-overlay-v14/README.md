# Existing P09 inputs: v14 technical capture window package

This directory copies and serializes existing recorded results. It runs no ns-3, motion engine, SUMO, training or UE capture. The original 210 and their capture-filtered directory remain unchanged. No RGB, LiDAR, map geometry or model weights are included.

- 6 L6-2 v5 and 24 linked v12 / v13-metadata episodes are the selected update sources.
- 6 L6-4 episodes retain the same old peer-load group, including 11 and 7 missing events for v1/v2 seed02. These actual results are included as requested. The two incomplete stories remain labeled incomplete.
- No tower-egress candidate is selected. Original peer v1 seed00 succeeded; tower-egress v1 seed00 introduced a READY-receipt regression. The additional reliable-control proposal is stopped and not activated.

`sources/` contains the exact existing trajectories, scoped scripts/scenes and recorded causal evidence. `source_manifest.json` binds each file to its original path, bytes and SHA256. `capture_filtered_updates/` contains a separate UE projection using the existing capture templates and background records. Only affected recorded actors are replaced. Projection does not certify physical event realization or recreate missing observations.

The original formal capture contract remains tick0..900 inclusive, 10Hz truth, capture step5: 90 seconds, 2Hz and181 planned sampling times. README.md and Dataset/tools/roi_contract.py in AERO_WORLD, together with both original L6 seed00 episode manifests and901 truth records, establish this scope. These are planned sampling times, not a claim that sensor files already exist.

Every ordinary UE truth/weather/trajectory input ends at tick900. This also enforces the window if an importer ignores supplemental metadata. `L6-4_v1__seed00` has additional actual records at90.1..98.2s and `L6-4_v2__seed00` at90.1..92.8s. Their projected suffixes remain in `additional_recorded_suffix/`; complete source trajectories and all event logs remain in `sources/`. Prefix+suffix byte hashes match the pre-partition projection. SUMO/global-UAV background has no recorded suffix, so that extra horizon is excluded from UE collection. No background is extrapolated or held forward.

`capture_window.json` and `multimodal_window_mask.jsonl` mark the outside-window multimodal support invalid/UNKNOWN. It is not an event-negative label. Inside-window mask eligibility does not assert capture success: actual image/LiDAR availability still comes from the user's subsequent collection. The UE client source is not present in this server checkout; no new capture_plan schema or client behavior is asserted.

Old event-realization/occupancy/planning annotations do not certify the new source. The current entry points to the copied current script and scene. Actual event admissions and receipts remain in `sources/`; the new empty realization file is explicitly NOT_RECOMPUTED, not a claim of zero events. Logged labels identify event admission, not an independently certified physical effect.

Use the companion `pull_entry.json` for the archive hash. Unpack into a NEW directory and select it as the data root; do not extract into the original210 directory. Each `scenario_package.json` and `render_host_config.json` refers to `capture_filtered_updates/<episode>/...` within this package. The Windows output path is versioned under `G:/aw_cap/_direct_render_host_capture_filtered_v14/`, preserving the old output directory. Existing logical model assets, sensor rig and map must already be installed in the user's UE project. This package does not transfer private map assets.

`final_ready=true` in the assembly/file manifests means technical serialized inputs within the original90s window passed file/reference/clock/background-binding checks. It does not mean all scripted stories passed, sensor calibration passed, UE ran, or the full210+ARM scientific adoption is accepted. `original210_reference_index.json` preserves174 original references; this assembly does not certify them as finalC. The parent delivers the final combined collection decision to the user once.

The 143 ARM records are dependencies, not 143 additional capture episodes. This package does not replace their source references or claim their updated outputs are verified.
