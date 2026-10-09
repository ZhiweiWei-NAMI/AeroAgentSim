# Viewer evaluation — Q7 + Q7b / D-viewer

The nonspatial presentation is implemented. Q7b completes the previously unmeasured
hardware part with verified RTX 3090 rendering and three repetitions per case for
both Vulkan and SwiftShader. GL/EGL also identifies the hardware but captures a black
3D viewport in this viewer; its timings are retained only as fault diagnostics.
The retirement decision remains **open**: the measured
limits and remaining visual/authoring/UI scope still require owner disposition.
The matched hardware result does not establish superiority over AeroBench.

## Recorded facts and presentation

Runs without spatial bindings open an inspector-first relation graph. Spatial runs
keep their 3D viewport and can open either Relations / topology or Queue / state
beside it. All views use the same retained store, entity generation, replay cursor,
and selected entity. No engine or journal state is written by these views.

The graph groups entities by the recorded `TypeInfo.directory` and declared type
ancestry. The service now reads browsing names/directories from
`registry.snapshot.json/details/raw_types`, including AeroGraph's `navigation.view`;
it never reads a mutable current ontology to label a historical run. Scenario-only
types keep their exact IDs and ancestry and have no invented directory.
Ancestry is the actual is-a list, not the browsing taxonomy.

Edges come from `TemporalFeedStore.edges`, after physical valid-time and retained
knowledge-cut resolution. Assertions, finite intervals, closes, cancellations and
entity removal therefore affect the graph through the same existing feed projection
as the inspector. Edge details retain their recorded interval and availability.
The state table reads `entity.fields`, never interpolated display positions or
guessed business states. Missing, null, false and zero remain distinguishable.
Scrubbing, including a same-nanosecond commit cut, updates these exact values.

Canvas draws the graph; bounded labels and 50-row pages limit DOM size. Layout
is deterministic and avoids a force simulation. A bounded raster caches unchanged
nodes/edges while selection and labels are drawn separately. Paused nonspatial runs
do not rebuild the store on every frame. No npm dependency was added.

## Hardware probe

Q7b runs outside the original sandbox, using headless Chromium **151.0.7922.34**.
The orchestrator's sequential probe is retained in
[orchestrator-gpu-probes.json](/tmp/aas-q/q7b/orchestrator-gpu-probes.json).
Q7b independently records the browser WebGL2 probe and each measured spatial
renderer in every raw measurement file. Both hardware backends identify RTX 3090:

```text
GL/EGL: ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3090/PCIe/SSE2, OpenGL ES 3.2)
Vulkan: ANGLE (NVIDIA, Vulkan 1.3.242 (NVIDIA NVIDIA GeForce RTX 3090 (0x00002204)), NVIDIA)
```

The harness accepts only a recognized NVIDIA hardware string in hardware mode;
SwiftShader, llvmpipe, softpipe, lavapipe, swrast, software and masked/unrecognized
strings fail the run. SwiftShader mode also verifies its actual renderer. Invalid
modes/cases, missing comparison builds/counters and nonfinite samples fail explicitly;
frame sampling has a 60-second watchdog. No alternate successful result is invented.

One full Vulkan pilot used the same seven measurement windows as GL/EGL. Its scale100
p50/p95/p99 was **83.3 / 100.0 / 100.0 ms**, matched ours **83.3 / 100.1 / 100.1 ms**,
and matched AeroBench **49.9 / 66.7 / 66.7 ms**, versus the first GL/EGL repetition's
**66.7 / 83.4 / 83.4**, **66.7 / 83.4 / 83.4**, and **16.7 / 16.8 / 33.4 ms**.
Screenshot inspection then found that GL/EGL's 3D viewport was black for our slice,
scale100, matched and synthetic cases, while DOM labels and renderer counters kept
updating. AeroBench's GL/EGL capture displayed its city. Vulkan displayed both
viewers' cities and our synthetic markers. GPU identity and draw counters alone
therefore do not establish usable presentation. The GL/EGL series is retained as
three repetitions of fault diagnostics, excluded from the accepted hardware table.
Vulkan is the hardware series and was completed to three repetitions without
changing the production build, quality preset, input feeds or camera. The cause of
the GL/EGL capture failure is not established here; no renderer fix is claimed.
`Q7_GL=hardware` now defaults to Vulkan; `Q7_ANGLE=gl-egl` explicitly selects the
diagnostic backend. The harness does not switch backends after an error.

For historical context, Q7's `frontend/e2e/q7-gpu-probe.mjs` ran sequentially,
with one browser at a time. `/dev/nvidia*` and `/dev/dri` were absent in this sandbox;
`nvidia-smi` reported it could not communicate with the NVIDIA driver. An NVIDIA
Vulkan ICD file exists, which alone does not establish device access.

