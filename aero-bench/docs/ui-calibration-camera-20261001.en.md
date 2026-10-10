# Observation camera transform contract

`frontend/src/observation-camera.ts` resolves a perspective camera from one vehicle pose and one declared camera mount. It contains no renderer loop, clock, selection state, or provider fallback.

## Frames and units

All positions and distances use metres. The caller converts ENU data into renderer coordinates before calling this module:

- renderer world: +X east, +Y up, +Z south
- vehicle body: +X forward, +Y up, +Z right
- Three.js optical frame: +X right, +Y up, -Z forward
- quaternion storage: `[x, y, z, w]`

Quaternions use the Three.js local-to-parent convention. The resolved optical rotation is:

```text
Q(world <- optical) = Q(world <- body)
                      * Q(body <- mount)
                      * Q(mount <- optical)
```

The last term exists only for a mount declared with `kind: "gimbal"`. A gimbal mount without its current orientation is an error. Supplying a gimbal orientation to a rigid mount is also an error. The module does not infer stabilization from the camera mode or aircraft type.

`horizontalMirror` is part of the mount contract. Most cameras set it to `false`. The formal inspection camera declares +X forward, +Y image-right, and +Z image-up. Mapping Three.js right/up/forward to those three axes has determinant -1, so a quaternion cannot represent it. The scenario adapter uses a proper rotation for forward and up and sets `horizontalMirror: true` for the remaining image-axis reflection. Footprint rays use that reflection. `applyMountedCamera` records it in `camera.userData.observationHorizontalMirror` for the render surface.

The render surface applies `horizontalMirror` after drawing the camera image. It must not implement the reflection with a negative camera scale: Three.js adjusts face culling for reflected object transforms, but not for a reflected camera transform. A negative camera scale can hide front-facing geometry. The surface must clear its image reflection when it returns to a non-mirrored view.

The body-local mount offset is rotated by the full vehicle attitude before being added to the vehicle position. Aircraft pitch and roll therefore move and rotate the camera. This is a rigid onboard pose, not an orbit camera placed near the aircraft.

## Projection and footprint

Each mount declares vertical field of view, aspect ratio, and near and far clipping distances. `applyMountedCamera` applies the resolved pose and those projection values to a `THREE.PerspectiveCamera`.

`cameraFootprintOnPlane` intersects the optical center and four frustum-corner rays with a constant world-up plane. Every ray reports one of four states:

- `hit`: the intersection is in front of the camera and inside its clipping range
- `parallel`: the ray lies on the horizon of the reference plane
- `behind`: the plane intersection is behind the optical origin
- `clipped`: the intersection is outside the declared near or far plane

The function returns a polygon only when all four corner rays report `hit`. A minimap can still use the individual ray results to distinguish a partial horizon view from a missing footprint. The module does not extend rays to an arbitrary range or create substitute points.

## Validation and integration boundary

Positions, offsets, plane height, projection values, and quaternion components must be finite. Quaternion magnitudes must already be within `1e-6` of one. The module rejects invalid rotations instead of normalizing them. Vertical field of view must be between 0 and 180 degrees, aspect must be positive, and clipping distances must satisfy `0 < near < far`.

The current integration may declare an authored display camera for simulated RGB preview. That declaration does not prove a Gazebo sensor, live stream, recorded frame, or provider capability. The consuming panel must label its source and keep the resolved pose attached to the same simulation tick as the vehicle pose, telemetry, overlays, and events.

The mount offset and near plane must be calibrated against the selected aircraft model so visible aircraft parts are intentional and the near plane does not cut through the mesh. This module does not move the camera to hide an intersection because such a correction would change the declared sensor pose.

`frontend/src/observation-camera-sensors.ts` reads a fixed camera declared in `PublicScenario.sensors`. It derives the body-relative mount from the parent and sensor initial world poses with the same ENU-to-renderer conversion used by the map. It does not assume that formal vehicle axes have the same labels as the rendered model axes.

The adapter returns `null` when the vehicle has no declared camera and rejects multiple cameras because the scenario has no preferred-camera field. It uses the declared horizontal and vertical FOV as bounds, then takes the largest centered crop for the current display aspect. The sensor resolution and declared FOV remain in the result for labeling. Near 0.05 m and far 14,000 m are viewer clipping settings, not sensor range claims.

The adapter labels its output `Simulated RGB · declared sensor pose` and states that no provider frame is present. A rendered view at this pose must not be labeled live or recorded video. An authored display camera for an undeclared sensor needs a separate label and mount source.

Replay code should pass the sampled vehicle and gimbal orientations from one simulation time. Pause, step, reset, speed changes, and seeks should select a new sample before resolving the camera. Missing gimbal state or a missing sensor should remain unavailable rather than reuse an earlier orientation.
