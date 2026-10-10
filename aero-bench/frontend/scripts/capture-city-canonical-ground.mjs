/** GPU browser acceptance for a render scene with verified canonical ground road assets.
 *
 * Usage: node capture-city-canonical-ground.mjs <origin> <output-dir> [scene-path] [cameras.json]
 * Cameras are derived from the scene's own canonical road on first use and written to
 * <output-dir>/cameras.json; passing that file back reproduces the same views (matched cameras).
 * Only the downloaded test bundle is hooked, to expose the map for camera placement.
 */
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export function verifiedGroundCoverInventory(bytes, reference, scene) {
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  if (sha256 !== reference.sha256 || bytes.length !== reference.size_bytes)
    throw new Error('Published environment source byte identity differs from its scene binding');
  const source = JSON.parse(bytes.toString('utf8'));
  if (source.schemaVersion !== 'aero-bench.city-environment-source/v1'
      || source.source?.osmSha256 !== scene.road_assets.mesh_pack_source_sha256
      || !Array.isArray(source.groundCovers))
    throw new Error('Published ground cover source contract or authority differs from the selected scene');
  const ids = source.groundCovers.map(cover => cover.id);
  if (ids.some(id => typeof id !== 'string' || !id.length) || new Set(ids).size !== ids.length
      || source.inspection?.groundCoverCount !== ids.length)
    throw new Error('Published ground cover inventory has invalid identities or count');
  if (scene.road_assets.source_scene_id === 'shanghai-huangpu-east-v1' && ids.length !== 19)
    throw new Error('Huangpu must retain exactly its 19 source ground covers');
  return { url: reference.url, sha256, sizeBytes: bytes.length, count: ids.length, ids: ids.sort() };
}

export function isCompleteGroundCoverDrawnSet(drawn, geometryId, expectedIds) {
  if (drawn?.status !== 'pass' || drawn.geometry_id !== geometryId || drawn.omission_count !== 0
      || !['parser_accepted_ids', 'drawable_ids', 'drawn_ids', 'omitted_ids', 'unexpected_ids']
        .every(field => Array.isArray(drawn[field]))) return false;
  if (!Array.isArray(expectedIds) || new Set(expectedIds).size !== expectedIds.length) return false;
  const expected = new Set(expectedIds), expectedCount = expectedIds.length;
  return drawn.parser_accepted_ids.length === expectedCount
    && new Set(drawn.parser_accepted_ids).size === expectedCount
    && drawn.parser_accepted_ids.every(id => expected.has(id))
    && drawn.drawable_ids.length === expectedCount && new Set(drawn.drawable_ids).size === expectedCount
    && drawn.drawable_ids.every(id => expected.has(id))
    && drawn.drawn_ids.length === expectedCount && new Set(drawn.drawn_ids).size === expectedCount
    && drawn.drawn_ids.every(id => expected.has(id))
    && drawn.omitted_ids.length === 0 && drawn.unexpected_ids.length === 0;
}

