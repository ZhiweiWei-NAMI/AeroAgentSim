# Registered browser geometric depth

## Scope and opt-in

Set `camera.depth: true` on a `CaptureRequest` to request registered RGB plus
**rendered geometry (optical z)** from the browser camera. This is **not physical
ranging, a native RGB-D device, or weather sensing**. Omit `depth`, or set it to
`false`, for the existing RGB-only path; that path does not require or admit
unsolicited depth metadata. Non-boolean depth options are rejected.

`BrowserRenderer.render()` and `CommittedBrowserRenderer.render_cut()` return a
`RenderedFrame` with `png`, `mode`, and, for admitted depth requests, `depth`,
`calibration`, and `camera_pose`. The existing PNG artifact store/replay schema is
unchanged: it does **not** persist or replay these three additional fields. A
consumer needing depth must retain the returned bundle together with its request;
this change does not add a depth download API or a new scenario plugin API.

The implementation salvages the reviewed browser/static-skinned hunks of
`archive/pr-17-companion-platform` (commit
`4ea31d5a5c7451e7b6e835a8f17d3bb4033e4e8f`), not the whole draft. Unlike the archived
receiver, it admits requested depth only after validation. The archived
`AEROAGENTSIM_FRONTEND_DIST` override also selects the built capture console;
without it, the existing checkout-relative default is unchanged. No build is
created automatically by this override. The unrelated archived camera `up` and
`exclude_actor` options are not introduced here.

## Wire bundle

The JSON-lines bridge returns the applied `request`, `png_base64`, and all three
records below. The browser API uses `png_data_url` instead of `png_base64`.
**Every record** includes `request_id` and `source_cut`; fields listed here are
required and unknown fields within these records are rejected. These literals
and shapes define this optional extension; incompatible changes require an
explicitly revised contract, not reinterpretation of the encoding string.

| Record | Fields / interpretation |
|---|---|
| `depth` | `encoding: "float32-le-metres"`, integer `width`, `height`, `data_base64`, `near_m`, `far_m`, `depth_convention: "optical_z"`, `vertical_origin: "top-left"`, `invalid: "NaN"`, `source: "same-camera-webgl-depth-buffer"` |
| `calibration` | Flat row-major nine-element `K`; integer `width`, `height`; `registered_rgb_depth: true`; `depth_convention: "optical_z"`; `depth_min_m`, `depth_max_m`; `distortion: []`; `depth_semantics: "rendered geometry (optical z); not physical ranging or weather sensing"` |
| `camera_pose` | Nested row-major 4×4 `T_world_enu_from_camera_optical`; `source: "applied_committed_capture_camera"` |

### Encoding, units and missing samples

- `data_base64` is canonical standard base64 with padding, no whitespace. Decoded
  bytes are exactly `4 * width * height`: IEEE-754 binary32, **little-endian**,
  contiguous rows starting at the top left. No header, stride padding or compression.
- Sample `(x, y)` begins at byte `4 * (y * width + x)`. WebGL's bottom-origin
  readback is flipped once by the producer. RGB and depth share dimensions.
- A finite sample is positive camera-forward **z in metres**, within the inclusive
  declared clip interval. It is not Euclidean distance along the pixel ray.
- **Any IEEE-754 float32 NaN bit pattern means missing**, not zero range. Sky/clear
  depth, nonfinite reconstruction or samples outside the clip interval are
  encoded as NaN by the producer. Missing samples are binary NaNs inside base64,
  never JSON `NaN` numbers. An entirely missing image is admissible (for example,
  empty sky); admission does not promise useful geometric coverage.
- Positive/negative infinity, zero, negative finite samples, out-of-interval
  samples, and an absent/different `invalid` declaration are rejected. The
  default clip interval is `0.15`–`16000` metres unless the request specifies
  `near`/`far`; depth and calibration must agree with the request.

Dimensions retain the existing `1..8192` per-axis bound. Depth additionally limits
`width * height` to **4,194,304 pixels** (16 MiB raw depth), checked before browser
render-target allocation and before Python decoding. The bridge's existing
96 MiB total reply limit still applies, including PNG/base64/metadata overhead.
These are bounds, not a promise that every allowed image fits a particular GPU or
capture deadline. RGB-only dimensions and reply limits are unchanged.

### Intrinsics and coordinate frames

The renderer uses a centered, undistorted perspective camera. `fov` is the
**vertical** field of view in degrees, with square pixels and aspect
`width / height`. With `f = height / (2 * tan(fov / 2))`:

```text
K = [ f, 0, width/2,
      0, f, height/2,
      0, 0, 1 ]
```

Pixel coordinates use the **outer top-left image edge** as the origin; pixel
centres are `(x + 0.5, y + 0.5)`. For a finite sample `z`, the optical point is
`[(x + 0.5 - cx) * z / fx, (y + 0.5 - cy) * z / fy, z]`. Consumers using an
integer-centred convention must shift the principal point by `-0.5` themselves.
`distortion: []` means this rendered camera has no lens-distortion model, not
that a real device has been calibrated.