| Requested backend | Actual result |
|---|---|
| ANGLE Vulkan, Vulkan enabled, Vulkan surface disabled | WebGL probe exceeded 15 s; GPU context/Skia initialization errors |
| ANGLE GL/EGL (`--use-gl=angle --use-angle=gl-egl`) | SwiftShader |
| EGL (`--use-gl=egl`) | SwiftShader |
| Explicit ANGLE SwiftShader | SwiftShader |

Original Q7 successful probes all reported:

```text
ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)
```

Raw evidence: [gpu-probes.json](/tmp/aas-q/q7/gpu-probes.json),
[gpu-probes.log](/tmp/aas-q/q7/gpu-probes.log). No requested hardware flag was treated
as proof of hardware rendering. The measurement harness rejects software renderer
strings when `Q7_GL=hardware` is selected.

## Measurement protocol and inputs

Use `frontend/e2e/q7-measure.mjs` against production builds. One browser/page,
1440 × 900 CSS pixels, DPR 1, two-second warmup, six-second sampling window per
case. Frame times are consecutive `requestAnimationFrame` timestamp differences;
p50/p95/p99 use nearest-rank percentiles. They include browser scheduling, compositor
wait and work on the page. CPU submission times are separately exposed; these are
not GPU timer-query measurements. Draw calls/triangles include the viewer's active
render passes, and do not count unique meshes or unique triangles.

Memory includes CDP `JSHeapUsedSize` and Three.js geometry/texture object counts.
These counters are not GPU allocation bytes or total process RSS. Q7b additionally
matches CDP's Chromium GPU-process PID to the `nvidia-smi` process table, recording
driver-reported process MiB after the screenshot. These are whole browser GPU-process
allocations, including compositor/caches and retained allocations from earlier cases;
they are not isolated scene allocations or peak VRAM. No per-resource VRAM estimate
is substituted. SwiftShader has no attributable NVIDIA allocation.

| Input | Provenance / scope |
|---|---|
| p1-slice city | Copied recorded run `p1-slice-city-fab3c5fdfec2`; 1,645 projected journal records; 5 spatial entities plus nonspatial orders/observations |
| p1-scale city, 100 entities | Actual execution of `scenarios/visual/p1-scale-city.yaml` with only the first 100 authored entities retained; ID `q7-p1-scale-city-100`; same engine configuration; 10 simulated seconds; 45 projected records |
| Synthetic spatial, 1,000 entities | Explicit `q7-fixtures.mjs` load fixture; declared marker binding; 41 commits over 10 seconds; position/state/value updates |
| Synthetic topology, 2,000 entities / 5,000 edges | Explicit load fixture; four derived types; 41 commits; state/value updates; 100 edges close at 5 seconds |

The real runs are exported using the production journal projector and its subject
projection context. Playwright routes the exported feed through the ordinary `/runs`
HTTP viewer boundary; this avoids including backend storage pagination latency in
rendering measurements. The synthetic fixtures are explicitly named and kept in the
test harness; they are never provided as production run data.

The initial physical cursor is 0 ns, after the available 0 ns commits. Quality is
manually fixed to Balanced (`med`) for our spatial cases, preventing automatic
quality downgrades from changing the measurement mid-window. The graph interaction
window dispatches a wheel zoom event on every animation frame. Q7 raw samples remain
under `/tmp/aas-q/q7`; Q7b raw samples, diagnostics, GPU PID snapshots and screenshots
are under `/tmp/aas-q/q7b`.

## Q7b repeated hardware and software results

Percentiles pool the actual frame intervals from three independent invocations;
they do not average per-run percentiles. Each repetition keeps the same case order,
2-second warmup and nominal 6-second sample window. The last interval can exceed the
window. Per-run counts and p95 ranges expose the sample size and variation. Canvas
2D graph and HTML table results have no WebGL calls/triangles and do not measure
WebGL throughput. All graph/state temporal checks passed in every completed run.

The hardware tables use Vulkan captures with visible city geometry/markers.
GL/EGL's renderer identity and submission counts are genuine, but the captured
3D viewport is black; those timings are not an accepted visual-performance result.
The GL/EGL capture failure remains a presentation limit despite verified GPU identity.

The GPUs were shared throughout, with heavy existing workloads. The recorded
Chromium GPU PID was attributed to device 0, an RTX 3090, in each hardware run.
Across Vulkan and GL/EGL post-capture snapshots, device 0 used **17,432–18,335 MiB**
of 24,576 MiB and reported **84–100%** utilization. These are whole-device totals
including other workloads and this browser; [snapshot observations](/tmp/aas-q/q7b/shared-load-observations.json)
retain the mapping to each raw record.
Driver snapshots are taken after capture, not continuously during the sample, so
they do not isolate competing work or establish a peak/dedicated-GPU budget. CPU
scheduling, browser compositor pacing and other workloads can affect variance.
Near-16.7 ms rAF intervals indicate the observed browser cadence; they do not
establish a higher hardware throughput ceiling.

