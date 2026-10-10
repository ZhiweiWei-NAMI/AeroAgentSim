# UI calibration and project-owner delivery

This work continues the existing city-platform plan. Existing implementation,
scene identities, Provider capabilities and review gates remain in force.

## Implemented delta

| Increment | Implementation | Evidence and limits |
| --- | --- | --- |
| Shared observation | Existing selection drives fleet, scene, minimap, details and events. Camera intent remains separate from Control commands. Click and drag have separate completion paths. | Map integration tests; browser workflow is the final consumer gate. |
| Genuine onboard rendering | Rigid camera pose composes current vehicle pose and declared initial mount. Projection stays within declared FOV; the formal image-axis reflection is explicit. Author previews use a separately labeled display mount. | 17 camera tests; model-clearance review. Gimbal controls remain unavailable without public gimbal state. |
| Linked secondary view | Preview and main onboard view exchange while retaining selection and clock. The secondary pass uses one 320×180 target in the existing renderer. | Maximum 5 Hz during playback is a budget, not a measured FPS result. Separate main submission, secondary readback and minimap timing fields are exposed. |
| Spatial minimap | SVG markers, heading, routes, facilities, regions, events, north, scale, legend, fit, keyboard navigation and draggable observation indicator. | Complete footprints are projections onto renderer y=0. Horizon crossings remain explicit; terrain and occlusion coverage are not claimed. |
| Operational context | Declared telemetry, sample age, health, activity, connectivity, task/order/facility links and exact-event-tick locations. Missing values remain unknown. | Public data adapter tests. Events without declared severity or resolution retain those unknown states. Command acceptance and waypoint arrival never imply completion. |
| Event density | Prioritized, paged event strip; explicit repeated-condition grouping; bounded map event markers. Fleet list has its own scroll and retains room for selected-object actions. | Monitor tests and visual review at desktop sizes. Raw event totals and grouped occurrence evidence remain available. |
| Source failure | Public samples remain inspectable when presentation assets fail. Camera imagery stays unavailable; there is no replacement formal source. | An older incompatible scene publication was preserved as a failed browser attempt. |

The reference study visually inspected DJI FlightHub 2, QGroundControl, Mission
Planner, Ignition, AVEVA and Siemens interfaces. The adopted patterns are
context-preserving map/video exchange, explicit source states, compact object
context and labeled exceptions. See [the reference report](ui-calibration-references-20261001.en.md).

## Project-owner takeover

W1d and W3d retained their backend scopes until completion. The strict traffic
profile test expectation now matches the verified current profile. Fresh backend
validation passed 2,933 tests, with five explicit skips and zero failures.
Generated contracts matched all 90 files.

Formal native v8 Run
`c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba`
passed the independent verifier's 15 declared goals, all 14 sealed artifacts,
and digest-checked HTTP delivery of all 443 indexed replay files. The separate
public-boundary audit checked 919,308,210 bytes. This result does not unblock
weather physics, airspace enforcement or physical Logistics/charging.

The read-only Control service remains available on port 5393. Temporary Vite
servers on 5400/5403 have stopped; their earlier PID files are historical.

## Validation receipts

- [Backend summary](../validation/ui-calibration-20261001/takeover-backend/validation-summary.json)
- [Formal delivery and public boundary](../validation/ui-calibration-20261001/takeover-formal/report.md)
- [Frontend suite](../validation/ui-calibration-20261001/frontend-accepted-tests.log)
- [Private production build](../validation/ui-calibration-20261001/frontend-accepted-build.log)
- [Camera integration review](ui-calibration-camera-integration-review-20261001.en.md)
- [Source/data review](ui-calibration-data-20261001.en.md)
- [Authenticated replay loading correction](ui-calibration-replay-gate-addendum-20261002.en.md)

The final browser receipt is complete. Process and test fixtures are not formal
execution evidence. The browser result is partial acceptance, with the remaining
gates recorded below.

The registered replay browser probe exposed a terminal HTTP 429 after 112
declared asset responses. Control returned no `Retry-After`. The viewer now
retries only read-only content-addressed asset 429 responses, with cancellation,
server-directed delay or a 60-second default, and four retries. Authentication,
manifest, trace, digest and contract failures retain their existing error paths.
The actual service limit and sealed run are unchanged. The failed attempt and
transport receipt remain available for review.

Two additional observation regressions were corrected: a source change retains
the application's selection even when its object reference is unchanged, and
the onboard/external minimap observer uses the current camera position. Free
inspection continues to display its orbit target. Focused tests cover both.

## Final browser and whole-project checkpoint

The real v8 city reached ready after three asset retry windows. The browser
recorded 22 actions and 15 screenshots at both requested desktop sizes. Selection
survived camera exchange, minimap navigation, seek, stepping, pause, reset and
return. All observation actions issued zero non-GET requests. Eight captured
source hashes still match the current files.

Full UI acceptance remains partial: the expanded preview overlaps the event
strip at both sizes; the narrow header and sidebar overlap; a street-view source
note overlaps the minimap legend. This sealed run has no declared actionable
incident severity/location or UAV logistics context, and its disabled selector
cannot exercise two-source transitions. The raw harness contains one
telemetry-minimize assertion timeout against a hidden duplicate; subsequent
actions completed and the helper is corrected. See [browser acceptance](ui-calibration-browser-baseline-20261001.en.md)
and [the receipt](../validation/ui-calibration-20261001/browser-baseline/final/acceptance-summary.json).

Hardware samples came from RTX 3090 under concurrent host load. They expose
expensive SVG updates and secondary readback; they do not establish a desktop
frame budget. The independent model-assisted review completed with its
snapshot limits stated in the project session record.

Claude's later final verification supersedes the earlier counts: 1,220 frontend
tests, 2,933 backend passes and five skips, 90 matching contracts, typecheck,
build, script tests, sealed-run rechecks and GPU gates. It preserves visible
provenance chips and corrects a capture-test cwd assumption. See
[plan section 8.1](city-platform-implementation-plan.en.md#81-final-verification-by-claude-2026-10-01-1145-pdt).
The current continuation sequence is in the project-owner handoff record
(session record, omitted from this export).

## Remaining capability proposals

Recorded/live-video switching needs an actual supported video source. Public
sensor-frame references do not currently provide a direct sensor association;
a frame-to-sensor contract proposal is separate from this UI change. Public
gimbal state, terrain-aware/occlusion-aware footprints, richer measured facility
occupancy/charging and physical logistics are also separate dependencies.
Optional comparison and wallboard layouts can reuse this selection and snapshot
boundary after the supported inputs and presentation acceptance are established.
