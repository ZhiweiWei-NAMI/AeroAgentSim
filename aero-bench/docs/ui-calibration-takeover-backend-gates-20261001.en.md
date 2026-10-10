# UI calibration takeover: backend gates

Recorded 2026-10-01. This handoff covers the backend checks needed by the UI
calibration work. It preserves the active W1 city-inspection lane, the completed
W3 Logistics lane, and the broad native-city blockers. The only source change
in this lane is the routed traffic-preview test expectation at
`tests/test_traffic_preview_jobs.py:194`.

A read-only WorkBuddy DSH audit independently checked the current Provider
registry, compiler blockers, W1/W3 evidence, and handoff drift. Its route and
findings are recorded in
[`dsh-audit-summary.json`](../validation/ui-calibration-20261001/takeover-backend/dsh-audit-summary.json).

## Closed regression

The checked-in Huangpu traffic-preview registry now derives profile identity
`3d41eb157a5e1564d06a436763a441a89850458fa9b12ad39c2c5f3a712d8b9c`.
The declared identity equals the digest recomputed from the strict profile
document. All 31 pinned input files match their declared sizes and SHA-256
digests. The test still compares the complete public catalog exactly; only its
stale expected profile identity changed.

Fresh current-tree validation passed:

| Check | Result | Evidence |
| --- | --- | --- |
| Traffic-preview profile tests | 16 passed in 0.77 s | [`focused-traffic-preview-junit.xml`](../validation/ui-calibration-20261001/takeover-backend/focused-traffic-preview-junit.xml) |
| Generated contracts | 90 files matched | `python tools/generate_contracts.py --check` |
| Full backend suite | 2,933 passed, five skipped, zero failures or errors, two expected rejection-test warnings, 1,178.94 s | [`full-backend-junit.xml`](../validation/ui-calibration-20261001/takeover-backend/full-backend-junit.xml) |

The full JUnit inventory contains all 2,938 collected cases. No source file
under `aero_bench/`, `tests/`, `tools/`, or `containers/` changed during that
run. The five skips retain their declared external conditions: three current
container-image opt-ins, one digest-pinned SUMO Docker opt-in, and the archive
builder's clean-tree requirement. Exact results and skip reasons are in
[`validation-summary.json`](../validation/ui-calibration-20261001/takeover-backend/validation-summary.json).

## Current backend gates

| Gate | Current evidence | State and owner |
| --- | --- | --- |
| Formal `traffic.restricted` | The retained R5 SUMO proof applied one restriction, observed two lane changes, and preserved destinations across four reroutes. | Passed for its declared R5 profile; W3 evidence is complete. |
| Formal scheduled `order.created` | Logistics v7 run `316379c151fc42f6b70f089d125073408cb1081ca2bef56f5fa2e475df2f6175` passed sealed execution, an independent verifier, and public replay. It created two orders at Business barriers. | Passed for the nonphysical `logistics.arrivals.v1` profile; W3 is complete. |
| Native city inspection v8 | Run `c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba` has a passed independent verdict. Its terminal audit checked 14 sealed files and 443 indexed replay files with matching digests. Control admission and authenticated read-only HTTP delivery passed for all 443 files. | `verified_indexed_delivery_complete`; `v8-ready.txt` is published and the replay service remains running for W4. |
| Latest backend regression | The fresh suite above passes against a source-stable tree. | Closed by this lane. |
| Broad Huangpu native readiness | [`huangpu-city-native-readiness-v6.json`](../validation/codex-takeover-20261001/B/huangpu-city-native-readiness-v6.json) remains `blocked`. | Weather physics, runtime airspace enforcement, and physical Logistics remain separate backend work. |
| General editor-to-formal lowering | The compiler lowers only declared profile capabilities. Current formal event paths are `traffic.restricted` and scheduled `order.created`; unsupported fields emit blockers. | Incomplete by design. Extend it after a real Provider capability exists. |

The scheduled-arrivals proof contains no pickup, custody, flight, pad contact,
handoff, delivery, energy, or charger actuation. The UI may show order creation
and its recorded Business timing. It cannot present that evidence as physical
delivery progress.

## Capability boundary for the UI

The production Provider registry currently contains
`inspection.business`, `logistics.business`, `ns3.rpc`, `px4.gazebo`, and
`sumo.traci`. The broad native readiness record keeps these blockers:

- `provider.weather`: visual weather and authored environment values do not
  apply wind or precipitation through a registered physics Provider.
- `provider.airspace`: region contracts and detection kernels do not supply
  activation, permission, revocation, or runtime enforcement authority.
- `task.physical-logistics`: `LOGISTICS_RUNTIME_IMPLEMENTED` remains false for
  custody, pad service, charging, and physical completion.

`weather.changed`, `airspace.activated`, and `charger.outage` therefore remain
explicit compiler blockers. Changes to fleet, traffic, facilities, airspace,
algorithms, orders, generated demand, performance profiles, or authored
landscape also remain blocked unless the selected registered profile declares
and implements their lowering. Kubernetes execution remains blocked until an
actual cluster supplies the required kubeconfig, Namespace, RBAC, and
NetworkPolicy enforcement.

The frontend should preserve the source label on each view and event:
engineering preview, declared formal run, or verified replay. The offline SUMO
preview can share scene and route context with a formal run, but its recorded
motion stays engineering evidence. A provider binding in a resolved run does
not convert an offline preview into formal telemetry.

## Handoff corrections

The following older statements are now superseded:

- `W3-BACKEND/claude-routing-needed.md` routes the traffic-preview identity
  assertion as pending. The strict expectation and both focused and full
  backend reruns now pass.
- B-017-14's 2,748-pass backend result and W3's later 2,932-pass/one-failure
  result are historical. The current result is 2,933 passed and five skips.
- B-017-13's broad lowering gap remains, while two event types now have formal
  consumers: `traffic.restricted` and nonphysical scheduled `order.created`.
- The earlier W1 Markdown narrative was written while v8 was running. The
  current `report.json`, `terminal-v8.json`, `http-delivery-v8.json`, and
  `v8-ready.txt` record completed verification and indexed delivery.

## Ownership after this handoff

W1 completed the v8 Control admission, HTTP byte audit, and readiness file. Its
read-only replay service remains running at the base URL recorded in
`v8-ready.txt`. W4 now owns browser rendering. The UI lanes can use the
completed backend regression and existing contracts without changing Provider
meaning.

Weather physics, airspace enforcement, and physical Logistics each need a
separate backend assignment with a strict Provider contract, digest-pinned
workload, requested/applied evidence, sealed artifacts, and an independent
verifier. Compiler fields should become editable only with those capabilities.
These additions exceed the current UI calibration scope.