JS heap is one instantaneous CDP sample per window, without forced GC. All cases
reuse a page within each invocation; the matched platform scene follows its
ordinary scale100 window, while AeroBench is a new navigation. These observations
are not an isolated memory benchmark. GPU process allocations also retain prior
case/compositor caches. Geometry/texture counts are Three.js object counts, not
allocation sizes, and calls/triangles include shadow/postprocessing passes.

SwiftShader's spatial windows still contain few intervals even after three
repetitions. In particular, each AeroBench window contributes only one long
interval: its pooled p95/p99 are extrema of three observations, not reliable tail
estimates. A nominal 6-second window ending on an 11-second frame is an overrun,
not a throughput claim based on six complete seconds. The frozen 80.75-second
preview remains a viewer workload, not a new simulation or native flight test.

AeroBench's hardware workload includes shadows (calls p50/max 959/967; about 8.30 million
triangles), while its software workload disables them (749 calls / about 7.63
million triangles). Our Balanced workload retains shadows in both modes. The
matched camera, surface and area do not equate entity types, positions, models,
visual detail or physics. The measured matched platform viewer remains slower
than AeroBench on the usable hardware backend despite fewer calls/triangles.
Its CPU update/submission times also remain substantial; these include scene
updates and render submission and do not identify a specific bottleneck or measure
GPU execution time. Hardware measurement is complete, while D-viewer performance
and replacement acceptance remain open.

**RTX 3090 / Vulkan** — three independent browser invocations.

| Case | Intervals in r1 / r2 / r3 | Pooled frame ms p50 / p95 / p99 | Per-run p95 range, ms | Calls p50 (max) | Triangles p50 (max) |
|---|---|---|---|---|---|
| p1-slice city / 5 spatial entities | 361 / 361 / 361 | 16.7 / 16.8 / 16.8 | 16.7–16.8 | 95 (95) | 219,809 (219,809) |
| p1-scale city / 100 | 74 / 78 / 81 | 83.3 / 83.5 / 100.0 | 83.4–100.0 | 95 (95) | 242,609 (242,609) |
| Matched surface: ours / 100 | 71 / 66 / 70 | 83.4 / 100.1 / 100.1 | 100.0–100.1 | 95 (95) | 242,609 (242,609) |
| Synthetic spatial / 1,000 | 318 / 339 / 345 | 16.7 / 33.3 / 33.4 | 16.8–33.3 | 48 (48) | 242,364 (242,364) |
| Graph / 2,000 nodes, 5,000 edges | 361 / 361 / 361 | 16.7 / 16.7 / 16.8 | 16.7 | N/A | N/A |
| State table / 2,000 entities | 361 / 361 / 361 | 16.7 / 16.7 / 16.8 | 16.7–16.8 | N/A | N/A |
| Matched surface: AeroBench / 94 | 136 / 166 / 143 | 33.4 / 66.7 / 66.7 | 50.1–66.7 | 959 (967) | 8,294,763 (8,296,130) |

**Verified SwiftShader** — three independent browser invocations.

| Case | Intervals in r1 / r2 / r3 | Pooled frame ms p50 / p95 / p99 | Per-run p95 range, ms | Calls p50 (max) | Triangles p50 (max) |
|---|---|---|---|---|---|
| p1-slice city / 5 spatial entities | 10 / 10 / 9 | 616.7 / 883.2 / 1016.6 | 700.0–1016.6 | 95 (95) | 219,809 (219,809) |
| p1-scale city / 100 | 9 / 9 / 10 | 650.0 / 833.2 / 883.3 | 700.0–883.3 | 95 (95) | 242,609 (242,609) |
| Matched surface: ours / 100 | 7 / 7 / 7 | 916.6 / 983.3 / 983.4 | 916.7–983.4 | 95 (95) | 242,609 (242,609) |
| Synthetic spatial / 1,000 | 13 / 12 / 13 | 499.9 / 550.0 / 616.6 | 533.2–616.6 | 48 (48) | 242,364 (242,364) |
| Graph / 2,000 nodes, 5,000 edges | 361 / 361 / 360 | 16.7 / 16.8 / 16.8 | 16.7–16.8 | N/A | N/A |
| State table / 2,000 entities | 361 / 361 / 361 | 16.7 / 16.7 / 16.8 | 16.7 | N/A | N/A |
| Matched surface: AeroBench / 94 | 1 / 1 / 1 | 11066.3 / 11266.1 / 11266.1 | 10816.3–11266.1 | 749 (749) | 7,627,014 (7,627,014) |

