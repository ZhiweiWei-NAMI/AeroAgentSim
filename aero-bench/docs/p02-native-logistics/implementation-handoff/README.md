# P02 original-worker implementation handoff

This is a source snapshot for native review, captured from the existing isolated worktree. It does not enable logistics, start a flight, publish a new verifier, or accept native parcel execution. The base is `65280711ed486b0a8ac12c3f0ffc122141b4c5c0`; the remote previously lacked these uncommitted modules.

| Original worker | Actual status | Saved modules / evidence |
| --- | --- | --- |
| Catalog/reconnect — `a400e686-3a13-4b1e-950c-f443091ea395` | Ordinary Flash public result completed. Coordinator verified 61 backend tests; previous coordinator frontend check passed 49 tests and TypeScript. | `aero_bench/control/{contracts,manager}.py`, `frontend/src/{run-start-identity,run-store}.ts`, generated contracts, `tests/test_control_start_discovery.py`; exact patch and test record included. |
| Native parcel — `c17d3614-e676-4394-acc4-32391809e123` | Author document says module-complete / integration-deferred. PID1334642 was still active and no final public result existed at capture. Same-session closeout/release requested. Coordinator independently ran 37 module tests, all passed in 0.64s. | `aero_bench/tasks/logistics/native_parcel_contract.py`, `native_parcel_runtime.py`, `tests/tasks/test_native_parcel_runtime.py`, `validation/p02_native_parcel_glm/implementation.md`; exact saved bytes included. |

Both original tasks use `workbuddy/glm-5.3-flash`. No duplicate worker was launched. The native parcel worker still owns its four assigned files; this packet copies their saved bytes without changing those files. `status.json` records paths, hashes, save times and scope.

| Integration component | Implemented | Concrete remainder |
| --- | --- | --- |
| Transition RPC | Typed admitted-action input and state-machine transition decisions | Actual command/RX construction, transition RPC journal and hook feed. Current business provider still refuses physical pickup/handoff/delivery. |
| Current-tick dwell | Pure-module contiguous closed samples; gap/motion/contact loss and foreign binding refusal; admission bound to closing barrier | Per-run factory and existing `LogisticsRuntimeHook` wiring. Synthetic tests do not establish native dwell. |
| Backend parcel projection | `parcel_carriage_pose` full-attitude/body-local math and snapshots | Publish derived parcel entity/state through the actual SceneState/public trace and shared UI selector. |
| Dedicated sealed verifier / entrypoint | Required replay contract is declared | Independent parcel verifier, task entrypoint, immutable new compilation and logistics image pins. Inspection15/15 is preserved and does not certify logistics. |
| Catalog UI wiring | Authenticated exact-run non-secret start identity and RunSession import/idempotent Start | User-facing discovery/reconnect affordance and live reload acceptance. No authentication expansion. |

The signed pose-reference change was separately reviewed by the existing catalog session. Its seven focused checks passed; coordinator's related suite passed 96 tests in7.798s. That review excludes the parcel state machine and native execution. Calibration is model/provider-specific, not a universal offset; no presence tolerance was relaxed.

Next implementation remains narrowly defined by the five rows above. A new logistics compilation must use the corrected verifier requirement before the one authorized logistics run. The preserved inspection compilation, failed original evaluation and successful same-seal reevaluation remain separate.
