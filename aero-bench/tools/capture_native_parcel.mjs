#!/usr/bin/env node
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { parseArgs } from 'node:util';
import { NativeParcelStreamEvidence } from './native_parcel_stream_evidence.mjs';

const { values } = parseArgs({ options: {
  origin: { type: 'string' }, control: { type: 'string' },
  'credentials-file': { type: 'string' }, 'compilation-result': { type: 'string' },
  output: { type: 'string' }, 'reconnect-tick': { type: 'string', default: '4' },
  'timeout-seconds': { type: 'string', default: '1800' },
  'poll-seconds': { type: 'string', default: '5' },
  'request-timeout-seconds': { type: 'string', default: '60' },
  attach: { type: 'boolean', default: false },
  replay: { type: 'boolean', default: false },
  renderer: { type: 'string', default: 'software' },
  help: { type: 'boolean', short: 'h' },
} });
if (values.help) {
  console.log(`Capture the existing native parcel platform through its actual live UI.
Usage: node tools/capture_native_parcel.mjs --origin URL --control URL
  --credentials-file PATH --compilation-result PATH --output DIRECTORY
Options: --reconnect-tick 4 --timeout-seconds 1800 --poll-seconds 5
  --request-timeout-seconds 60 --attach --replay --renderer software|hardware
Use --attach to require an existing authenticated catalog start identity.
Use --replay to play the actual sealed history after the run completes.
Use --attach --replay to require an existing run and reuse its idempotent Start
only for access credentials. Without --attach the UI may start the selected
compiled run; an existing catalog start identity is still reused when present.
The credentials file is the private JSON written by tools/run_stack.py.
Starts through the UI, plays and follows the carrier, disconnects and logs in
again, reconnects to the same run/start identity, and waits for a real terminal.
Writes sanitized control evidence, append-only runtime-stream-evidence.jsonl,
screenshots and a Chromium video. Use a fresh output directory; the JSONL
transcript is never overwritten. Canonical sealed trace bytes stay separate.
Returns nonzero for runtime failure, timeout or incomplete viewer evidence.`);
  process.exit(0);
}
for (const key of ['origin', 'control', 'credentials-file', 'compilation-result', 'output']) {
  if (!values[key]) throw new Error(`Missing --${key}; use --help`);
}
function positiveNumber(key) {
  const value = Number(values[key]);
  if (!Number.isFinite(value) || value <= 0) throw new Error(`--${key} must be positive`);
  return value;
}
const reconnectTick = positiveNumber('reconnect-tick');
if (!Number.isInteger(reconnectTick)) throw new Error('--reconnect-tick must be an integer');
const timeoutMs = positiveNumber('timeout-seconds') * 1000;
const pollMs = positiveNumber('poll-seconds') * 1000;
const requestTimeoutMs = positiveNumber('request-timeout-seconds') * 1000;
if (!['software', 'hardware'].includes(values.renderer)) throw new Error('--renderer must be software or hardware');
const origin = new URL(values.origin).origin;
const endpoint = new URL(values.control).origin;
const out = resolve(values.output);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bootstrap = JSON.parse(readFileSync(resolve(values['credentials-file']), 'utf8'));
if (typeof bootstrap.bootstrap_token !== 'string' || !bootstrap.bootstrap_token
    || typeof bootstrap.bootstrap_csrf !== 'string' || !bootstrap.bootstrap_csrf) {
  throw new Error('Credentials file lacks bootstrap_token or bootstrap_csrf');
}
const compiled = JSON.parse(readFileSync(resolve(values['compilation-result']), 'utf8'));
if (compiled.runs?.length !== 1 || typeof compiled.runs[0].run_id !== 'string'
    || typeof compiled.compilation_id !== 'string') {
  throw new Error('Compilation result must contain exactly one run and a compilation_id');
}
const runId = compiled.runs[0].run_id;
mkdirSync(out, { recursive: true });
const secrets = new Set([bootstrap.bootstrap_token, bootstrap.bootstrap_csrf]);
function sanitize(value) {
  if (typeof value === 'string') {
    for (const secret of secrets) value = value.replaceAll(secret, '[REDACTED]');
    return value;
  }
  if (Array.isArray(value)) return value.map(sanitize);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) =>
      [key, /token|password|authorization|credentials|csrf/i.test(key) ? '[REDACTED]' : sanitize(item)]));
  }
  return value;
}
function save(name, value) {
  writeFileSync(resolve(out, name), JSON.stringify(sanitize(value), null, 2) + '\n');
}
const started = performance.now();
const elapsed = () => (performance.now() - started) / 1000;
function log(event, details = {}) { console.log(JSON.stringify(sanitize({ event, ...details, elapsed_s: elapsed() }))); }
const { chromium } = createRequire(resolve(root, 'frontend/package.json'))('@playwright/test');
let browser, context, page, credentials = null;
let rendererEvidence = null;
const startRecords = [], snapshots = [], viewRecords = [], errors = [];
const capturedStages = new Set(), phases = new Set();
let streamEvidence = null;
let reconnected = false, followingCarrier = false, played = false, terminal = null;
let discoveredStartId = null;
let stopRequested = null;
let navigationStarted = false, needsLogin = false;
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { stopRequested = signal; });

