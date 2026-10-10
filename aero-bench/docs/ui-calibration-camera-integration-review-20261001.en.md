# Observation camera integration review

Date: 2026-10-01

## Scope and evidence

This review covers the camera integration in `frontend/src/map.ts` and the scene-clock handoff in `frontend/src/app.ts`. Those files were inspected without modification. The transform implementation and its sensor adapter are documented in `docs/ui-calibration-camera-20261001.en.md`.

The review used the current source, the resolved inspection releases, the X500 viewer asset, and focused Vitest runs. It does not treat the client-rendered scene as a Gazebo frame, live stream, or recorded sensor frame.

## Current integration

| Concern | Current behavior | Review result |
| --- | --- | --- |
| Main camera pose | `updateCamera()` reads `getWorldPosition()` and `getWorldQuaternion()` after the current `SceneState` has updated the vehicle node. It then applies the rigid mount. | Correct basis for parented scene nodes. Add an integration test with a translated and rotated parent. |
| Formal mount | `declaredSensorMount()` derives the body-relative mount from the resolved parent and sensor world poses. It does not use mesh bounds or the authored body-axis assumption. | Correct separation from presentation geometry. |
| Authored preview | An undeclared engineering preview places a rigid display camera 0.15 m beyond the visible mesh's local +X bound. The source label calls it an authored display camera and an undeclared sensor. | Honest presentation behavior. It must remain separate from formal sensor capability. |
| Replay time | `currentMapScene()` supplies the selected replay `SceneState`; vehicle pose, telemetry, routes, events, footprint, and `secondaryCameraTimeS` read that scene or its simulation time. Replay state changes call `renderMap()`. | The data path is consistent. The current tests check the clock helper, but do not prove an end-to-end pose and preview stay on the same tick during reverse seek. |
| Secondary view | One shared renderer draws to one 320 x 180 `WebGLRenderTarget`. Normal preview and minimap updates are limited to one start per 200 ms; deliberate interactions bypass the limit. | Meets the design target. Browser timing remains unmeasured, and synchronous pixel readback must be included in the measurement. |
| Horizontal image convention | The formal mount keeps a proper quaternion and records `horizontalMirror`. The main canvas and preview canvas mirror the completed image, the footprint mirrors its rays, and scene picking reverses pointer X. | Correct approach for the formal left-handed image-axis mapping. Browser evidence must confirm winding, text-independent orientation, pointer selection, and mirror clearing. |
| Return to overview | Entering a non-free mode stores the free camera and OrbitControls target. Returning to free restores position, orientation, target, projection defaults, and a non-mirrored surface without clearing selection. | Correct for the free-overview to onboard path. The chase-to-onboard swap does not preserve chase as the demoted view. |
| Source labeling | A formal client render is labeled `simulated RGB`, `declared sensor pose`, and `not a recorded frame`. The authored mount is labeled as undeclared. | Correct. No provider frame or gimbal state is inferred. |

## Actual inspection mount

`releases/urban-infrastructure-inspection-v1/world/package.json` declares `camera.inspection.front` on `uav.inspector` at parent offset `(x=0.12, y=0.03, z=0.242)` m, identity orientation, 80 degree horizontal FOV, 60 degree vertical FOV, and 640 x 480 resolution. Both inspected resolved runs contain the same vehicle and sensor world poses:

- vehicle ENU: `(-260.0, -310.0, 0.137)` m
- sensor ENU: `(-259.88, -309.97, 0.379)` m
- derived renderer-local offset: `(forward=0.12, up=0.242, right=-0.03)` m

The formal optical convention is +X forward, +Y image-right, and +Z image-up. Three.js uses +X right, +Y up, and -Z forward. The adapter uses a proper rotation for forward and up, then a final horizontal image reflection. The authored display mount instead follows the viewer mesh's local +X presentation axis. The two mounts must not share offsets or capability labels.

The X500 presentation spec fits the model to `1.0 x 0.35 x 1.0` m and centers it. The source GLB position accessors span approximately `[-1.55, -0.793, -1.55]` through `[1.55, 0.793, 1.55]`, so the fitted vertical envelope is `-0.175` through `0.175` m. The declared sensor origin is 0.067 m above that envelope. This geometric check shows that the origin is outside the fitted mesh; it does not replace a browser image check for intentional rotor or fuselage visibility near the bottom of the image.

The declared 80 by 60 degree FOV does not exactly match the 4:3 resolution under a pinhole model. The adapter takes a centered crop inside both limits. The calculated display FOV is 75.18 by 60.00 degrees at 4:3 and 80.00 by 50.53 degrees at 16:9. Near 0.05 m and far 14,000 m are viewer clipping settings, not sensor range evidence.

## Gate findings

### 1. Do not retain a stale onboard pose when the target becomes unavailable

`updateCamera()` returns when the selected node is absent or invisible. If the map is already in cockpit mode, the main camera keeps its previous valid pose and renders the new scene from that stale pose. Selecting a non-aircraft object, losing the vehicle sample within the same scenario, or hiding the selected aircraft can trigger this path. The camera state also remains whatever the last successful frame wrote unless another branch replaces it.

