# UI calibration browser acceptance

Snapshot: 2026-10-01 10:07 PDT. This report covers the integrated source
frontend, the authored preview, and the registered sealed replay. It records
browser behavior from the current working tree and does not treat a failed
formal source as authored or mock data.

## Result

The integrated monitor works end to end for selection, external follow, rigid
onboard inspection, camera-preview exchange, minimap navigation, replay
controls, and return to overview. The formal workflow ran against sealed run
`c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba`
on the hardware renderer reported by WebGL. All eight recorded frontend source
digests still match the files in the working tree.

The browser receipt does not close three acceptance items:

- The sealed run contains no actionable event with declared severity,
  location, and affected object. The interface correctly shows unknown values,
  so an exception-to-incident-location sequence cannot be claimed from this
  source.
- The expanded camera preview covers part of the event strip at both measured
  desktop sizes. The overlap is 34,549 px² at 1600×900 and 12,894 px² at
  1280×720.
- The registered replay exposes only a disabled source-selector placeholder.
  Source-change selection retention was therefore not exercised in this
  browser run.

The raw workflow records 22 actions. Twenty-one have a clean harness result.
The telemetry-minimize action clicked the visible control and produced the
minimized `姿态 PFD · uav.inspector 展开` bar, but its old assertion queried a
hidden duplicate node and timed out. The next 18 actions completed. The raw
receipt is preserved unchanged; the reusable script now checks visible nodes
and passes `node --check`.

## Baseline and additive delta

The initial 08:10 PDT source capture loaded the authored city at `/` in
SwiftShader. It contained the existing entity tree, formal-control panel,
telemetry HUD, replay transport, and 3D scene. It had no operations-monitor,
global-minimap, rigid-onboard, or camera-source elements. Those functional
captures remain in `validation/ui-calibration-20261001/browser-baseline/` and
carry no hardware-performance claim.

The final integration reuses the existing map, selection, replay, scene, and
Control paths. The browser demonstrated these additions:

| Addition | Browser evidence |
| --- | --- |
| Source and clock header | Authored preview reads `工程记录 / 作者展示 · 非正式执行`; the formal source reads `记录回放 c69f303f · 只读观察`. The clock changes with the same replay playhead. |
| Compact fleet and context | The formal monitor exposes 525 objects: 1 UAV, 72 ground vehicles, 36 people, and 416 static assets. Selecting `uav.inspector` updates the tree, detail panel, 3D selection, and minimap marker. |
| Explicit camera modes | Free overview, external follow, and rigid onboard are selectable. Gimbal onboard remains disabled. The selected source reads `相机来源：模拟 RGB · 就绪`. |
| Camera view exchange | The selected UAV's rigid onboard image becomes the main view. Opening the 320×180 preview and using `交换主画面` exchanges overview and sensor views while retaining `uav.inspector` and replay index 100. |
| Spatial navigation | The SVG minimap shows north, a 200 m scale, legend, 525 object markers, 109 routes, and two polygons. Empty-map navigation visibly changes cockpit to free inspection. Drag, click, Home, and arrow-key actions retain selection. |
| Replay inspection | Seek, one-step, 2× play, visual pause, reset to index 0, restore to index 100, and return to overview all completed. |

The authored preview also passed UAV selection, rigid onboard entry, fleet
collapse at 1280×720, and return to overview. Its source remained explicitly
labelled as an engineering preview throughout.

## Formal source and transport

The browser entered through the live catalog at
`http://127.0.0.1:5403/?run=c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba`,
selected the requested registered run, then invoked its read-only replay-access
endpoint. Bootstrap values were read from the authorized service process only;
the credential fields cleared after catalog admission, and no credential value
appears in the JSON or log.

The trace contains 298 scene states and 918 public events. Native loading
reached 418/418 after three rate-limit boundaries. Network evidence records
2,032 HTTP 200 responses and twelve HTTP 429 asset responses. The bounded asset
retry resumed after each boundary and reached `轨迹与城市场景已就绪`; no alternate
scene source was selected. The only non-GET request was the expected
`POST /v1/runs/<run-id>/replay-access`. Every recorded observation, camera,
minimap, and replay action emitted zero non-GET requests.