async function screenshot(name) {
  // Password fields are also transparent throughout recording; clearing makes
  // screenshots safe even after an interrupted login.
  await page.locator('input[type="password"]').evaluateAll(inputs => {
    for (const input of inputs) input.value = '';
  });
  await page.screenshot({ path: resolve(out, name), mask: [page.locator('input[type="password"]:visible')] });
}
async function setControlsOpen(open) {
  const expanded = await page.evaluate(() => document.body.classList.contains('p02-controls-open'));
  if (expanded !== open) await page.getByRole('button', { name: '正式运行控制', exact: true }).click();
  if (await page.evaluate(() => document.body.classList.contains('p02-controls-open')) !== open) {
    throw new Error('Formal controls toggle did not reach the requested visible state');
  }
}
async function login() {
  await setControlsOpen(true);
  const token = page.getByLabel('引导操作员令牌', { exact: true });
  const csrf = page.getByLabel('引导 CSRF 令牌', { exact: true });
  for (const field of [token, csrf]) {
    if (await field.getAttribute('type') !== 'password') throw new Error('Bootstrap field is not a password input');
  }
  await page.getByLabel('控制服务地址', { exact: true }).fill(endpoint);
  await token.fill(bootstrap.bootstrap_token);
  await csrf.fill(bootstrap.bootstrap_csrf);
  const reply = page.waitForResponse(r => r.url() === `${endpoint}/v1/catalog`);
  await page.getByRole('button', { name: '加载运行目录', exact: true }).click();
  const response = await reply;
  await token.fill(''); await csrf.fill('');
  if (!response.ok()) throw new Error(`Catalog failed: HTTP ${response.status()}`);
  const catalog = await response.json();
  discoveredStartId = catalog.runs.find(run => run.run_id === runId)?.start_id ?? null;
}
async function start(name) {
  const reply = page.waitForResponse(r => r.url() === `${endpoint}/v1/runs` && r.request().method() === 'POST');
  await page.getByRole('button', { name, exact: true }).click();
  const response = await reply;
  const value = await response.json();
  if (!response.ok()) throw new Error(`Start failed: HTTP ${response.status()} ${value.detail?.code ?? 'missing error code'}`);
  if (value.run_id !== runId || value.snapshot?.run_id !== runId) throw new Error('Start returned a different run');
  credentials = value.credentials;
  if (!credentials?.operator_token || !credentials?.csrf_token) throw new Error('Start omitted actual run credentials');
  secrets.add(credentials.operator_token); secrets.add(credentials.csrf_token);
  const request = response.request().postDataJSON();
  if (typeof request.start_id !== 'string') throw new Error('Start omitted start_id');
  if (discoveredStartId !== null && request.start_id !== discoveredStartId) {
    throw new Error('Start changed the existing authenticated catalog identity');
  }
  const record = { run_id: value.run_id, start_id: request.start_id,
    phase: value.snapshot.phase, transition_sequence: value.snapshot.transition_sequence };
  startRecords.push(record); log(reconnected ? 'reconnected' : 'started', record);
  await setControlsOpen(false);
}
async function collectStream() {
  const batch = await page.evaluate(() => window.__parcelCapture.splice(0));
  for (const record of batch) {
    if (record.error) { errors.push(record); continue; }
    streamEvidence.append(record);
  }
}
async function observeView(tick) {
  const play = page.getByRole('button', { name: '▶ 播放', exact: true });
  if (await play.count() && await play.isEnabled()) await play.click();
  const playback = page.locator('[data-role="p02-live-play"]');
  if (await playback.count() && await playback.getAttribute('aria-pressed') === 'true') played = true;
  const actualMode = await page.locator('#city-map').getAttribute('data-observation-camera-mode');
  followingCarrier = actualMode === 'chase';
  if (!followingCarrier && await page.locator('#city-map').getAttribute('data-scene-ready') === 'true') {
    const carrier = page.locator('.operations-monitor-fleet-row[data-object-id="uav.p02.carrier"]');
    if (await carrier.count() && await carrier.isVisible()) {
      log('select-carrier-request', { tick, selector: 'operations-monitor-fleet-row' });
      await carrier.click();
      const follow = page.getByRole('button', { name: '外部跟随', exact: true });
      if (await follow.count() && await follow.isEnabled()) {
        await follow.click();
        followingCarrier = await page.locator('#city-map').getAttribute('data-observation-camera-mode') === 'chase';
        log('follow-carrier-request', { tick, actual_camera_mode: await page.locator('#city-map').getAttribute('data-observation-camera-mode') });
      }
    }
  }
  const view = await page.evaluate(() => ({
    clock: document.querySelector('.clock')?.textContent ?? null,
    timeline: document.querySelector('.timeline-head')?.textContent ?? null,
    orders: [...document.querySelectorAll('.p02-cargo-order')].map(row => ({
      cursor_tick: row.getAttribute('data-cursor-tick'), text: row.textContent,
    })),
    map: { ...document.querySelector('#city-map')?.dataset },
    control_map: { ...document.querySelector('.map')?.dataset },
    canvases: [...document.querySelectorAll('#city-map canvas')].map(canvas => ({
      width: canvas.width, height: canvas.height, client_width: canvas.clientWidth, client_height: canvas.clientHeight,
    })),
    playing: document.querySelector('[data-role="p02-live-play"][aria-pressed="true"]')?.textContent ?? null,
    source_notes: [...document.querySelectorAll('.source-note,.control-error,.map-message')].map(node => node.textContent),
    loading_progress: document.querySelector('.loading-progress:not([hidden])')?.textContent ?? null,
  }));
  const record = { ...view, runtime_tick: tick, elapsed_s: elapsed(), following_carrier: followingCarrier };
  viewRecords.push(record);
  const lastStage = streamEvidence.latestParcelRecord;
  const parcelState = lastStage?.payload.event.public_payload.find(field => field.name === 'parcel_state')?.value ?? null;
  const stage = `${parcelState}:${view.clock}`;
  if (tick !== null && followingCarrier && !capturedStages.has(stage)) {
    capturedStages.add(stage);
    await screenshot(`runtime-tick-${tick}-${viewRecords.length}.png`);
    log('motion-view', { tick, clock: view.clock, parcel_state: parcelState });
  }
}

