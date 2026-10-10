/** Matched-camera video capture for the city render preview (U5), plus the per-segment
 * frame-time numbers the overlay reports (U2 evidence).
 *
 * Reuses the exact GPU launch flags and the bundle-hook technique of
 * `capture-city-canonical-ground.mjs` (route-intercept the built app bundle to expose
 * `window.__aeroVisualMap`). One browser context -- and one `.webm` -- per segment, so a
 * segment's scene reload cannot leak state (follow id, camera, playback clock) into the next.
 *
 * Modes:
 *   cameras  Hold each camera view from the supplied cameras.json while preview playback
 *            runs for --seconds of real wall-clock time (default 8s). --count limits how
 *            many of the file's cameras are captured (default: all of them).
 *   follow   For each actor id in --actors (a JSON array of ids, inline or a file path),
 *            follow it for --seconds of real wall-clock time. An entry `auto:vehicle` or
 *            `auto:pedestrian` selects, inside the loaded page, the recorded motor vehicle or
 *            person that is displayed at preview second 34 and has the largest recorded
 *            displacement between seconds 34 and 34 + --seconds (deterministic, not chosen
 *            for a good view).
 *
 * Follow obstruction check: every 10th sampled frame casts rays against the live scene with
 * the app's own three.js instance. (a) A ray from 600 m above the camera straight down to the
 * camera: a building-mesh hit means the camera is under a building roof (inside it).
 * (b) A segment from the camera to the actor aim point (actor origin + 1 m for ground actors,
 * the origin for UAVs): hits are attributed to buildings, vegetation, or road fixtures. Any
 * camera-inside-building sample or building-blocked sightline fails the segment; vegetation
 * and fixture blockage is reported as a fraction. The raycasts run in the sampled frames, so
 * those frames include the reported raycast time.
 *
 * Follow caveat (documented per the task brief): map.ts exposes no *public* method to follow
 * an arbitrary recorded traffic actor by id. `focusPreviewGroundEntity(kind)` only picks one
 * algorithmically and is private; `focusPreviewFlight(id)` is private and UAV-only. The actual
 * follow mechanism all of these funnel into is the private field `previewFollowId`, read every
 * frame inside the private `renderPreviewFrame()` (map.ts, the `else if (this.previewFollowId
 * !== null)` branch around line 2216). This script sets `window.__aeroVisualMap.previewFollowId`
 * directly -- the same bundle-hook/private-field pattern `capture-city-canonical-ground.mjs`
 * already uses for `previewFollowId`, `previewSeconds` and `setCameraMode` -- and lets the
 * app's own animation loop (already running once a scene with recorded traffic is active)
 * update the camera every frame.
 *
 * Usage:
 *   node capture-city-video.mjs <origin> <output-dir> <scene-path> <cameras.json> cameras \
 *     [--seconds=8] [--count=N]
 *   node capture-city-video.mjs <origin> <output-dir> <scene-path> <cameras.json> follow \
 *     --actors=<ids.json|'["a","b"]'> [--seconds=8]
 */
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

function parseArgs(argv) {
  const positional = [];
  const options = {};
  for (const arg of argv) {
    const match = /^--([^=]+)=(.*)$/.exec(arg);
    if (match) options[match[1]] = match[2];
    else positional.push(arg);
  }
  return { positional, options };
}

const { positional, options } = parseArgs(process.argv.slice(2));
const [origin = 'https://127.0.0.1:5209', outputArg = '../validation/city-video', scenePath = '/city-presentation/default-scene-v1.json',
  camerasFile, mode] = positional;
const output = resolve(outputArg);
const seconds = Number(options.seconds ?? '8');
if (!Number.isFinite(seconds) || seconds <= 0) throw new Error(`--seconds must be a positive number, got ${options.seconds}`);
if (mode !== 'cameras' && mode !== 'follow') throw new Error(`mode must be 'cameras' or 'follow', got ${JSON.stringify(mode)}`);
if (!camerasFile) throw new Error('cameras.json path is required (4th positional argument)');