The earlier `c073...` attempt remains as separate evidence of an incompatible
native route. It is not evidence for this successful v8 load.

## Operator workflow

At replay index 100, the event strip contained 303 records across
`public.traffic-light`, `public.network-link`, and `public.status`. Selecting
`event.0000000000054507` opened its evidence digests and displayed unknown
severity, location, affected object, task, order, facility, acknowledgement,
and resolution. Those unknowns match the sealed record. Across the full run,
no event declares severity or a location label; only four
`public.sensor-frame` events carry an object relationship.

The workflow then selected `uav.inspector`. The context panel showed the
declared HOLD activity, ENU position, 11.2 m AGL altitude, 89.8° heading,
1.52 m/s speed, 50% battery, healthy state, replay-synchronized freshness, and
00:00:50 sample time. Connectivity, payload, destination, task, and order
remain unknown because the public sample does not supply them.

External follow moved the minimap observer to `(177.72, 321.02)` beside the
selected marker at `(178.85, 319.88)`. Rigid onboard moved it to
`(178.88, 319.87)`. An empty-map click moved the free observer to `(64, 40)`
without clearing the UAV. Returning to cockpit restored `(178.88, 319.87)`.
This confirms that chase and onboard indicators follow the active camera or
aircraft instead of a saved overview target.

The selected formal pose does not yield a complete ground footprint. The
minimap states `视锥足迹不可用` and keeps the draggable observation-centre marker
available. It does not invent a footprint or move the aircraft. A later seek
shows positioned sensor-frame events, but those records are routine sensor
evidence rather than declared exceptions.

## Camera and replay behavior

The rigid onboard view is visually distinct from an orbit view near the UAV.
The formal screenshot uses the declared simulated RGB source and places the
camera against the building facade at the selected vehicle pose. The secondary
canvas is labelled `模拟 RGB 相机预览`. When the main view is onboard, the preview
contains overview; after exchange, the main view is free overview and the
preview identifies the declared sensor pose as a non-recorded simulated frame.

Selection survived camera exchange, minimap navigation, seek to index 140,
single-step to 141, 2× playback, reset to index 0, restore to index 100, viewport
resize, onboard re-entry, and final return to overview. Visual pause is shown as
a display-clock state; the receipt makes no claim that an executing system was
stopped.

## Desktop layout

Both viewports have matching document client and scroll dimensions, so the page
does not introduce document-level horizontal or vertical overflow. At 1600×900,
the four monitor wrappers do not overlap. At 1280×720, the header and sidebar
intersect by 646 px². The selected context and camera controls remain usable.

The preview canvas visibly overflows the sidebar wrapper into the expanded
event strip. This occurs at both viewports and is missing from the wrapper-only
overlap probe. The final captures therefore document the defect instead of
classifying the responsive layout as complete. The minimized legacy telemetry
bar remains visible at the lower right without blocking the camera controls.

## Measured performance

The browser used Chromium with `--enable-gpu --use-gl=angle
--use-angle=gl-egl --disable-software-rasterizer --ignore-gpu-blocklist`.
WebGL reported `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3090/PCIe/SSE2,
OpenGL ES 3.2)` and the map reported `renderBackend=hardware`. The host has six
RTX 3090 GPUs with driver 535.183.01. GPU 0 was 85% busy immediately before the
run and 90% busy three minutes after it because of unrelated work.

| State | Main submit | Draw calls | Triangles | Minimap update | 20-sample rAF interval |
| --- | ---: | ---: | ---: | ---: | ---: |
| Formal overview, paused, 1600×900 | 20.4 ms | 1,216 | 2,481,009 | 73.5 ms | mean 16.66 ms; p95 16.70 ms |
| Formal rigid onboard, paused, 1600×900 | 47.3 ms | 1,155 | 2,480,276 | 130.3 ms | mean 16.67 ms; p95 16.80 ms |
| Formal 2× playback, 1600×900 | 15.8 ms | sampled separately | sampled separately | 128.8 ms | mean 284.16 ms; p95 616.70 ms |
| Formal rigid onboard, paused, 1280×720 | 18.8 ms | 1,155 | 2,480,276 | 113.8 ms | mean 16.67 ms; p95 16.80 ms |