The optical frame is **right/down/forward**. The output world frame is local
**east/north/up (ENU), metres**, relative to the scene origin, not geographic
latitude/longitude and not PX4 body/NED coordinates. Multiply a homogeneous
optical column vector by `T_world_enu_from_camera_optical` to get ENU. Internally,
Three.js world axes are east/up/south and camera axes are right/up/back; the
export converts both bases. The 4×4 matrix must be finite with last row
`[0, 0, 0, 1]`, an orthonormal rotation, and determinant `+1`.
Admission allows `1e-6` absolute rotation/axis error and `1e-6` metres of
translation error (no coordinate-scaled translation tolerance). Intrinsics use
`1e-9` relative / `1e-8` absolute tolerance for floating-point reconstruction.

## Request/cut binding and fail-closed admission

1. The browser applies the requested committed scene and camera. Existing
   run/epoch/actor-generation, asset inventory, camera snapshot and source-cut
   checks remain in place. It renders RGB and geometric depth without advancing
   simulation time or animation between passes.
2. The bridge forwards only viewer-produced metadata. It never manufactures
   missing depth, calibration, pose or bindings from the input request.
3. Python checks the echoed request, including request ID, run, actor, camera,
   asset identity and cut, and validates the actual PNG and its dimensions.
4. All three extension records must carry the exact request ID and source cut
   `{index, instant: [nanoseconds, microstep]}`. The existing browser integer tags
   preserve large values (`{"$integer": "9007199254740993"}`); Python decodes them
   without rounding and requires integer cut coordinates, not booleans, floats
   or numeric strings. Returned frame metadata contains decoded Python integers.
5. Python validates encoding, dimensions, bounded byte count, every sample,
   clip planes, calibration against the requested FOV/dimensions, and rigid pose.
   Pose translation and all three axes (including roll) must agree with the
   requested camera. Explicit cameras require both `eye` and `target`. For known
   nadir/oblique revisions, Python reconstructs the camera from the request's
   cut-bound snapshot actor/subject positions and authored pitch/bearing. Missing
   or ambiguous snapshot positions fail; repeated actor rows must agree on
   position. The browser still verifies those snapshot positions against actual
   committed facts; the receiver is not an independent scene renderer.
6. Missing, malformed, stale or mismatched requested data raises
   `CaptureDepthError` (a `ValueError` carrying `request_id`), never a successful
   partial frame. Reply admission failure closes the browser bridge; no stub
   fallback is selected. The PNG-only `StubRenderer` refuses depth requests.
   Existing `TimeoutError` handling remains a timeout, not success. Through the
   capture engine, validation failures become typed `capture-receipt/v1`
   `status: "failed"` results with a reason and no successful artifact record.

Preparation/readiness is not a photograph. An ACK or accepted command is not
capture completion, storage acceptance, or mission success. Binding to an
owner-issued committed cut does not grant arbitrary callers a trusted-cut
bypass, nor establish projection finality or native sensor accuracy. Bundle
validation catches inconsistent metadata; it does not cryptographically attest
that an untrusted renderer rendered truthful pixels.

## Rendering limitations and verification status

- Depth is reconstructed from the same camera's depth-writing geometric surfaces
  with authored materials/alpha tests and the RGB base pass's atmosphere layer.
  Transparent rain, smoke, fog, fire or glass that does not write depth can affect
  RGB without supplying range samples. Tone mapping/weather appearance is not a
  physical sensor-response model. Depth-buffer precision/clip ratios constrain
  numerical accuracy, especially at long distance.
- Registered capture rejects diagnostic text overlays; it does not invent depth
  for label pixels. Lack of floating-point readback support is a failure.
- Catalogued skinned subjects use independent bone hierarchies cloned from the
  original cached GLB, fitted and placed at the committed pose. Geometry/material
  resources remain shared; cloned skeleton resources are disposed per capture.
  This is the authored **static** pose, not walking animation or physical motion.
- The new Python tests use synthetic PNG/depth and mocked bridge transport; the
  TypeScript tests exercise pure helpers only. Neither establishes real-browser
  rendering, native acceptance, sensor fidelity or task success.
- **Generate-only delivery:** no tests, pytest, npm, Vitest, TypeScript compiler,
  builds, package installations, images, Docker or servers were run for this
  change. Static review only. Test pass/fail is unverified.
- Still required in separately authorized validation: Python/TypeScript suites
  and typechecking; real Chromium optical-z values and pixel registration;
  source-cut/pose fidelity; static skinned clone visibility, isolation and
  disposal; hardware/software GL compatibility, memory bounds and deadlines.
  No native or physical-ranging acceptance is claimed by these browser checks.