The integration must either leave cockpit visibly or cover the main image with an unavailable state. It must clear the prior sensor identifier and source state. It must not present a newly rendered scene from the last onboard pose as the current aircraft view.

Required test: enter cockpit on a valid aircraft, remove or hide its current sample without changing scenario identity, render again, and assert that the stale onboard image is not presented as ready. Repeat after selecting an event or ground object.

### 2. Derive live clock state from the live control state

The replay label correctly follows `ReplayState.isPlaying()`. The live path currently reports `playing` whenever the session has at least one historical `SceneState`. A paused, terminal, or disconnected provider session can therefore appear to be advancing.

The live label should derive from the active runtime/observation clock contract. If the interface cannot prove that time is advancing, it should not infer motion from the existence of history. A visualization pause must remain distinct from a provider pause or stop.

Required test: retain a non-empty state history while moving the runtime through running, paused, terminal, and disconnected states; assert the displayed clock label for each supported state.

### 3. Keep both camera controls synchronized with the accepted mode

The operations monitor clears its optimistic mode on the next authoritative snapshot. The telemetry HUD uses its own select value. If cockpit is rejected because no camera is available, or if the operations monitor changes the mode, the HUD can retain a different value. A telemetry-HUD mode change also requests an ordinary map render; if the monitor's 200 ms cadence suppresses that snapshot while replay is paused, the monitor can retain its old active button until another render occurs.

Use the map's accepted mode as the shared value after every request and force one observation snapshot for a deliberate mode change. A rejected request should return or publish the retained mode.

Required test: request cockpit without a selected camera, then assert that the map, operations buttons, telemetry select, canvas mirror, and source label all remain in the same mode. Repeat successful free, chase, and cockpit transitions from both controls.

### 4. Avoid duplicate forced preview readbacks on selection

`selectObservationTarget()` calls the application selection callback and then forces an observation render. The application selection subscriber also renders the map synchronously. With preview enabled, one click can therefore run two forced 320 x 180 renders and two synchronous `readRenderTargetPixels()` calls.

Choose one owner for the post-selection render. The immediate visual result must remain, but one selection should produce one forced preview readback.

Required test: enable the preview, select one vehicle through the monitor and through the 3D scene, and count secondary render/readback calls for each completed click.

## Required acceptance coverage

The pure transform tests cover translation, yaw, roll, mount and gimbal quaternion order, projection validation, mirror direction, and plane-footprint outcomes. `map.observation.test.ts` now covers selected-aircraft gating, an authored mount moving with a root vehicle, free-view restoration, navigation without vehicle motion, and the recorded-time helper. The integration gate still needs these checks:

1. Put a UAV under a translated and rotated parent, advance its current state, and compare the main and preview camera poses with `mountedCameraPose()`.
2. Seek replay forward and backward, step while paused, change speed, reset, and assert that entity pose, camera pose, telemetry time, event cutoff, footprint, and preview timestamp all use one selected tick.
3. Verify ordinary preview renders start no more than 5 Hz, forced interactions render immediately, the 320 x 180 target is reused, and `destroy()` disposes it.
4. Keep the free to cockpit to free restoration test. Add chase to cockpit to return; either preserve chase as the secondary view or rename the action to match its free-overview behavior.
5. Verify the formal image's left/right direction, front-face visibility, pointer selection, and non-mirrored free/chase restoration in a real browser.
6. Verify missing sensor, missing current sample, hidden vehicle, scene replacement, loading, and disconnect states without reusing a prior pose or source label.
7. Measure main render, minimap update, secondary render, and readback separately on declared hardware and fleet size. The `dataset` budget strings are implementation targets; only captured timings are measurements.

The current footprint integration passes only a complete four-corner polygon to the minimap. The pure helper preserves `parallel`, `behind`, and `clipped` ray states, but the monitor cannot yet show a partial or horizon-crossing footprint. That contract extension can follow the stale-pose and clock fixes. The current reference plane is renderer `y=0`; non-flat terrain needs an explicit plane or terrain intersection before the footprint can claim ground coverage.

## Validation snapshot

The following focused command passed 60 tests in six files on 2026-10-01:

```text
npm test -- --run src/map.test.ts src/app.replay-lifecycle.test.ts \
  src/observation-camera.test.ts src/observation-camera-sensors.test.ts \
  src/operations-monitor.test.ts src/operations-data.test.ts
```

The camera integration command then passed 22 tests in three files, including the five new map-level cases:

```text
npm test -- --run src/map.observation.test.ts \
  src/observation-camera.test.ts src/observation-camera-sensors.test.ts
```

Together the two commands cover 65 distinct tests in seven files. `npx tsc --noEmit --pretty false` also passed after the map observation fixture was completed.

Browser timing and the final formal-camera screenshot remain pending. They should use the resolved inspection scenario, select `uav.inspector`, promote `camera.inspection.front`, seek backward while paused, locate a linked event, and return to the saved overview without changing the selected object.