async function captureSealedReplay(trace) {
  if (trace.run_id !== runId || trace.scene_states.length < 2) throw new Error('Sealed replay has no matching physical history');
  await setControlsOpen(true);
  const open = page.getByRole('button', { name: '加载封存回放', exact: true });
  await open.waitFor(); await open.click();
  log('sealed-replay-loading', { run_id: runId, scene_states: trace.scene_states.length });
  let progressKey = null;
  while (true) {
    if (stopRequested !== null) throw new Error(`Capture interrupted by ${stopRequested}; actual run remains unchanged`);
    if (performance.now() - started > timeoutMs) throw new Error('Sealed replay loading timed out');
    const loading = await page.evaluate(() => ({
      ready: document.querySelector('#city-map')?.dataset.sceneReady === 'true',
      tick: document.querySelector('#city-map')?.dataset.sceneTick ?? null,
      rendered_uav_count: document.querySelector('#city-map')?.dataset.renderedUavCount ?? null,
      provenance: document.querySelector('#mode-pill')?.dataset.provenance ?? null,
      source_key: document.querySelector('.operations-monitor-source')?.dataset.sourceKey ?? null,
      stage: document.querySelector('.loading-progress')?.getAttribute('data-stage') ?? null,
      text: document.querySelector('.loading-progress:not([hidden])')?.textContent ?? null,
      map: { ...document.querySelector('.map')?.dataset },
    }));
    if (loading.stage === 'failed') throw new Error(`Sealed replay load failed: ${loading.text}`);
    if (loading.text !== progressKey) { progressKey = loading.text; log('sealed-replay-progress', loading); }
    save('replay-loading-evidence.json', { run_id: runId, ...loading, elapsed_s: elapsed() });
    if (loading.stage === 'ready' && loading.ready && loading.tick === String(trace.scene_states[0].at.tick)
        && loading.rendered_uav_count === '1' && loading.provenance === 'recorded'
        && loading.source_key?.startsWith(`${runId}:`)) break;
    await page.waitForTimeout(5000);
  }
  await setControlsOpen(false);
  const play = page.getByRole('button', { name: '▶ 播放', exact: true });
  await play.click();
  const carrier = page.locator('.operations-monitor-fleet-row[data-object-id="uav.p02.carrier"]');
  await carrier.click();
  await page.getByRole('button', { name: '外部跟随', exact: true }).click();
  if (await page.locator('#city-map').getAttribute('data-observation-camera-mode') !== 'chase') {
    throw new Error('Sealed replay carrier follow did not become active');
  }
  const replayFrames = [], displayedTicks = new Set(), positions = new Set(), stages = new Set();
  let previousTick = null, previousState = null, previousMode = null;
  const lastTick = trace.scene_states.at(-1).at.tick;
  const replayStarted = elapsed();
  log('sealed-replay-playing', { run_id: runId, last_tick: lastTick, replay_video_start_s: replayStarted });
  while (true) {
    if (stopRequested !== null) throw new Error(`Capture interrupted by ${stopRequested}; actual run remains unchanged`);
    if (performance.now() - started > timeoutMs) throw new Error('Sealed playback timed out');
    const display = await page.evaluate(() => ({
      tick: document.querySelector('#city-map')?.dataset.sceneTick ?? null,
      mode: document.querySelector('#city-map')?.dataset.observationCameraMode ?? null,
      clock: document.querySelector('.clock')?.textContent ?? null,
      rendered_uav_count: document.querySelector('#city-map')?.dataset.renderedUavCount ?? null,
      playing: document.querySelector('[data-role="p02-live-play"]')?.getAttribute('aria-pressed') ?? null,
    }));
    if (display.tick !== null && display.tick !== '' && display.tick !== previousTick) {
      const tick = Number(display.tick);
      const scene = trace.scene_states.find(state => state.at.tick === tick);
      if (!scene) throw new Error('Displayed replay tick is absent from the sealed trace');
      const sample = scene.samples.find(item => item.entity_id === 'uav.p02.carrier');
      if (!sample || display.rendered_uav_count !== '1') throw new Error('Replay frame does not render its measured carrier');
      const parcel = trace.events.filter(event => event.event_type === 'public.parcel-projection' && event.at.tick <= tick).at(-1);
      const parcelState = parcel?.public_payload.find(field => field.name === 'parcel_state')?.value ?? null;
      displayedTicks.add(tick); positions.add(JSON.stringify(sample.pose.position.enu));
      if (parcelState !== null) stages.add(parcelState);
      const record = { ...display, tick, simulation_time_ns: scene.at.sim_time_ns,
        position_enu: sample.pose.position.enu, armed: sample.armed ?? null,
        flight_mode: sample.mode ?? null, contacts: sample.contacts ?? null,
        parcel_state: parcelState, parcel_event_id: parcel?.event_id ?? null, elapsed_s: elapsed() };
      replayFrames.push(record);
      if (parcelState !== previousState || sample.mode !== previousMode || tick % 20 === 0 || tick === lastTick) {
        await screenshot(`replay-tick-${tick}.png`); log('sealed-replay-frame', record);
      }
      previousTick = display.tick; previousState = parcelState; previousMode = sample.mode;
      save('replay-view-evidence.json', { run_id: runId, source: 'same-run sealed authoritative public trace',
        replay_video_start_s: replayStarted, frames: replayFrames });
      if (tick === lastTick) {
        const pause = page.getByRole('button', { name: '⏸ 暂停', exact: true });
        if (await pause.count()) await pause.click();
        await screenshot('replay-terminal.png');
        if (positions.size < 2 || displayedTicks.size < 2 || !stages.has('loaded') || !stages.has('delivered')) {
          throw new Error('Sealed replay lacks displayed measured motion, pickup or delivery');
        }
        save('replay-view-evidence.json', { run_id: runId, source: 'same-run sealed authoritative public trace',
          replay_video_start_s: replayStarted, replay_video_end_s: elapsed(), frames: replayFrames,
          displayed_stages: [...stages], displayed_tick_count: displayedTicks.size });
        break;
      }
    }
    await page.waitForTimeout(200);
  }
}