async function main() {
const origin = process.argv[2] ?? 'http://127.0.0.1:5209';
const output = resolve(process.argv[3] ?? '../validation/city-canonical-ground');
const scenePath = process.argv[4] ?? '/city-presentation/building-render-scene-v1.json';
const cameraFile = process.argv[5];
const SECONDS = [34, 36];
await mkdir(output, { recursive: true });
const sceneResponse = await fetch(new URL(scenePath, origin));
if (!sceneResponse.ok) throw new Error(`Published scene HTTP ${sceneResponse.status}`);
const sceneBytes = Buffer.from(await sceneResponse.arrayBuffer());
const scene = JSON.parse(sceneBytes.toString('utf8'));
const environmentResponse = await fetch(new URL(scene.environment_source.url, origin));
if (!environmentResponse.ok) throw new Error(`Published environment HTTP ${environmentResponse.status}`);
const coverInventory = verifiedGroundCoverInventory(Buffer.from(await environmentResponse.arrayBuffer()),
  scene.environment_source, scene);
const browser = await chromium.launch({ channel: 'chromium', headless: true, args: [
  '--enable-gpu', '--use-angle=vulkan', '--enable-features=Vulkan',
  '--disable-vulkan-surface', '--disable-software-rasterizer', '--ignore-gpu-blocklist',
] });
const page = await browser.newPage({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 } });
const errors = [];
let publishedBundle = null;
page.on('pageerror', error => errors.push(error.message));
await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  publishedBundle = { path: new URL(route.request().url()).pathname,
    sha256: createHash('sha256').update(source).digest('hex'), sizeBytes: Buffer.byteLength(source) };
  const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
  if (!pattern.test(source)) throw new Error('Visual capture map hook no longer matches');
  await route.fulfill({ response, body: source.replace(pattern,
    match => match.replace('this.map=', 'window.__aeroVisualMap=this.map=')) });
});
try {
  const start = performance.now();
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const data = document.querySelector('#city-map')?.dataset;
    return (data?.sceneReady === 'true' && data?.skyReady === 'true' && data?.texturesReady === 'true') || data?.sceneError === 'true';
  }, undefined, { timeout: 300000 });
  const sceneError = await page.locator('#city-map').getAttribute('data-scene-error-message');
  if (sceneError) throw new Error(`City scene failed: ${sceneError}`);
  await page.locator('#city-map .city-scene-loading').waitFor({ state: 'hidden', timeout: 300000 });
  const sceneQuiescence = await page.evaluate(async () => {
    const map = window.__aeroVisualMap;
    if (!map.renderStreamer) throw new Error('Canonical ground capture requires the live building streamer');
    await map.renderStreamer.loadAll();
    const progress = map.renderStreamer.progress;
    if (progress.active !== 0 || progress.failed !== 0 || progress.loaded !== progress.total)
      throw new Error(`Building streamer is not complete: ${JSON.stringify(progress)}`);
    return progress;
  });
  const loadMs = performance.now() - start;
  const binding = await page.evaluate(async scenePath => {
    const data = document.querySelector('#city-map').dataset;
    const scene = await (await fetch(scenePath)).json();
    const fixtures = await (await fetch(scene.road_assets.effective_fixtures.url)).json();
    return { verified: data.roadAssetsVerified, roadSha256: data.roadAssetsRoadSha256,
      effectiveFixturesSha256: data.roadAssetsEffectiveFixturesSha256,
      trafficSha256: data.roadAssetsTrafficSha256, flightSha256: data.roadAssetsFlightSha256,
      surfaceSha256: data.roadAssetsSurfaceSha256, sceneSource: data.sceneSource,
      expected: fixtures.counts };
  }, scenePath);
  if (binding.verified !== 'true') throw new Error('Scene did not verify its canonical road assets');
  if (binding.surfaceSha256 !== scene.road_assets.displayed_surface_sha256)
    throw new Error('Live road geometry differs from the inventory-bound published scene');
  binding.sceneSha256 = createHash('sha256').update(sceneBytes).digest('hex');
  binding.groundCoverInventory = coverInventory;
  const cameras = cameraFile ? JSON.parse(await readFile(cameraFile, 'utf8')) : await page.evaluate(async ({ scenePath, seconds }) => {
    const scene = await (await fetch(scenePath)).json();
    const roads = await (await fetch(scene.road_assets.road.url)).json();
    const traffic = await (await fetch(scene.road_assets.traffic.url)).json();
    const groups = new Map();
    for (const lane of roads.lanes) {
      if (lane.kind !== 'motor' || lane.id.startsWith(':') || lane.width < 2.5) continue;
      const edge = lane.id.slice(0, lane.id.lastIndexOf('_'));
      const lanes = groups.get(edge) ?? []; lanes.push(lane); groups.set(edge, lanes);
    }
    const candidates = [...groups.entries()].filter(([, lanes]) => lanes.length >= 2).map(([edge, lanes]) => {
      const p = lanes[Math.floor(lanes.length / 2)].shape;
      const a = p[0], b = p.at(-1), length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      return { edge, lanes: lanes.length, x: (a[0] + b[0]) / 2, z: (a[1] + b[1]) / 2, length,
        dx: (b[0] - a[0]) / length, dz: (b[1] - a[1]) / length };
    }).filter(c => c.length > 60).sort((a, b) => b.lanes - a.lanes || Math.hypot(a.x, a.z) - Math.hypot(b.x, b.z));
    const result = [{ name: 'overview', eye: [0, 420, 380], target: [0, 0, 0] }];
    for (const c of candidates) {
      if (result.length >= 4) break;
      if (result.some(r => Math.hypot(r.target[0] - c.x, r.target[2] - c.z) < 120)) continue;
      result.push({ name: `street-${result.length}`, edge: c.edge, laneCount: c.lanes,
        eye: [c.x - c.dx * 45 - c.dz * 10, 14, c.z - c.dz * 45 + c.dx * 10], target: [c.x, 1.5, c.z] });
    }
    const crossing = roads.crossings.map(item => ({ id: item.id, x: (item.shape[0][0] + item.shape.at(-1)[0]) / 2,
      z: (item.shape[0][1] + item.shape.at(-1)[1]) / 2 })).sort((a, b) => Math.hypot(a.x, a.z) - Math.hypot(b.x, b.z))[0];
    if (crossing) result.push({ name: 'crossing', crossingId: crossing.id,
      eye: [crossing.x - 22, 26, crossing.z + 26], target: [crossing.x, 0, crossing.z] });
    const signal = traffic.signals[0];
    if (signal) result.push({ name: 'signal', signalId: signal.id,
      eye: [signal.x - 14, 7, signal.z + 14], target: [signal.x, 4, signal.z] });
    const lamp = roads.street_lamps[0];
    if (lamp) result.push({ name: 'street-lamp', eye: [lamp.x - 10, 5, lamp.z + 10], target: [lamp.x, 3, lamp.z] });
    // Fixed cameras on recorded actor samples at the first capture time; the second capture
    // time then shows the recorded motion under the same camera.
    const frame = traffic.frames.find(item => Math.abs(item.second - seconds[0]) < 1e-6);
    const vehicle = frame?.vehicles.find(row => row[4] !== 'bicycle');
    if (vehicle) result.push({ name: 'vehicle', actorId: vehicle[0],
      eye: [vehicle[1] - 16, 9, vehicle[2] + 16], target: [vehicle[1], 1, vehicle[2]] });
    const person = frame?.persons[0];
    if (person) result.push({ name: 'pedestrian', actorId: person[0],
      eye: [person[1] - 7, 4, person[2] + 7], target: [person[1], 1, person[2]] });
    return result;
  }, { scenePath, seconds: SECONDS });
  await writeFile(resolve(output, 'cameras.json'), JSON.stringify(cameras, null, 2));
  const frames = [];
  for (const view of cameras) for (const second of SECONDS) {
    const stats = await page.evaluate(({ view, second }) => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      map.previewFollowId = null;
      map.previewSeconds = second;
      map.setCameraMode('free');
      map.camera.position.set(...view.eye);
      map.controls.target.set(...view.target);
      map.controls.update();
      map.configureCityPresentation({ mood: 'day' });
      map.renderPreviewFrame();
      map.renderer.getContext().finish();
      const data = map.root.dataset;
      const gl = map.renderer.getContext(), debug = gl.getExtension('WEBGL_debug_renderer_info');
      const drawnSignals = map.trafficPreview.group.children
        .filter(child => child.userData.target?.kind === 'traffic_signal').length;
      return { second, calls: map.renderer.info.render.calls, triangles: map.renderer.info.render.triangles,
        eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        drawnSignals, effectiveStreetLamps: map.roadPresentation?.userData.effectiveStreetLampCount,
        groundCoverDrawnSet: JSON.parse(data.cityGroundCoverDrawnSet ?? 'null'),
        loaderHidden: map.root.querySelector('.city-scene-loading')?.hidden === true,
        streamActive: map.renderStreamer.progress.active,
        visible: { vehicles: Number(data.previewVisibleVehicles), bicycles: Number(data.previewVisibleBicycles),
          pedestrians: Number(data.previewVisiblePedestrians), uavs: Number(data.renderedUavCount) },
        renderer: String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER)) };
    }, { view, second });
    await page.locator('#city-map canvas').screenshot({ path: resolve(output, `${view.name}-t${second}.png`) });
    frames.push({ view: view.name, ...stats });
  }
  const failures = [];
  if (frames.some(frame => /swiftshader|llvmpipe|software/i.test(frame.renderer))) failures.push('software renderer');
  if (frames.some(frame => frame.drawnSignals !== binding.expected.effective_signals))
    failures.push('drawn signals differ from the effective fixture inventory');
  if (frames.some(frame => frame.effectiveStreetLamps !== binding.expected.effective_street_lamps))
    failures.push('drawn street lamps differ from the effective fixture inventory');
  if (frames.some(frame => !isCompleteGroundCoverDrawnSet(frame.groundCoverDrawnSet, binding.surfaceSha256, coverInventory.ids)))
    failures.push(`canonical ${coverInventory.count}/${coverInventory.count} source-identity ground-cover drawn-set gate failed`);
  if (frames.some(frame => !frame.loaderHidden || frame.streamActive !== 0))
    failures.push('captured before loading and streaming settled');
  if (errors.length) failures.push('page errors');
  const report = { status: failures.length ? 'FAIL' : 'PASS', failures, origin, scenePath, loadMs,
    publishedBundle, sceneQuiescence, binding, frames, errors };
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, failures, loadMs, binding }, null, 2));
  if (failures.length) process.exitCode = 1;
} finally { await browser.close(); }
}

if (process.argv[1] === new URL(import.meta.url).pathname) await main();