Memory ranges cover the three end-of-window observations; GPU process MiB is sampled after the screenshot. Object counts list the observed range. N/A means no WebGL renderer for the graph/table.

| Case | JS heap MiB, hardware / software | Geometries / textures, hardware | Geometries / textures, software | NVIDIA browser GPU-process MiB |
|---|---|---|---|---|
| p1-slice city / 5 spatial entities | 21.2–30.9 / 18.8–20.5 | 25 / 72 | 25 / 72 | 232 |
| p1-scale city / 100 | 45.5–55.4 / 14.7–24.8 | 25 / 72 | 25 / 72 | 238 |
| Matched surface: ours / 100 | 32.3–44.7 / 15.9–17.3 | 25 / 72 | 25 / 72 | 314 |
| Synthetic spatial / 1,000 | 92.8–103.4 / 54.7–64.1 | 8 / 43 | 8 / 43 | 211–212 |
| Graph / 2,000 nodes, 5,000 edges | 104.8–113.8 / 88.2–96.9 | N/A | N/A | 56–57 |
| State table / 2,000 entities | 93.8–111.7 / 98.2–101.6 | N/A | N/A | 56–57 |
| Matched surface: AeroBench / 94 | 86.3–113.9 / 71.8–72.1 | 576–578 / 204–205 | 464 / 192 | 895 |

CPU update/submission (graph: redraw) timings also pool raw samples; these are not GPU execution timings. The state table exposes no submission timer.

| Case | Hardware CPU ms p50 / p95 / p99 | SwiftShader CPU ms p50 / p95 / p99 |
|---|---|---|
| p1-slice city / 5 spatial entities | 3.0 / 4.2 / 5.1 | 5.1 / 6.6 / 6.8 |
| p1-scale city / 100 | 68.6 / 77.5 / 81.4 | 82.5 / 125.1 / 133.3 |
| Matched surface: ours / 100 | 72.8 / 77.1 / 78.8 | 82.2 / 125.0 / 138.2 |
| Synthetic spatial / 1,000 | 10.0 / 12.9 / 15.4 | 14.2 / 19.8 / 25.1 |
| Graph / 2,000 nodes, 5,000 edges | 3.8 / 5.4 / 6.1 | 3.8 / 5.3 / 6.1 |
| State table / 2,000 entities | N/A | N/A |
| Matched surface: AeroBench / 94 | 17.8 / 22.0 / 25.5 | 22.9 / 24.3 / 24.3 |

Raw data and pooled calculations: [summary.json](/tmp/aas-q/q7b/summary.json). Each JSON retains every frame interval and diagnostic sample, actual renderer strings, Chromium GPU information, screenshots and per-window memory observations.

| Series | Repetition 1 | Repetition 2 | Repetition 3 |
|---|---|---|---|
| hardware-vulkan | [raw r1](/tmp/aas-q/q7b/hardware-vulkan-r1/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r2](/tmp/aas-q/q7b/hardware-vulkan-r2/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r3](/tmp/aas-q/q7b/hardware-vulkan-r3/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) |
| swiftshader | [raw r1](/tmp/aas-q/q7b/swiftshader-r1/measurements-swiftshader-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r2](/tmp/aas-q/q7b/swiftshader-r2/measurements-swiftshader-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r3](/tmp/aas-q/q7b/swiftshader-r3/measurements-swiftshader-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) |
| hardware-gl-egl | [raw r1](/tmp/aas-q/q7b/hardware-gl-egl-r1/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r2](/tmp/aas-q/q7b/hardware-gl-egl-r2/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) | [raw r3](/tmp/aas-q/q7b/hardware-gl-egl-r3/measurements-hardware-p1-slice-city-p1-scale-city-100-synthetic-1000-topology-2000-5000-bench.json) |

GL/EGL screenshots: [black matched viewport](/tmp/aas-q/q7b/hardware-gl-egl-r2/matched-aas-100-hardware.png), [black synthetic viewport](/tmp/aas-q/q7b/hardware-gl-egl-r1/synthetic-1000-hardware.png). Usable Vulkan captures: [our matched city](/tmp/aas-q/q7b/hardware-vulkan-r3/matched-aas-100-hardware.png), [AeroBench matched city](/tmp/aas-q/q7b/hardware-vulkan-r3/matched-aerobench-native-hardware.png), [1,000 markers](/tmp/aas-q/q7b/hardware-vulkan-r2/synthetic-1000-hardware.png).

The follow-up [GL/EGL slice diagnostic](/tmp/aas-q/q7b/gl-egl-console-diagnostic/measurements-hardware-p1-slice-city.json) again captured black despite no page exception or captured console warning/error. No completed case had a captured page exception. Console capture was added for Vulkan r2/r3 and this diagnostic: Vulkan records ReadPixels stall warnings; AeroBench additionally records a shadow-map deprecation, Z-UP FBX notices, missing KHR_parallel_shader_compile, and one resource 404. The 404 URL was not captured, so its source is not inferred. Visible scenes and 94-entity diagnostics were verified; this is not an assertion that every network resource or live-control path succeeded.

