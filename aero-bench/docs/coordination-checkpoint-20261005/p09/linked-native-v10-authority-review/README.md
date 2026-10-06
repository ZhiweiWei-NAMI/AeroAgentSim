# P09 v10 exact-source authority review packet

This packet addresses the independently reported v4 sender-authority, owner-scope, weather-availability and pending-landing defects. It contains five completed representative numerical candidates. Original published210 episodes remain unchanged. External native causal review precedes adoption and any one-shot capture decision. The legacy result directory name `adopted` means a coupled numerical fixed point; it does not mean dataset acceptance.

## Actual executions

| Episode | Native iterations | Last tick | Native coupled wall time | Unfired events |
| --- | ---: | ---: | ---: | ---: |
| L6-4_v1__seed00 | 9 | 982 | 84.821s | 0 |
| L6-4_v2__seed01 | 8 | 900 | 73.085s | 0 |
| X5_comm_failure_to_pad_contention__seed00 | 4 | 900 | 29.506s | 0 |
| L5-1_v1__seed00 | 0 | 900 | n/a (no network provider) | 0 |
| L5-1_v2__seed00 | 0 | 900 | n/a (no network provider) | 0 |

## Sender authority and physical scope

- L6-4 installs an explicit channel-6 rendezvous at original tick460 (46.0s) in the versioned profile before fault execution. Its source basis is the original nominal authored tick/after schedule. The station and both actors know this absolute plan; their radio switching does not infer private watchdog outcomes. This is an authored policy change, not evidence that the old scenario already exchanged reports.
- Each L6 owner retains its own degraded-heartbeat decision and own safe-hold timer. The tower sends an owner response only after the matching ready report is actually accepted. Landing timers use that owner's received execution-report payload plus the original 80/88-tick delay. Actor-private event tables never supply tower timer evidence.
- Source event IDs are retained as action-free narrative aggregates. Owner-local events name the exact owner; a collective label does not authorize physical dispatch.
- Pending landing successors extend the network planning horizon before command admission. The focused late-tick845 control exercises landing timers925/933 beyond the old900 horizon and verifies both actors land. This control is synthetic; the two actual native L6 representatives recover earlier under the explicit rendezvous profile. A no-receipt control stays unadmitted at its finite958-tick horizon.
- X5 approach and arrival requests are owner-local. Pad results require the exact admitted pad-owned arbitration row and both actor-to-pad request receipts accepted before arbitration. The pad sends results at58.5s after arbitration at58.0s; actual receptions are58.502690717s and58.503246845s.
- X5 station repair and pad-record expiry at65.5s are an explicit preloaded authored policy based on the original same-source seed00 schedule. Each actor independently observes its available healthy heartbeat before its local recovery action. No physical custody is inferred.
- L5 heavy-rain and recovery bundles are split into exogenous weather updates, owner-local observation admission, actor actions, and action-free narrative aggregates. For v2 heavy rain: update33.0s; sample33.1s; availability33.2s; first admission33.3s; actor action33.5s; aggregate34.0s. Recovery now updates47.5s and acts48.0s because downstream timers follow the actual aggregate. These versioned time shifts require visual recapture review.

Native ns-3 evidence here is shared-channel 802.11n UDP traffic, delivered packet rows, exact owner/epoch receipts and queued controller admission. Report payloads are modeled application records frozen at submission and bound to exact native packet RX through the immutable ledger. They are not wire-decoded PX4/MAVLink acknowledgements. Physical motion is the existing authored10Hz waypoint executor, not aerodynamic or PX4 feedback. Radio configuration remains R1:2412MHz/20MHz/HtMcs0/16dBm/NF7/log-distance n3/L0=40.09532929124565. Cochannel workload is legitimate peer UDP competition, not certified wideband jamming. Geometry/shadowing/extra fading are inactive in these representative receipts; this packet does not claim RF calibration or rain attenuation.

## Readable evidence

`source/` contains exact current adapter/provider/test bytes and the unchanged authored engine/interpreter dependencies. `frozen-source.json` is the pre-execution snapshot. `source-manifest.json` records exact copied files. `cases/` contains original and versioned event_script/scene_setup, admission decisions, action schedules, native receipts, exact accepted-message packet rows, final RF config, event-boundary poses/weather, and all-entity terminal projections. Fields in the projections retain their original values; repeated metadata is omitted. This is a compact review export, not a standalone ns-3/runtime distribution.

L6-4_v1 extends from900 to982 ticks. `all-entity-extended-suffix.jsonl` records every entity on ticks901–982, rather than certifying only the second UAV's landing. Source comparison flags such as `weather_changed` compare complete arrays and also become true when duration is extended; they do not alone establish changed weather values on the common interval.

The L6 old/new first pose difference is27.1s in these representatives. The source script schedules `multi_uav_hold_entry` at27.0s; the repaired owner-local holds execute later. The difference reflects removal/delay of that old timed move and the resulting waypoint continuation. It is not attributed to a command received at62s. First-difference rows in each summary preserve exact before/after values.

`x5-v4-missing-operands-556-557.json` preserves six exact oldv4 pose rows with JSONL line references, the original guard and admission rows. Derived3D distances are11.16472335195952m and10.849279904807318m, below the11.485m guard. With one-tick availability and strict-before use, samples556/557 become usable at558/559. It supplements the old559 decision; it is not the newv10 arbitration time.

## Verification and remaining work

24 focused tests passed in35.564s. They cover owner isolation, exact receipts, weather availability, explicit rendezvous, pending-successor horizon and pad sender authority. The existing independent Sol reviewer inspected the v10 delta and actual six-UAV terminal rows; no new defect was found in that scope. Parent/native source-chain adoption review remains outstanding. No UE collection or original210 replacement occurred.

Other seeds, remaining source groups, L2-1_v2 backup geometry, and the final210+ARM A/B/C manifest are not accepted by this packet. The five candidate A classifications are render-dependency findings for reviewed representatives; they are not a blanket recapture list for the36-scenario scope or all210 episodes.

Re-run focused checks from the current AERO_WORLD root with the existing Python environment. Native representative execution uses `python -m Dataset.semantic_simulation.ns3_episode.linked_replay --scenario <scenario> --seed <0|1|2> --output-root <new-independent-directory>`. The exporter is `tools/export_linked_authority_review.py`; it expects completed named representatives and the authorized server paths, and refuses source drift.
