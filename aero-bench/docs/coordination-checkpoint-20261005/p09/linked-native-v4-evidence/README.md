# P09 v4 exact-source and chain review packet

This is a bounded snapshot of actual v4 source bytes and generated representative outputs. It preserves the original published210 inputs. It is not the final adoption or capture manifest.

`source-manifest.json` identifies every copied source/config file. `source/` includes the actual linked adapter, ns-3 provider, receipt helpers, authored motion engine and interpreter. `episode-index.json` records the measured batch statuses and points to per-scenario chain packets.

Each representative has the original narrative/event and scene inputs, exact adopted script/scene/profile, native message receipts, action schedules, sampled event-boundary poses, all-actor final states and the concrete first before/after difference. These are actual generated values. No raw image/LiDAR archives, credentials or private provider telemetry are included.

Original ticks are100ms; the usual action/capture grid is5 ticks. Event admission evidence and action execution times are separate. Receiver-watchdog loss uses expected mature heartbeat slots, not old global accepted-TX loss. Native UDP receive/acceptance does not establish PX4 flight: motion here is the authored waypoint executor. Legacy event IDs remain; cochannel traffic is not certified wideband jamming.

A fired landing event does not establish touchdown. Inspect every actor terminal pose and appended duration, and retain narrative ordering/predecessors. The v4 extension only completes already admitted landing schedules at their original speeds/routes. Background entities/weather continue over any added ticks; their effect must be reviewed before UE collection.

Known unresolved cases in this snapshot include L2-1_v2 backup radio geometry and L6-4 admission/action-grid or command-deadline failures. They are evidence for repair, not accepted final versions. Read the actual per-episode status. No UE collection has started.

Boundary trajectory files are explicit field projections (entity_id,tick,pos_enu,vel_mps,yaw_deg,label_class,state,activity_type) from the named generated JSONL. Values are unchanged; repeated source metadata is omitted. Script/scene/source copies are exact bytes.