## Historical Q7 sandbox results

The original Q7 WebGL cases below use **verified SwiftShader**, with the renderer
string shown above. Q7 had no hardware measurements; Q7b's repeated results above
complete that part. Canvas/table cases do not create a WebGL context.

| Case | Intervals sampled | Frame ms p50 / p95 / p99 | Draw calls p50 (max) | Triangles p50 (max) | JS heap MiB | Geometries / textures |
|---|---:|---|---|---|---:|---|
| p1-slice city, 5 spatial entities | 9 | 650.0 / 949.9 / 949.9 | 95 (95) | 219,809 (219,809) | 14.30 | 25 / 72 |
| p1-scale city, 100 spatial entities | 10 | 650.0 / 850.1 / 850.1 | 95 (95) | 242,609 (242,609) | 15.33 | 25 / 72 |
| Synthetic, 1,000 spatial entities | 13 | 483.3 / 533.3 / 533.3 | 48 (48) | 242,364 (242,364) | 50.50 | 8 / 43 |
| Matched surface: our city / 100 | 7 | 950.0 / 966.7 / 966.7 | 95 (95) | 242,609 (242,609) | 16.34 | 25 / 72 |
| Matched surface: AeroBench city / 94 | **1** | 10,899.6 / 10,899.6 / 10,899.6 | 749 (749) | 7,627,014 (7,627,014) | 62.57 | 464 / 192 |

The short software-rendering windows yield too few spatial samples for stable tail
estimates. In particular, AeroBench's one interval exceeds the nominal six-second
window: its three computed percentiles are the same single observation, **not a
usable p95/p99 distribution**. These are exploratory samples on this sandbox, not
a general performance ranking. No captured page errors occurred in the completed
cases. Full raw records:
[spatial measurements](/tmp/aas-q/q7/measurements-swiftshader-initial.json),
[AeroBench measurement](/tmp/aas-q/q7/measurements-swiftshader-bench.json).
The initial spatial result file also retains the slower pre-optimization graph
measurement; it is not the final graph result.

Final graph/state numbers appear in the separate
[nonspatial measurements](/tmp/aas-q/q7/measurements-swiftshader-topology-2000-5000.json).

| Nonspatial case | Intervals | Frame ms p50 / p95 / p99 | JS heap MiB | WebGL calls / triangles |
|---|---:|---|---:|---|
| Graph: 2,000 entities / 5,000 edges; wheel event every frame | 360 | **16.7 / 16.7 / 16.8** | 89.81 | Not applicable: Canvas 2D |
| State: 2,000 entities; paused, 50 rows per page | 361 | **16.7 / 16.7 / 16.8** | 98.03 | Not applicable: HTML table |

Graph redraw CPU p50 / p95 / p99 is **3.7 / 5.1 / 5.8 ms**; the state table has
no renderer submission counter. This graph sample measures navigation on a paused
cut. It does not establish the same throughput while continually ingesting new
large graphs. Before bitmap caching and removing redundant paused seeks, a less
frequent wheel-event run measured **166.6 / 250.0 / 400.0 ms**. The final test uses
a heavier interaction schedule, so the two runs are not an identical-workload ratio.

Browser checks verify graph selection in the inspector and mission timeline,
5,000 → 4,900 → 5,000 active relations on End/Home, and the selected state row
changing from `"running" / 1` to `"done" / 41` and back. These checks use all 41
commits, not a static mock screenshot.

CPU update/submission p50 / p95 / p99 in ms: slice **5.4 / 8.1 / 8.1**, scale100
**96.6 / 132.9 / 132.9**, synthetic1000 **13.6 / 16.7 / 16.7**, matched ours
**96.6 / 107.3 / 107.3**, AeroBench **23.5 / 23.5 / 23.5** (one sample).
These timings cover different viewer update paths and are not GPU execution times.

## Matched city comparison

AeroBench was copied from the actively edited, read-only main tree to
`/tmp/aas-q/q7/aerobench-copy`, excluding `node_modules` and `dist`. npm dependencies
were installed **offline** from a scratch copy of cached lockfile tarballs; no new
package or shared node_modules was modified. Its original build typechecked but
failed on the dangling `public/platform-0.1/native-parcel-0.1.mp4` symlink. Only this
unrelated missing video link was removed in the scratch copy; the offline build then
passed. The source main tree was not edited or built.

