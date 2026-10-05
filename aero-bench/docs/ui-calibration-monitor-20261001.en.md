# Compact operations monitor delta

Date: 2026-10-01  
Owner: Codex monitor worker  
Status: reusable frontend module and map adapter are present in the working tree; integrated browser acceptance is pending

## Baseline reused

The viewer already has one shared `TraceTarget` selection model, a scene renderer, free/chase/cockpit camera modes, public trace time, and interface tokens in `styles.css`. The monitor reuses those contracts. It does not add a selection store, command channel, map renderer, or operational-data fallback.

The new module is split into:

| File | Responsibility |
| --- | --- |
| `frontend/src/operations-monitor.ts` | Stable snapshot API, keyed DOM updates, camera/view actions, event strip, object details, and SVG minimap interactions |
| `frontend/src/operations-monitor.css` | Compact overlay layout, existing viewer-token use, visible focus, status labels, and responsive desktop layouts |
| `frontend/src/operations-monitor.test.ts` | Selection, camera, minimap gesture, stale/missing data, event linkage, DOM stability, and disposal checks |

## Integration contract

Mount once with `mountOperationsMonitor(host, callbacks)` and call `handle.update(snapshot)` as time or data changes. The returned `previewContainer` is stable. The scene owner may place a canvas or video in it; monitor updates do not replace that child.

The snapshot carries source identity and label, simulation time and clock state, the existing selected `TraceTarget`, spatial objects, optional routes and polygons, events, and observation-camera state. Objects expose supported operational values through typed telemetry and optional facts. Missing values render as `未知`. Events without a declared source severity use `severity: "unknown"` and render as `等级未知`; the adapter must not promote them to informational, warning, or critical.

All operational objects select `{ kind: "entity", id }`, including facilities, matching current scene picking. Events select `{ kind: "event", id }`. The monitor never issues vehicle, mission, dispatch, or gimbal commands. Its callbacks are limited to shared selection, observation-camera navigation, observation mode, preview visibility, and view exchange.

Coordinates use renderer-local metres: `x` points east, `y` points up, and `z` points south. The minimap places north (`-z`) at the top. `headingRad` is clockwise from north. Adapters must convert source yaw before populating this field when the source uses another convention.

## Implemented interactions

- The compact header identifies the source, simulation clock, and visual clock state. `画面暂停` describes only the visualization clock.
- The fleet/task disclosure, detail panel, 3D selection, minimap markers, and event rows use the same selected target.
- The fleet viewport is capped at `min(25vh, 180px)` so contextual details and camera actions remain reachable at 720 px desktop height. UAVs appear first, a selected non-UAV follows them, and remaining ground vehicles, people, and facilities have distinct group labels.
- Activity, connectivity, health, and data freshness remain separate fields. Standard measurements and source-specific facts preserve unknown values.
- Camera controls distinguish global overview, external follow, and a supported rigid onboard source. The gimbal control is present and disabled because no gimbal-control contract is supplied.
- The camera source is labelled simulated RGB, live video, recorded video, unavailable, or unknown. Loading, stale, disconnected, and unavailable states remain distinct.
- The SVG minimap draws routes, restricted/facility/operating polygons, objects, events, and the observation footprint without creating a Three.js renderer. It includes north, a metre scale, a compact legend, and fit-to-area control.
- An empty first snapshot is fitted again when that source supplies its first scene object, route, polygon, or located event. Later clock updates preserve the fitted bounds until the source changes or the operator uses fit-to-area.
- The observation centre and camera direction remain visible and draggable when the view rays do not form a complete ground footprint. The map labels that case `视锥足迹不可用`; it does not invent horizon intersections.
- Empty-map click and keyboard arrows navigate the observation camera. Dragging the observation footprint pans that camera. A cockpit view first changes visibly to free inspection. Pointer movement must cross six CSS pixels before it becomes a drag, and `pointercancel` clears the gesture.
- Marker and event actions work from the keyboard. Labels are decluttered when more than twelve objects are present; the selected object remains labelled.
- Events count a condition as active only when the source explicitly supplies `resolved: false`. Missing resolution renders as `解决状态未知`. Acknowledgement and resolution stay independent.
- The live event strip renders twelve prioritized groups, then loads fifty more per deliberate action. The minimap renders at most eighty prioritized located events. An explicit `groupKey` collapses repetitions only when condition, severity, resolution class, and affected objects also match; the expanded row retains occurrence IDs, times, links, and evidence.
- The event strip starts collapsed. User expansion and fleet disclosure state persist across clock updates because the monitor patches keyed content without remounting either disclosure.

## Verification

Measured module checks on 2026-10-01:

```text
npx vitest run src/operations-monitor.test.ts src/operations-data.test.ts
Test Files  2 passed (2)
Tests       25 passed (25)
```

An isolated strict TypeScript check of the module and shared target type also passed, followed by the full frontend typecheck. The checks cover stable keyed nodes and focus across clock updates, entity selection for facilities, keyboard markers, camera capability gating, preview-slot preservation, empty-map navigation, drag-threshold suppression, cockpit exit, pointer cancellation, first-data fitting, missing footprints, bounded event rendering, source-declared repetition groups, event-to-location linkage, honest disconnected and missing-sensor states, invalid coordinates, duplicate IDs, and disposal.

## Dependencies and remaining acceptance

The root integration worker owns snapshot adaptation and mounting in `map.ts`/`app.ts`. It must keep the existing selection store authoritative, provide source labels from real run/preview state, convert headings to the declared north-clockwise convention, and throttle monitor updates. The current budget is a 5 Hz target for DOM/SVG updates. It is not a measured rate. Secondary camera rendering needs a separate measured budget and must reuse the existing renderer path rather than create a minimap renderer.

Browser acceptance still needs the integrated workflow: identify an exception, select its aircraft, open the rigid onboard view, locate the incident on the minimap, inspect mission/order/facility context, and return to overview without losing selection. That pass must also cover common desktop sizes, browser focus order, scene changes, pause/step/seek/reset, stale and disconnected sources, and proof that observation gestures emit no flight commands. Wallboard presentation and replay comparison remain later additions because this module does not supply their data or layout authority.