async function loadActorIds() {
  const raw = options.actors;
  if (!raw) throw new Error("follow mode requires --actors=<ids.json|'[\"id1\",\"id2\"]'>");
  const text = raw.trim().startsWith('[') ? raw : await readFile(resolve(raw), 'utf8');
  const ids = JSON.parse(text);
  if (!Array.isArray(ids) || ids.length === 0 || ids.some(id => typeof id !== 'string')) {
    throw new Error('--actors must resolve to a non-empty JSON array of string actor ids');
  }
  return ids;
}

const GPU_ARGS = [
  '--enable-gpu', '--use-angle=vulkan', '--enable-features=Vulkan',
  '--disable-vulkan-surface', '--disable-software-rasterizer', '--ignore-gpu-blocklist',
];

/** Mirrors the percentile method in src/city-perf-overlay.ts (linear interpolation over an
 * ascending-sorted array). Duplicated here deliberately: this script runs under plain Node
 * against raw samples collected from the page, so it cannot import the TS module without a
 * build step, and this file owns no other shared runtime with the app bundle. */
function percentile(sortedAscending, fraction) {
  if (sortedAscending.length === 0) throw new Error('percentile requires at least one sample');
  const lastIndex = sortedAscending.length - 1;
  const rank = fraction * lastIndex;
  const lowerIndex = Math.floor(rank), upperIndex = Math.ceil(rank);
  const lower = sortedAscending[lowerIndex], upper = sortedAscending[upperIndex];
  if (lowerIndex === upperIndex) return lower;
  const weight = rank - lowerIndex;
  return lower * (1 - weight) + upper * weight;
}

async function hookBundle(page) {
  let identity = null;
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    identity = { url: route.request().url(), sha256: createHash('sha256').update(source).digest('hex'),
      sizeBytes: Buffer.byteLength(source) };
    const token = 'this.map = new PublicTraceMap(';
    if (source.split(token).length !== 2) {
      throw new Error('Source visual capture map hook no longer matches exactly once');
    }
    await route.fulfill({ response, body: source.replace(token,
      'window.__aeroVisualMap = this.map = new PublicTraceMap(') });
  });
  await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    identity = { url: route.request().url(), sha256: createHash('sha256').update(source).digest('hex'),
      sizeBytes: Buffer.byteLength(source) };
    const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
    if (!pattern.test(source)) throw new Error('Visual capture map hook no longer matches');
    await route.fulfill({ response, body: source.replace(pattern,
      match => match.replace('this.map=', 'window.__aeroVisualMap=this.map=')) });
  });
  return () => identity;
}

