# Why the first pose difference precedes command receipt

The43.1s difference begins when the original script resumes movement after its planned hold, while receipt-replay/v5 stays in `await_command_receipt`. It is a declared command-admission/wait-policy change. It must not be described as the aircraft reacting to a fault command received at43.1s or as an interpolation-only difference.

## One existing trace: L6-2_v1__seed00

| Time | Existing evidence |
|---|---|
|43.0s|Original planned hold releases. Both sampled poses match; the new executor waits for receipt.|
|43.1s|Original pose moves with nonzero velocity. New pose remains held with zero velocity.|
|43.900000001s|Gateway TX of `uav_delayed_response`.|
|44.409123885s|Aircraft RX and acceptance of the command.|
|44.5s|Next strict10Hz executor tick starts `slowdown_motion`; position is still the route origin.|
|44.6s|First displaced candidate position.|
|45.6s|Original activity is `command_delay`; new is `airborne`, phase `slowdown_motion`. This is an enum difference, not a second action.|

Post-release candidate velocity is the forward difference of sampled positions, matching ns-3 linear interpolation over the following interval. That explains nonzero velocity at the44.5s execution-origin sample; it does not explain away the earlier43.1s wait-policy difference.

The original planned hold is retained (v1:22.7–43s; v2:19.7–43s). Source-script commands after release are admitted only after real accepted receipts. The healthy branch uses the same gate. The implementation is a numerical waypoint executor with native ns-3 UDP receipts, not PX4 physical control.

## Exact comparison and all six measurements

Original: current preserved `aw_data/render_ready_episodes_capture_filtered/<episode>/truth_frames.jsonl`. New: independently reviewed candidate `receipt_replay/run1/mission_trajectory.jsonl.gz`, schema `p01.ns3.receipt-replay/v5`. Original formal input is not identified as a v4 replay. `L6-2_v1/v2` are scenario variants; both new versions use replay/v5.

| Episode | First pose/velocity difference | First activity difference | First command RX | Execution |
|---|---:|---:|---:|---:|
|L6-2_v1__seed00|43.1s|45.6s|44.409123885s|44.5s|
|L6-2_v1__seed01|43.1s|45.6s|44.415492105s|44.5s|
|L6-2_v1__seed02|43.1s|45.6s|44.413139885s|44.5s|
|L6-2_v2__seed00|43.1s|45.6s|45.404625603s|45.5s|
|L6-2_v2__seed01|43.1s|45.6s|45.404625603s|45.5s|
|L6-2_v2__seed02|43.1s|45.6s|45.404625603s|45.5s|

The43.1/45.6s entries were individually measured for six episodes in `design/p09/receipt_repair/six_episode_adoption_review.json`. They are not copied from the seed00 example. The accompanying JSON contains exact old/new paths and selected phase/activity comparisons without raw poses.

Source: `Dataset/semantic_simulation/ns3_episode/receipt_replay.py` `_plan_boundary` (line95), `_actor_execution` (line106), held pose (line336), receipt wait (line372), sampled velocity (lines402–405), and explicit source-command removal (line443). `controller_receipt_consumer.py` enforces owner/epoch/availability.

These six versions remain planned A adoptions. Changed poses require recapture after the combined enhancement freeze; original files remain preserved. The0.5s grid's43.5s changed pose does not establish the first changed pixel. This explanation used one bounded existing trajectory segment and the existing six-episode review; no simulation or capture was rerun.