Both viewers use Shanghai Huangpu east. Their OSM2World mesh-pack manifests have the
same SHA-256:
`8f70bc96be129d5458336464026070fdab2af22ef12fa7435ead255e0300bc98`.
Both declare the same map origin, latitude 31.2288 / longitude 121.481.
Our synchronized public city/HDRI/model assets are copied from the platform tree into
scratch build output only; the worktree does not include those large asset bytes.

The matched camera uses position `[280,220,300]`, target `[30,25,-25]`, vertical
FOV 48°, near/far 0.15 / 16000 m, and a 1440 × 900 render surface. The scratch-only
AeroBench hook freezes its existing bundled traffic preview at **80.75 s**, removes
camera follow, and calls the existing preview renderer every frame. At this cut its
rendered counts are **53 motor vehicles + 12 bicycles + 27 pedestrians + 2 UAVs = 94**.
Ground traffic is retained SUMO-TraCI output; the two UAVs have the explicitly
declared `planned-visual-flight` source and are **not measured native flight**.
This is a viewer comparison, not a new AeroBench simulation execution. The hook
source is retained at
[aerobench-native-hook.txt](/tmp/aas-q/q7/aerobench-native-hook.txt).

An earlier attempt to adapt exactly 100 p1-scale positions into AeroBench's models
did not complete within the bounded browser run. It produced no valid comparison
metrics; [failure evidence](/tmp/aas-q/q7/aerobench-injection-failure.json) is retained.
The final comparison uses the closest working native preview with similar count,
as allowed by Q7. Positions, entity types and trajectories are consequently different.

This matches area, camera and render surface, with similar dynamic entity counts.
AeroBench retains its richer ground/road/fixture/building presentation and its own
textured X500 model. Our renderer uses instancing, distance/model-budget LOD,
HDRI and Balanced postprocessing. AeroBench disables shadows on software renderers
and enables them on hardware; our Balanced preset retains its own shadow pipeline
in both series. Thus AeroBench's software/hardware comparison also changes shadow
work. These differences must remain
visible when interpreting triangle/call/frame-time numbers. No equal-physics or
equal-visual-fidelity claim follows from this comparison.

Small review thumbnails (full screenshots remain in scratch):

| Our viewer, 100 entities | AeroBench, 94 entities |
|---|---|
| ![Our matched city](img/q7-viewer-city.png) | ![AeroBench matched city](img/q7-aerobench-city.png) |

Full captures: [our city](/tmp/aas-q/q7/matched-aas-100-swiftshader.png),
[AeroBench city](/tmp/aas-q/q7/matched-aerobench-native-swiftshader.png),
[graph](/tmp/aas-q/q7/topology-2000-5000-swiftshader.png),
[state table](/tmp/aas-q/q7/state-2000-swiftshader.png).

![2,000 entities / 5,000 relations](img/q7-topology.png)

## Feature comparison

| Capability | This viewer | AeroBench's copied viewer |
|---|---|---|
| Arbitrary registry types without a pose | Inspector-first directory/ancestry graph and per-type exact state table | Primarily typed spatial entities and city/operations panels; no equivalent generic AeroGraph feed graph identified |
| Exact validity and knowledge cuts | Canonical ns/microstep and commit cuts; retained temporal facts/relations | Public trace/scene-state ticks and recorded operations/business records; a different contract |
| Spatial display | Declared bindings, interpolated display poses, instanced models/markers, LOD, trails | City entities, traffic, native presentation bindings, observation/sensor views |
| Camera choices | Orbit, follow, chase, event director | Free/chase/cockpit, mounted observation cameras, overview and navigation controls |
| City detail | Shared OSM2World pack, optional roads/trees, HDRI and quality presets | Richer road surfaces/fixtures, buildings, vegetation, water, weather/time-of-day and reflections |
| Authoring | Platform Studio region/engine/scenario workflow | City Studio, building/model placement, terrain/road and asset editing workflows |
| Business/network UI | Generic facts, relation intervals, typed subjects, command/event/receipt inspector | Dedicated operations, cargo/order/custody, telemetry, network and algorithm panels |
| Performance display | Renderer diagnostics and reproducible Q7 harness | Existing `?perf=1` / F9 overlay plus preview/observation diagnostics |

Source evidence for the copied viewer: `src/map.ts`, `src/city-studio.ts`,
`src/entity-visuals.ts`, `src/city-perf-overlay.ts`, `src/operations-monitor.ts`,
`src/state/camera.ts`, `src/app.ts`. The comparison describes source and exercised
preview rendering; it does not assert that every authoring/live-control feature was
tested end to end in Q7.

## Reproduction and gates

Scratch artifacts are intentionally untracked. The orchestrator owns committing this
worktree; no commit, branch, checkout or reset was performed.

Q7b reuses both retained production builds and their assets; it does not rebuild or
write the live AeroBench tree. The servers started for Q7b use these commands in
their respective working directories and are stopped after measurement:

```bash
# frontend/ in wt-q7b
npm run preview -- --host 127.0.0.1 --port 18770 --strictPort \
 --outDir /tmp/aas-q/q7/viewer-dist
# /tmp/aas-q/q7/aerobench-copy/
npm run preview -- --host 127.0.0.1 --port 18771 --strictPort
# wt-q7b root: repeat in separate output directories (r1, r2, r3).
mkdir -p /tmp/aas-q/q7b/hardware-vulkan-r1
cp /tmp/aas-q/q7/{slice-feed,scale100-feed}.json /tmp/aas-q/q7b/hardware-vulkan-r1/
ln -s /tmp/aas-q/q7/aerobench-copy /tmp/aas-q/q7b/hardware-vulkan-r1/aerobench-copy
Q7_GL=hardware Q7_ANGLE=vulkan Q7_OUT=/tmp/aas-q/q7b/hardware-vulkan-r1 \
 Q7_CASES=p1-slice-city,p1-scale-city-100,synthetic-1000,topology-2000-5000,bench \
 node frontend/e2e/q7-measure.mjs
# Use Q7_GL=swiftshader and swiftshader-r1/r2/r3 for the baseline.
# Q7_ANGLE=gl-egl reproduces the separate black-viewport diagnostics.
```

The actual run order is GL/EGL r1, Vulkan r1, SwiftShader r1, GL/EGL r2,
SwiftShader r2, GL/EGL r3, SwiftShader r3, Vulkan r2 and Vulkan r3, followed by a
small GL/EGL slice-only console diagnostic. Each invocation launches one browser and
closes it before the next starts. The remaining-invocations driver is retained as
[run-remaining.sh](/tmp/aas-q/q7b/run-remaining.sh) and
[run-vulkan-completion.sh](/tmp/aas-q/q7b/run-vulkan-completion.sh); the nearest-rank pooling and
GPU-PID attribution script is [summarize.mjs](/tmp/aas-q/q7b/summarize.mjs).

Q7b changes the measurement harness and these two documentation files only.
`npm run typecheck` passed; `npm test -- --cache=false` passed **20 files / 91 tests**.
Final-state logs: [typecheck](/tmp/aas-q/q7b/typecheck-final.log),
[frontend tests](/tmp/aas-q/q7b/frontend-tests-final.log).
The cache flag prevents writes into shared `node_modules`. No dependency was added.
`node --check frontend/e2e/q7-measure.mjs` and `git diff --check` passed.
`node /tmp/aas-q/q7b/check-renderer-guard.mjs` passed **16 checks**, exercising the
actual guard against software/masked strings and both verified NVIDIA strings
without starting another browser. [Check log](/tmp/aas-q/q7b/renderer-guard-check.log).
Python ruff/strict mypy and the non-Docker Python suites are not applicable to
Q7b's touched files and were not rerun. No Docker/native simulation was run.
Two concurrent WorkBuddy DSH audit sessions completed with **exit 0**, using
`workbuddy/glm-5.3-flash`, **131072 maxTokens**, no effort parameter. Their file scope
was scratch reports only: [protocol audit](/tmp/aas-q/q7b/glm-protocol/audit.txt) and
[evidence audit](/tmp/aas-q/q7b/glm-evidence/audit.txt); transcripts are retained in
`/tmp/aas-q/q7b/dsh-home/sessions`. Both agents' tool sandboxes could not read the
retained Q7 scratch directory. Their claim that the scratch hook was absent is
incorrect for the parent process: the parent read it and verified its actual
renderer/camera/count diagnostics during the runs. Timeout, renderer recognition,
output isolation and memory caveat findings were checked against source/results.
The original Q7 implementation gates and build preparation follow for provenance.

```bash
# Run from the worktree root.
node frontend/e2e/q7-gpu-probe.mjs /tmp/aas-q/q7
# Run each frontend in its own cwd; the comparison frontend is the scratch copy.
cd frontend
npm run typecheck
npm test -- --cache=false
npm run build -- --outDir /tmp/aas-q/q7/viewer-dist
npm run preview -- --host 127.0.0.1 --port 18770 --outDir /tmp/aas-q/q7/viewer-dist
# A separate shell, inside the copied frontend:
cd /tmp/aas-q/q7/aerobench-copy
npm ci --offline --cache /tmp/aas-q/q7/npm-cache --no-audit --no-fund
npm run build
npm run preview -- --host 127.0.0.1 --port 18771
# Worktree root, after the feed exports and scratch assets are prepared:
Q7_CASES=p1-slice-city,p1-scale-city-100,synthetic-1000 node frontend/e2e/q7-measure.mjs
Q7_CASES=topology-2000-5000 node frontend/e2e/q7-measure.mjs
Q7_CASES=bench node frontend/e2e/q7-measure.mjs
# Hardware invocation (Q7b's repetition directories/flags are specified above):
Q7_GL=hardware Q7_ANGLE=vulkan node frontend/e2e/q7-measure.mjs
```