try {
  streamEvidence = new NativeParcelStreamEvidence({ runId, path: resolve(out, 'runtime-stream-evidence.jsonl'), sanitize });
  const rendererArgs = values.renderer === 'hardware'
    ? ['--use-gl=angle', '--use-angle=vulkan', '--enable-features=Vulkan', '--disable-vulkan-surface', '--enable-webgl']
    : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl'];
  browser = await chromium.launch({ channel: 'chromium', headless: true, args: rendererArgs });
  context = await browser.newContext({ viewport: { width: 1600, height: 1000 },
    recordVideo: { dir: resolve(out, 'runtime-video'), size: { width: 1600, height: 1000 } } });
  // Mirror only the public SSE envelopes consumed by the existing UI. Headers,
  // bootstrap responses and run access credentials are never recorded.
  await context.addInitScript(() => {
    window.__parcelCapture = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      const input = args[0];
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (/\/v1\/runs\/[0-9a-f]{64}\/events(?:\?|$)/.test(url) && response.ok) {
        const reader = response.clone().body.getReader();
        void (async () => {
          const decoder = new TextDecoder(); let pending = '';
          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              pending += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n');
              let end;
              while ((end = pending.indexOf('\n\n')) !== -1) {
                const frame = pending.slice(0, end); pending = pending.slice(end + 2);
                const lines = frame.split('\n');
                const data = lines.filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
                if (data) window.__parcelCapture.push({
                  event: lines.find(line => line.startsWith('event: '))?.slice(7) ?? null,
                  id: lines.find(line => line.startsWith('id: '))?.slice(4) ?? null,
                  payload: JSON.parse(data),
                });
              }
            }
          } catch (error) {
            // An explicit UI disconnect aborts its own fetch. Other stream
            // errors remain part of the capture evidence.
            if (error.name !== 'AbortError') window.__parcelCapture.push({ error: error.message });
          } finally { reader.releaseLock(); }
        })();
      }
      return response;
    };
  });
  page = await context.newPage(); page.setDefaultTimeout(requestTimeoutMs);
  rendererEvidence = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl');
    if (gl === null) throw new Error('Capture renderer has no WebGL context');
    const extension = gl.getExtension('WEBGL_debug_renderer_info');
    if (extension === null) throw new Error('Capture renderer omitted its actual device identity');
    const renderer = gl.getParameter(extension.UNMASKED_RENDERER_WEBGL);
    const version = gl.getParameter(gl.VERSION);
    const software = /SwiftShader|llvmpipe|software/i.test(renderer);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return { renderer, version, backend: software ? 'software' : 'hardware' };
  });
  if (rendererEvidence.backend !== values.renderer) {
    throw new Error(`Requested ${values.renderer} renderer but browser selected ${rendererEvidence.renderer}`);
  }
  log('renderer-selected', rendererEvidence);
  page.on('framenavigated', frame => {
    if (navigationStarted && frame === page.mainFrame()) needsLogin = true;
  });
  page.on('pageerror', error => {
    errors.push({ source: 'page', message: sanitize(error.message) });
    log('page-error', { message: error.message });
  });
  await page.goto(`${origin}/index.html?view=live&run=${runId}&compilation=${compiled.compilation_id}`);
  await page.addStyleTag({ content: 'input[type="password"] { opacity: 0 !important; }' });
  await page.getByRole('button', { name: '正式运行控制', exact: true }).click();
  await login();
  if (values.attach && discoveredStartId === null) throw new Error('--attach requires an existing authenticated catalog start identity');
  await start(discoveredStartId === null ? '启动运行' : '重连启动（复用已认证运行标识）');
  navigationStarted = true;
  while (true) {
    if (stopRequested !== null) throw new Error(`Capture interrupted by ${stopRequested}; actual run remains unchanged`);
    if (performance.now() - started > timeoutMs) throw new Error(`Capture exceeded ${values['timeout-seconds']} seconds`);
    if (needsLogin) {
      needsLogin = false; followingCarrier = false;
      await page.waitForLoadState('domcontentloaded');
      await page.addStyleTag({ content: 'input[type="password"] { opacity: 0 !important; }' });
      if (!await page.getByLabel('控制服务地址', { exact: true }).isVisible()) {
        await page.getByRole('button', { name: '正式运行控制', exact: true }).click();
      }
      await login();
      if (discoveredStartId !== startRecords[0].start_id) throw new Error('Reload lost the authenticated existing start identity');
      await start('重连启动（复用已认证运行标识）');
      log('page-reload-reconnected', { run_id: runId, start_id: discoveredStartId });
    }
    const response = await context.request.get(`${endpoint}/v1/runs/${runId}`, {
      timeout: Math.min(requestTimeoutMs, timeoutMs - (performance.now() - started)),
      headers: { Origin: origin, Authorization: `Bearer ${credentials.operator_token}` },
    });
    if (!response.ok()) throw new Error(`Status failed: HTTP ${response.status()}`);
    const value = await response.json(); const snapshot = value.snapshot;
    if (snapshot?.run_id !== runId) throw new Error('Status returned a different run');
    snapshots.push(snapshot);
    const tick = snapshot.runtime_control?.current?.tick ?? null;
    if (tick !== null && (!Number.isInteger(tick) || tick < 0)) throw new Error('Status returned an invalid tick');
    if (!phases.has(snapshot.phase)) { phases.add(snapshot.phase); log('phase', { phase: snapshot.phase, tick }); }
    await collectStream(); await observeView(tick);
    if (snapshot.summary !== null && snapshot.summary !== undefined) {
      terminal = snapshot; save('runtime-summary.json', snapshot.summary);
      let sealedTrace = null;
      if (snapshot.summary.public_trace !== null) {
        const traceResponse = await context.request.get(`${endpoint}/v1/runs/${runId}/public/trace`, {
          timeout: requestTimeoutMs, headers: { Origin: origin, Authorization: `Bearer ${credentials.operator_token}` },
        });
        if (!traceResponse.ok()) throw new Error(`Sealed public trace failed: HTTP ${traceResponse.status()}`);
        const traceBytes = await traceResponse.body();
        sealedTrace = JSON.parse(traceBytes.toString('utf8'));
        // The public endpoint returns authoritative sealed bytes. Preserve
        // their exact float spelling and digest; diagnostic exports may use JSON.
        writeFileSync(resolve(out, 'public-trace.json'), traceBytes);
      }
      if (values.replay) {
        if (snapshot.summary.status !== 'passed') throw new Error(`Runtime terminal status: ${snapshot.summary.status}`);
        if (sealedTrace === null) throw new Error('Completed run has no sealed authoritative trace');
        await captureSealedReplay(sealedTrace);
        log('sealed-replay-complete', { run_id: runId, status: snapshot.summary.status });
        break;
      }
      // Let actual playback reach the final recorded UI frame before ending
      // video recording. The sealed trace remains separate from live capture.
      await page.waitForTimeout(Math.min(pollMs, 5000));
      const last = page.getByRole('button', { name: '尾帧 ⏭', exact: true });
      if (await last.count() && await last.isEnabled()) await last.click();
      await collectStream(); await observeView(tick); await screenshot('runtime-terminal.png');
      log('terminal', { phase: snapshot.phase, status: snapshot.summary.status,
        failure_classes: snapshot.failure_classes, reconnected });
      if (snapshot.summary.status !== 'passed') throw new Error(`Runtime terminal status: ${snapshot.summary.status}`);
      if (!reconnected || !followingCarrier || !played) throw new Error('Viewer capture lacks reconnect, carrier follow or actual playback');
      const clocks = new Set(viewRecords.map(view => view.clock).filter(clock => clock !== null));
      const displayedPositions = new Set(viewRecords.filter(view => view.map.sceneReady === 'true'
          && view.map.sceneTick !== undefined && view.map.sceneTick !== '')
        .map(view => streamEvidence.carrierPositionsByTick.get(Number(view.map.sceneTick)))
        .filter(position => position !== undefined));
      if (clocks.size < 2 || displayedPositions.size < 2) throw new Error('Viewer capture lacks advancing displayed frames or displayed measured carrier motion');
      break;
    }
    if (tick !== null && tick >= reconnectTick && !reconnected) {
      await screenshot('runtime-before-disconnect.png');
      await setControlsOpen(true);
      await page.getByRole('button', { name: '断开并清除凭据', exact: true }).click();
      credentials = null;
      await screenshot('runtime-disconnected.png');
      reconnected = true; followingCarrier = false;
      await login(); await start('重连启动（复用已认证运行标识）');
      if (startRecords[0].run_id !== startRecords[1].run_id
          || startRecords[0].start_id !== startRecords[1].start_id) throw new Error('Reconnect changed run_id or authenticated start_id');
      await screenshot('runtime-reconnected.png');
    }
    save('runtime-control-evidence.json', { run_id: runId, compilation_id: compiled.compilation_id,
      renderer: rendererEvidence, stream: streamEvidence?.summary() ?? null, start_records: startRecords, snapshots, view_records: viewRecords, reconnected, errors, elapsed_s: elapsed() });
    await page.waitForTimeout(pollMs);
  }
} catch (error) {
  errors.push({ source: 'capture', name: error.name, message: sanitize(error.message) });
  if (page && !page.isClosed()) {
    try { await screenshot('runtime-error.png'); } catch (captureError) {
      errors.push({ source: 'error-screenshot', message: sanitize(captureError.message) });
    }
  }
  log('capture-failed', { message: error.message }); process.exitCode = 1;
} finally {
  if (page && !page.isClosed()) {
    try { await collectStream(); } catch (error) { errors.push({ source: 'final-stream', message: sanitize(error.message) }); process.exitCode = 1; }
  }
  save('runtime-control-evidence.json', { run_id: runId, compilation_id: compiled.compilation_id,
    renderer: rendererEvidence, stream: streamEvidence?.summary() ?? null, start_records: startRecords, snapshots, view_records: viewRecords, reconnected,
    terminal_status: terminal?.summary?.status ?? null, elapsed_s: elapsed(), errors });
  streamEvidence?.close();
  await context?.close(); await browser?.close();
}