The secondary camera uses the shared renderer at 320×180 with a maximum 5 Hz
update rate. Its CPU wall time, including readback, was 510.3 ms on the first
visible sample, 34.2 ms after the view exchange, and 16.5 ms during the sampled
2× playback state. The minimap declares the same 5 Hz ceiling during playback.
These are point measurements under concurrent load. The 2× rAF stalls and the
first secondary-camera sample require a quieter performance run before setting
or accepting a frame budget.

## Browser diagnostics

There were no page exceptions. Console output contained the twelve recovered
429 responses, Three.js shadow-map deprecation warnings, Z-up FBX conversion
warnings, and Chromium's warning about in-memory password fields outside a
form. The formal scene ended ready. The map's older `sceneLoadStage` string
still reads `等待官方场景网格发布` after the loading-progress component reports
ready; the native presentation fields and screenshots show the loaded declared
scene.

The exact reusable selectors are recorded in `final-workflow.json`. The main
ones are:

```text
#city-map
.operations-monitor
.operations-monitor-fleet-row
[data-camera-mode]
[data-role='camera-preview-slot']
.operations-monitor-map
.operations-monitor-map-background
.operations-monitor-map-observer
.operations-monitor-map-footprint
.operations-monitor-map-event
.operations-monitor-event-main
.scrubber
.transport
```

Catalog admission uses `.control-panel .credential-input`, `.run-select`, and
`[data-role='open-registered-replay']`. The current registered-replay source
selector is visible but disabled with only `选择工作区公开轨迹`; no source switch is
claimed.

## Evidence

- Machine-readable acceptance summary:
  `validation/ui-calibration-20261001/browser-baseline/final/acceptance-summary.json`
- Raw browser state, actions, network records, hashes, and screenshot manifest:
  `validation/ui-calibration-20261001/browser-baseline/final/final-workflow.json`
- Timestamped execution log:
  `validation/ui-calibration-20261001/browser-baseline/final/inspect-final-workflow.log`
- Reusable Playwright workflow:
  `validation/ui-calibration-20261001/browser-baseline/inspect-final-workflow.mjs`
- Initial functional baseline:
  `validation/ui-calibration-20261001/browser-baseline/baseline.json`
- First terminal rate-limit diagnosis:
  `validation/ui-calibration-20261001/browser-baseline/final/transport-diagnostic.json`
- Hardware load samples:
  `validation/ui-calibration-20261001/browser-baseline/final/hardware-inventory.csv`
  and `hardware-inventory-after-final.csv`

Representative captures are
`replay-exception-selected-1600x900.png`,
`replay-declared-onboard-1600x900.png`,
`replay-onboard-with-preview-1600x900.png`,
`replay-camera-swapped-1600x900.png`,
`replay-return-overview-1600x900.png`,
`replay-declared-onboard-1280x720.png`, and
`replay-final-overview-1280x720.png`. Every final capture was inspected visually.

The source Vite servers remain available at ports 5400 and 5403 under the PID
files in the evidence directory. The read-only Control service remains at port
5393. No service was published or reconfigured for this browser pass.

## Remaining browser work

The next browser increment should keep the current state and address the
measured gaps in this order:

1. Contain the expanded preview within the available sidebar height or reserve
   space so it cannot cover the event strip, then recapture both desktop sizes.
2. Use a sealed fixture with a declared actionable event, location, affected
   object, and logistics relationships to complete the requested exception
   sequence. Missing contract fields must remain unknown.
3. Load two valid public sources in one session and exercise source-change
   selection retention. The current disabled selector cannot supply this test.
4. Exercise stale, disconnected, and missing-sensor transitions in a browser
   source that declares those states. The current run supplies a ready simulated
   RGB sensor and unknown connectivity without a transition.
5. Repeat the performance sample under declared quiescent conditions. Preserve
   separate main-render, minimap, and secondary-camera measurements.

Wallboard presentation and replay comparison remain proposed additions. They
are absent from this browser receipt.