async function waitForSceneReady(page) {
  await page.waitForFunction(() => {
    const data = document.querySelector('#city-map')?.dataset;
    return (data?.sceneReady === 'true' && data?.skyReady === 'true'
      && data?.texturesReady === 'true') || data?.sceneError === 'true';
  }, undefined, { timeout: 300000 });
  const sceneError = await page.locator('#city-map').getAttribute('data-scene-error-message');
  if (sceneError) throw new Error(`City scene failed: ${sceneError}`);
  await page.locator('#city-map .city-scene-loading').waitFor({ state: 'hidden', timeout: 300000 });
  await page.waitForFunction(() => (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
    undefined, { timeout: 300000, polling: 250 });
}

/** Runs inside the page. Starts a rAF loop recording per-frame timing/draw-call samples
 * until window.__perfSamplingActive is set to false. */
async function startSampling() {
  window.__perfSamples = [];
  window.__perfSamplingActive = true;
  const map = window.__aeroVisualMap;
  // Constructors from the live map preserve the exact Three.js instance in source and built bundles.
  if (!map.raycaster?.constructor || !map.camera.position?.constructor) throw new Error('Live Three.js constructors are missing');
  const raycaster = new map.raycaster.constructor();
  const Vector3 = map.camera.position.constructor;
  const roots = {
    buildings: map.buildingPresentation,
    vegetation: map.cityVegetationLayer?.group ?? null,
    vegetationLegacy: map.vegetationPresentation,
    roadFixtures: map.roadPresentation,
    staticSignals: map.staticSignalPresentation ?? null,
  };
  if (roots.buildings === null || roots.buildings === undefined) throw new Error('No live building presentation to test');
  const firstHit = (origin, direction, far, root) => {
    if (root === null || root === undefined || !root.visible) return null;
    raycaster.set(origin, direction); raycaster.near = 0; raycaster.far = far;
    // three.js raycasts ignore `visible`; count only visible meshes (hidden collision-box
    // helpers and line segments are not part of the rendered view).
    const shown = object => { for (let node = object; node !== null; node = node.parent) if (!node.visible) return false; return true; };
    const hit = raycaster.intersectObject(root, true).find(item => item.object.isMesh && shown(item.object));
    return hit === undefined ? null : { distance: hit.distance, object: hit.object.name || hit.object.type };
  };
  const obstruction = () => {
    const id = map.previewFollowId;
    const center = id === null ? null : map.trafficPreview?.entityPosition(id) ?? null;
    if (center === null) return null;
    const started = performance.now();
    const camera = map.camera.position.clone();
    const aim = center.clone().add(new Vector3(0, id.startsWith('uav.') ? 0 : 1, 0));
    const toActor = aim.clone().sub(camera);
    const distance = toActor.length();
    toActor.normalize();
    const above = camera.clone().add(new Vector3(0, 600, 0));
    const roof = firstHit(above, new Vector3(0, -1, 0), 600 - 0.05, roots.buildings);
    const blockers = {};
    for (const [name, root] of Object.entries(roots)) {
      const hit = firstHit(camera, toActor, Math.max(0, distance - 0.5), root);
      if (hit !== null) blockers[name] = hit;
    }
    const ndc = aim.clone().project(map.camera);
    const canvas = map.renderer.domElement;
    const screen = { x: (ndc.x + 1) / 2 * canvas.clientWidth, y: (1 - ndc.y) / 2 * canvas.clientHeight };
    const inView = Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1 && ndc.z < 1;
    return { actorInView: inView, actorScreenCssPx: [screen.x, screen.y],
      canvasCssPx: [canvas.clientWidth, canvas.clientHeight],
      cameraUnderBuildingRoof: roof !== null, roofHit: roof, cameraY: camera.y,
      sightlineM: distance, blockers, raycastMs: performance.now() - started };
  };
  let last = performance.now();
  let frameIndex = 0;
  const loop = now => {
    const frameMs = now - last;
    last = now;
    const check = frameIndex++ % 10 === 0 ? obstruction() : undefined;
    window.__perfSamples.push({
      obstruction: check,
      t: now, frameMs,
      triangles: map.renderer.info.render.triangles,
      drawCallsDataset: Number(map.root.dataset.previewRenderCalls ?? map.root.dataset.rendererDrawCalls ?? 'NaN'),
      followId: map.root.dataset.previewFollowId ?? '',
      actorPosition: map.previewFollowId === null ? null
        : map.trafficPreview?.entityPosition(map.previewFollowId)?.toArray() ?? null,
      cameraPosition: map.camera.position.toArray(),
    });
    if (window.__perfSamplingActive) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

async function stopSampling(page) {
  await page.evaluate(() => { window.__perfSamplingActive = false; });
  // Let the in-flight rAF callback (if any) observe the flag before we read the array.
  await page.waitForTimeout(50);
  return page.evaluate(() => window.__perfSamples);
}

function summarize(samples) {
  if (samples.length < 2) throw new Error(`need at least 2 frame samples, got ${samples.length}`);
  // Drop the very first sample: its frameMs is measured from before sampling started.
  const timed = samples.slice(1);
  const frameMsSorted = timed.map(s => s.frameMs).sort((a, b) => a - b);
  const meanFrameMs = frameMsSorted.reduce((sum, v) => sum + v, 0) / frameMsSorted.length;
  const last = samples[samples.length - 1];
  const positioned = samples.filter(sample => sample.actorPosition !== null);
  const distance = (left, right) => Math.hypot(...left.map((value, index) => value - right[index]));
  return {
    frameCount: samples.length,
    fps: meanFrameMs > 0 ? 1000 / meanFrameMs : 0,
    frameMsP50: percentile(frameMsSorted, 0.5),
    frameMsP95: percentile(frameMsSorted, 0.95),
    frameMsMax: frameMsSorted[frameMsSorted.length - 1],
    drawCallsLast: last.drawCallsDataset,
    trianglesLast: last.triangles,
    followIdLast: last.followId,
    actorPositionSampleCount: positioned.length,
    actorDisplacementM: positioned.length < 2 ? null
      : distance(positioned[0].actorPosition, positioned.at(-1).actorPosition),
    cameraDisplacementM: distance(samples[0].cameraPosition, last.cameraPosition),
    actorPositionStart: positioned[0]?.actorPosition ?? null,
    actorPositionEnd: positioned.at(-1)?.actorPosition ?? null,
    cameraPositionStart: samples[0].cameraPosition,
    cameraPositionEnd: last.cameraPosition,
  };
}

function summarizeObstruction(samples) {
  const checks = samples.map(sample => sample.obstruction).filter(check => check !== undefined && check !== null);
  const count = predicate => checks.filter(predicate).length;
  const fraction = predicate => checks.length === 0 ? null : count(predicate) / checks.length;
  const raycastMs = checks.map(check => check.raycastMs).sort((a, b) => a - b);
  return {
    checkCount: checks.length,
    actorOutOfViewCount: count(check => !check.actorInView),
    actorScreenCssPxFirst: checks[0]?.actorScreenCssPx ?? null,
    canvasCssPx: checks[0]?.canvasCssPx ?? null,
    cameraInsideBuildingCount: count(check => check.cameraUnderBuildingRoof),
    cameraBelowGroundCount: count(check => check.cameraY < 0.2),
    buildingBlockedCount: count(check => check.blockers.buildings !== undefined),
    vegetationBlockedFraction: fraction(check => check.blockers.vegetation !== undefined
      || check.blockers.vegetationLegacy !== undefined),
    fixtureBlockedFraction: fraction(check => check.blockers.roadFixtures !== undefined
      || check.blockers.staticSignals !== undefined),
    anyBlockedFraction: fraction(check => Object.keys(check.blockers).length > 0),
    sightlineM: checks.length === 0 ? null : {
      min: Math.min(...checks.map(check => check.sightlineM)), max: Math.max(...checks.map(check => check.sightlineM)) },
    raycastMsP50: raycastMs.length === 0 ? null : percentile(raycastMs, 0.5),
    raycastMsMax: raycastMs.at(-1) ?? null,
    blockedExamples: checks.filter(check => check.cameraUnderBuildingRoof || Object.keys(check.blockers).length > 0)
      .slice(0, 5),
  };
}

async function readRenderer(page) {
  return page.evaluate(() => {
    const gl = window.__aeroVisualMap.renderer.getContext();
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
}

/** Runs one recorded segment in its own browser context (its own fresh scene load and its
 * own .webm). `applyFn`/`applyArg` set up the camera (or the follow target) once the scene
 * is ready, before sampling starts. */
async function runSegment(browser, { name, outputBasename, applyFn, applyArg }) {
  const errors = [];
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 },
    recordVideo: { dir: output, size: { width: 1600, height: 1000 } },
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(`${name}: ${error.message}`));
  let result;
  try {
    const getBundleIdentity = await hookBundle(page);
    await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`, { waitUntil: 'domcontentloaded' });
    await waitForSceneReady(page);
    const setup = await page.evaluate(applyFn, applyArg);
    const renderer = await readRenderer(page);
    await page.evaluate(startSampling);
    await page.waitForTimeout(Math.round(seconds * 1000));
    const samples = await stopSampling(page);
    const stats = summarize(samples);
    result = { name, renderer, publishedBundle: getBundleIdentity(), requestedDurationS: seconds, setup, pageErrors: errors, ...stats };
    if (mode === 'follow') result.obstruction = summarizeObstruction(samples);
  } finally {
    const video = page.video();
    await page.close();
    await context.close();
    if (video) {
      const finalPath = resolve(output, `${outputBasename}.webm`);
      await rename(await video.path(), finalPath);
      if (result) result.video = finalPath;
    }
  }
  if (result === undefined) throw new Error(`segment ${name} failed before producing a result`);
  return result;
}

function applyCamera(view) {
  const map = window.__aeroVisualMap;
  map.previewFollowId = null;
  map.setCameraMode('free');
  map.camera.position.set(...view.eye);
  map.controls.target.set(...view.target);
  map.controls.update();
  map.configureCityPresentation({ mood: 'day' });
}

function applyFollow({ actorId: requestedId, seconds }) {
  const map = window.__aeroVisualMap;
  map.previewPlaying = false;
  cancelAnimationFrame(map.previewAnimation);
  map.previewAnimation = 0;
  map.previewSeconds = 34;
  map.renderPreviewFrame();
  const frames = map.trafficPreview.data.frames;
  const frameAt = second => frames.reduce((closest, candidate) =>
    Math.abs(candidate.second - second) < Math.abs(closest.second - second) ? candidate : closest, frames[0]);
  const start34 = frameAt(34), end = frameAt(34 + seconds);
  const vehicleIds = new Set(start34.vehicles.filter(row => row[4] !== 'bicycle').map(row => row[0]));
  const bicycleIds = new Set(start34.vehicles.filter(row => row[4] === 'bicycle').map(row => row[0]));
  const personIds = new Set(start34.persons.map(row => row[0]));
  let actorId = requestedId, selection = null;
  if (requestedId.startsWith('auto:')) {
    const kind = requestedId.slice(5);
    if (kind !== 'vehicle' && kind !== 'pedestrian') throw new Error(`Unsupported auto follow kind: ${kind}`);
    const startRows = kind === 'vehicle' ? start34.vehicles.filter(row => row[4] !== 'bicycle') : start34.persons;
    const endRows = new Map((kind === 'vehicle' ? end.vehicles : end.persons).map(row => [row[0], row]));
    const candidates = startRows.filter(row => endRows.has(row[0]) && map.trafficPreview.entityPosition(row[0]) !== null)
      .map(row => ({ id: row[0], displacementM: Math.hypot(endRows.get(row[0])[1] - row[1], endRows.get(row[0])[2] - row[2]) }))
      .sort((left, right) => right.displacementM - left.displacementM || left.id.localeCompare(right.id));
    if (candidates.length === 0) throw new Error(`No displayed ${kind} persists from 34 s to ${34 + seconds} s`);
    actorId = candidates[0].id;
    selection = { rule: 'largest recorded displacement among displayed actors present at both ends',
      fromSecond: start34.second, toSecond: end.second, candidateCount: candidates.length, top: candidates.slice(0, 5) };
  }
  const start = map.trafficPreview?.entityPosition(actorId);
  if (start === null || start === undefined) throw new Error(`Follow actor is absent at t=34: ${actorId}`);
  // Direct private-field assignment: see the module-level comment on the follow caveat.
  map.previewFollowId = actorId;
  map.setCameraMode('free');
  map.previewPlaying = true;
  map.previewLastFrameAt = performance.now();
  map.syncPreviewAnimation();
  return {
    actorId,
    actorKind: actorId.startsWith('uav.') ? 'uav' : bicycleIds.has(actorId) ? 'bicycle'
      : vehicleIds.has(actorId) ? 'vehicle' : personIds.has(actorId) ? 'pedestrian' : 'unsupported',
    requestedId,
    selection,
    previewSecond: 34,
    actorPosition: start.toArray(),
    flightSourceKind: map.trafficPreview?.flightData.source_kind ?? null,
    roadAssetsVerified: map.root.dataset.roadAssetsVerified,
    groundCoverDrawnSet: JSON.parse(map.root.dataset.cityGroundCoverDrawnSet ?? 'null'),
  };
}

async function main() {
  await mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chromium', headless: true, args: GPU_ARGS });
  const segments = [];
  try {
    if (mode === 'cameras') {
      const cameras = JSON.parse(await readFile(resolve(camerasFile), 'utf8'));
      const count = options.count !== undefined ? Number(options.count) : cameras.length;
      if (!Number.isInteger(count) || count < 1) throw new Error(`--count must be a positive integer, got ${options.count}`);
      const selected = cameras.slice(0, count);
      if (selected.length === 0) throw new Error('cameras.json contains no cameras to capture');
      for (const view of selected) {
        segments.push(await runSegment(browser, {
          name: `camera-${view.name}`, outputBasename: `camera-${view.name}`,
          applyFn: applyCamera, applyArg: view,
        }));
      }
    } else {
      const actorIds = await loadActorIds();
      for (const actorId of actorIds) {
        segments.push(await runSegment(browser, {
          name: `follow-${actorId.replace(":", "-")}`, outputBasename: `follow-${actorId.replace(":", "-")}`,
          applyFn: applyFollow, applyArg: { actorId, seconds },
        }));
      }
    }
  } finally {
    await browser.close();
  }
  const failures = [];
  for (const segment of segments) {
    if (/swiftshader|llvmpipe|software/i.test(segment.renderer)) failures.push(`${segment.name}: software renderer`);
    if (segment.pageErrors.length) failures.push(`${segment.name}: page errors`);
    if (mode === 'follow' && !segment.followIdLast) {
      failures.push(`${segment.name}: previewFollowId was cleared, actor id not found in recorded traffic`);
    }
    if (mode === 'follow' && !['bicycle', 'uav', 'vehicle', 'pedestrian'].includes(segment.setup?.actorKind)) {
      failures.push(`${segment.name}: target is not a recorded vehicle/pedestrian/bicycle/UAV actor`);
    }
    if (mode === 'follow' && segment.obstruction !== undefined) {
      const check = segment.obstruction;
      if (check.checkCount < 5) failures.push(`${segment.name}: only ${check.checkCount} obstruction checks ran`);
      if (check.actorOutOfViewCount > 0) failures.push(`${segment.name}: the actor was outside the view in ${check.actorOutOfViewCount} checks`);
      if (check.cameraInsideBuildingCount > 0) failures.push(`${segment.name}: camera was inside a building in ${check.cameraInsideBuildingCount} checks`);
      if (check.cameraBelowGroundCount > 0) failures.push(`${segment.name}: camera was below ground in ${check.cameraBelowGroundCount} checks`);
      if (check.buildingBlockedCount > 0) failures.push(`${segment.name}: a building blocked the actor sightline in ${check.buildingBlockedCount} checks`);
    }
    if (mode === 'follow' && segment.setup?.roadAssetsVerified !== 'true') {
      failures.push(`${segment.name}: actor is not hosted by the verified canonical road scene`);
    }
    if (mode === 'follow' && (segment.setup?.groundCoverDrawnSet?.status !== 'pass'
        || segment.setup?.groundCoverDrawnSet?.omission_count !== 0)) {
      failures.push(`${segment.name}: source-ground-cover drawn-set gate failed`);
    }
    if (mode === 'follow' && !(segment.actorDisplacementM > 0.1)) {
      failures.push(`${segment.name}: actor did not move during the recorded follow clip`);
    }
    if (mode === 'follow' && !(segment.cameraDisplacementM > 0.1)) {
      failures.push(`${segment.name}: follow camera did not move with the actor`);
    }
  }
  const report = { status: failures.length ? 'FAIL' : 'PASS', failures, origin, scenePath, camerasFile, mode, seconds, segments };
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, failures, segments: segments.map(s =>
    ({ name: s.name, renderer: s.renderer, fps: s.fps, frameMsP95: s.frameMsP95, video: s.video })) }, null, 2));
  if (failures.length) process.exitCode = 1;
}

await main();