Feed preparation uses scratch runs only. The retained 100-entity scenario is
[p1-scale-city-100.yaml](/tmp/aas-q/q7/p1-scale-city-100.yaml); its only authored
changes are ID and truncating the original `entities` list to its first 100 entries.
The execution command was:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src MYPYPATH=../aerokernel \
 AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph \
 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python \
 -m aeroagentsim.services.cli run /tmp/aas-q/q7/p1-scale-city-100.yaml \
 --out /tmp/aas-q/q7/runs
```

The printed run directory changes on a new execution. For the retained directories,
export with the mandated interpreter/environment:

```python
import json
from pathlib import Path
from aeroagentsim.services.storage import RunStorage
from aeroagentsim.services.projector import header, project
from aeroagentsim.services.subjects import projection_context

root = Path('/tmp/aas-q/q7')
for name, run in [
    ('slice-feed', 'p1-slice-city-fab3c5fdfec2'),
    ('scale100-feed', 'p1-scale-city-100-42796dc2258a'),
]:
    directory = root / 'runs' / run
    subjects = projection_context(directory, 1)
    commits = [project(record, subjects=subjects)
               for record in RunStorage(directory).records(1, 100000)]
    (root / f'{name}.json').write_text(json.dumps({
        'header': header(directory), 'commits': commits,
    }))
```

Our build output additionally needs the existing city, HDRI and X500 assets under
its normal `/assets/` paths; the retained scratch output already contains them.
Copy these assets to scratch output from the read-only platform public directory,
without modifying the shared source. AeroBench reproduction needs the retained
scratch hook above inserted into its camera/controls initialization; its source
remains copied under scratch. The committed harness never patches the live main tree.

Python checks use the mandated interpreter from the worktree root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src MYPYPATH=../aerokernel \
 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m ruff check \
 src/aeroagentsim/services/projector.py tests/platform/test_projector_presentation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src MYPYPATH=../aerokernel \
 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m mypy --strict \
 --cache-dir /tmp/aas-q/q7/mypy-cache \
 src/aeroagentsim/services/projector.py tests/platform/test_projector_presentation.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src MYPYPATH=../aerokernel \
 AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph \
 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m pytest \
 -q -p no:cacheprovider -m "not docker" tests/platform \
 --basetemp /tmp/aas-q/q7/pytest
# Separate new-test run after collection of the full suite:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src MYPYPATH=../aerokernel \
 AEROAGENTSIM_AEROGRAPH_ROOT=/mnt/data2/weizhiwei/AeroGraph \
 /mnt/data2/weizhiwei/aeroagentsim/AeroAgentSim-platform/.venv/bin/python -m pytest \
 -q -p no:cacheprovider -m "not docker" tests/platform/test_projector_presentation.py \
 --basetemp /tmp/aas-q/q7/pytest-presentation
```

ruff and strict mypy: **passed, 2 files**. Platform suite: **145 passed** in
188.72 s, plus the newly added pinned-presentation test **1 passed** in 7.87 s
(collected after the full run began). The full run has one existing
Starlette/httpx deprecation warning. No adapters/agents/packs/authoring implementation
changed; their suites and Docker/native simulators were not run for Q7.

Frontend: `npm run typecheck` **passed**; `npm test -- --cache=false` **20 files /
91 tests passed**; production build **passed**. The default test command passed its
tests but failed writing the Vitest results cache through read-only shared
`node_modules`; disabling that cache resolved the command. No npm package was added.
`node --check` passed for all three Q7 `.mjs` harness files; `git diff --check` passed.

Two concurrent broad WorkBuddy DSH tasks (components and read-only comparison audit)
were stopped after prolonged reasoning and incomplete output; their partial code was
not accepted. Two narrower tasks then **completed with exit 0**: eight
temporal fixture cases and a four-item source audit. The cases were checked against
the real temporal store and integrated; audit wording conflating AeroAgentSim and
AeroBench was corrected against source. All sessions selected
`workbuddy/glm-5.3-flash`, **131072 maxTokens**, no effort parameter. Reviewed outputs
and actual session logs remain under `/tmp/aas-q/q7/glm-cases`, `glm-audit`,
`glm-graph`, `glm-bench` and `dsh-home/sessions`. Implementation, measurement and
gate verification remained the parent agent's responsibility.

The nonspatial presentation gate has usable implementation and measured evidence.
Q7b completes the missing hardware measurements and software reruns. D-viewer's
performance acceptance and broader visual/authoring replacement decision remain
open; this report does not authorize retiring AeroBench.
