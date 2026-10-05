/**
 * Measure one versus two real local-reflection probes on the loaded city scene, then capture
 * a moving day/night and dry/rain sequence. This script does not enable a second production
 * probe. It temporarily replaces the production owner with two disjoint measurement owners,
 * disposes them, and restores the one-probe presentation setting before exit.
 *
 * Usage:
 *   node frontend/scripts/measure-city-reflection-probes.mjs \
 *     --origin=http://127.0.0.1:5393 \
 *     --output=validation/codex-takeover-20261001/R \
 *     --cameras=validation/platform-plan-20261001/E1-browser-v2/cameras.json \
 *     --host-condition=uncontended
 */
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { cpus, freemem, loadavg, totalmem } from "node:os";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { chromium } from "playwright";

const execFileAsync = promisify(execFile);

const GPU_ARGS = [
  "--enable-gpu",
  "--use-angle=vulkan",
  "--enable-features=Vulkan",
  "--disable-vulkan-surface",
  "--disable-software-rasterizer",
  "--ignore-gpu-blocklist",
  "--disable-background-timer-throttling",
  "--disable-renderer-backgrounding",
];

function parseArgs(argv) {
  const result = {};
  for (const argument of argv) {
    const match = /^--([^=]+)=(.*)$/.exec(argument);
    if (match === null) throw new Error(`Arguments must use --name=value form: ${argument}`);
    result[match[1]] = match[2];
  }
  return result;
}

function integerOption(options, name, fallback, minimum) {
  const value = Number(options[name] ?? fallback);
  if (!Number.isInteger(value) || value < minimum) {
    throw new Error(`--${name} must be an integer >= ${minimum}, got ${options[name]}`);
  }
  return value;
}

function percentile(values, fraction) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const rank = fraction * (sorted.length - 1);
  const lower = Math.floor(rank), upper = Math.ceil(rank);
  return lower === upper ? sorted[lower]
    : sorted[lower] * (upper - rank) + sorted[upper] * (rank - lower);
}

function summary(values) {
  if (values.length === 0 || values.some(value => !Number.isFinite(value))) return null;
  return {
    count: values.length,
    min: Math.min(...values),
    p50: percentile(values, 0.5),
    p95: percentile(values, 0.95),
    max: Math.max(...values),
    mean: values.reduce((total, value) => total + value, 0) / values.length,
  };
}

function rounded(value, digits = 4) {
  if (typeof value === "number") return Number(value.toFixed(digits));
  if (Array.isArray(value)) return value.map(entry => rounded(entry, digits));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, rounded(entry, digits)]));
  }
  return value;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function hostObservation() {
  const observation = {
    logicalCpuCount: cpus().length,
    loadAverage: loadavg(),
    memory: { freeBytes: freemem(), totalBytes: totalmem() },
    gpus: [],
  };
  try {
    const { stdout } = await execFileAsync("nvidia-smi", [
      "--query-gpu=index,name,driver_version,utilization.gpu,utilization.memory,memory.used,memory.total,pstate",
      "--format=csv,noheader,nounits",
    ], { timeout: 10_000 });
    observation.gpus = stdout.trim().split("\n").filter(Boolean).map(line => {
      const [index, name, driverVersion, gpuUtilizationPercent, memoryUtilizationPercent,
        usedMemoryMiB, totalMemoryMiB, performanceState] = line.split(",").map(value => value.trim());
      return { index: Number(index), name, driverVersion,
        gpuUtilizationPercent: Number(gpuUtilizationPercent),
        memoryUtilizationPercent: Number(memoryUtilizationPercent),
        usedMemoryMiB: Number(usedMemoryMiB), totalMemoryMiB: Number(totalMemoryMiB), performanceState };
    });
  } catch (error) {
    observation.gpuQueryFailure = error instanceof Error ? error.message : String(error);
  }
  return observation;
}

function texturePayloadCeiling(profile, target) {
  let edge = profile.probeSizePx, texelsPerFaceWithMips = 0;
  while (edge >= 1) {
    texelsPerFaceWithMips += edge * edge;
    edge = Math.floor(edge / 2);
  }
  const halfFloatRgbaBytes = 8;
  const assumedDepth32Bytes = 4;
  const cubeColorBytes = profile.cubeFacesPerRefresh * texelsPerFaceWithMips * halfFloatRgbaBytes;
  const cubeDepthBytes = profile.cubeFacesPerRefresh * profile.probeSizePx ** 2 * assumedDepth32Bytes;
  const pmremOutputBytes = target.filteredWidthPx * target.filteredHeightPx * halfFloatRgbaBytes;
  const pmremPingPongBytes = pmremOutputBytes;
  const perProbeBytes = cubeColorBytes + cubeDepthBytes + pmremOutputBytes + pmremPingPongBytes;
  return {
    assumptions: {
      cubeColor: "six RGBA16F faces with a complete mip chain",
      cubeDepth: "one 32-bit depth value per base-level face pixel",
      pmrem: "one RGBA16F CubeUV output plus one equal-sized PMREM ping-pong target",
      exclusions: "driver alignment, metadata, shader programs, geometry and transient command storage",
    },
    perProbe: {
      cubeColorBytes,
      cubeDepthBytes,
      pmremOutputBytes,
      pmremPingPongBytes,
      accountedTexturePayloadCeilingBytes: perProbeBytes,
      accountedTexturePayloadCeilingMiB: perProbeBytes / 1048576,
    },
    twoProbes: {
      accountedTexturePayloadCeilingBytes: perProbeBytes * 2,
      accountedTexturePayloadCeilingMiB: perProbeBytes * 2 / 1048576,
    },
  };
}

async function hookMap(page) {
  let hooked = null;
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await fetch(route.request().url());
    if (!response.ok) throw new Error(`Reflection measurement app hook failed: HTTP ${response.status}`);
    const source = await response.text();
    const token = "this.map = new PublicTraceMap(";
    if (source.split(token).length !== 2) {
      throw new Error("Reflection measurement source map hook no longer matches exactly once");
    }
    hooked = new URL(route.request().url()).pathname;
    await route.fulfill({ status: response.status, contentType: "text/javascript", body: source.replace(token,
      "window.__aeroVisualMap = this.map = new PublicTraceMap(") });
  });
  await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
    const response = await fetch(route.request().url());
    if (!response.ok) throw new Error(`Reflection measurement bundle hook failed: HTTP ${response.status}`);
    const source = await response.text();
    const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
    if (!pattern.test(source)) throw new Error("Reflection measurement bundle map hook no longer matches");
    hooked = new URL(route.request().url()).pathname;
    await route.fulfill({ status: response.status, contentType: "text/javascript", body: source.replace(pattern,
      match => match.replace("this.map=", "window.__aeroVisualMap=this.map=")) });
  });
  return () => hooked;
}

async function waitForScene(page) {
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return (data?.sceneReady === "true" && data?.skyReady === "true"
      && data?.texturesReady === "true") || data?.sceneError === "true";
  }, undefined, { timeout: 300000, polling: 250 });
  const error = await page.locator("#city-map").getAttribute("data-scene-error-message");
  if (error) throw new Error(`City scene failed: ${error}`);
  await page.locator("#city-map .city-scene-loading").waitFor({ state: "hidden", timeout: 300000 });
  await page.waitForFunction(() => window.__aeroVisualMap !== undefined
      && (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
    undefined, { timeout: 300000, polling: 250 });
}

async function screenshot(page, output, name, details) {
  const path = resolve(output, `${name}.png`);
  await page.locator("#city-map canvas").first().screenshot({ path });
  const bytes = await readFile(path);
  return { file: `${name}.png`, sha256: sha256(bytes), sizeBytes: bytes.length, ...details };
}

const sourceFiles = [
  "src/app.ts",
  "src/map.ts",
  "src/city-local-reflections.ts",
  "src/city-lighting.ts",
  "src/city-presentation.ts",
  "src/city-weather.ts",
  "src/city-render-weather-controls.ts",
];
const frontendRoot = resolve(new URL("..", import.meta.url).pathname);
async function hashSources() {
  return Object.fromEntries(await Promise.all(sourceFiles.map(async file => {
    const bytes = await readFile(resolve(frontendRoot, file));
    return [file, sha256(bytes)];
  })));
}

const options = parseArgs(process.argv.slice(2));
const origin = options.origin ?? "http://127.0.0.1:5393";
const output = resolve(options.output ?? "validation/codex-takeover-20261001/R");
const scenePath = options.scene ?? "/city-presentation/default-scene-v1.json";
const camerasPath = resolve(options.cameras
  ?? "validation/platform-plan-20261001/E1-browser-v2/cameras.json");
const hostCondition = options["host-condition"] ?? "unassessed";
if (!["unassessed", "contended", "uncontended"].includes(hostCondition)) {
  throw new Error(`--host-condition must be unassessed, contended, or uncontended, got ${hostCondition}`);
}
const trials = integerOption(options, "trials", 7, 5);
const framesPerState = integerOption(options, "video-frames-per-state", 30, 15);
const camerasBytes = await readFile(camerasPath);
const cameras = JSON.parse(camerasBytes.toString("utf8"));
const harnessSha256 = sha256(await readFile(new URL(import.meta.url)));
const firstView = cameras.find(camera => camera.name === "street-1");
const secondView = cameras.find(camera => camera.name === "street-3");
if (firstView?.edge === undefined || secondView?.edge === undefined) {
  throw new Error("Stored street-1 and street-3 road-edge cameras are required");
}

await mkdir(output, { recursive: true });
const hostBeforeBrowser = await hostObservation();
const sourceHashesAtStart = await hashSources();
const browser = await chromium.launch({ channel: "chromium", headless: true, args: GPU_ARGS });
const context = await browser.newContext({
  acceptDownloads: true,
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 1.5,
});
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));
const navigations = [];
page.on("framenavigated", frame => {
  if (frame === page.mainFrame()) navigations.push(frame.url());
});
const getHookedPath = await hookMap(page);
const runStarted = performance.now();
const report = {
  status: "RUNNING",
  scope: "Actual loaded-scene local-reflection transition and two-probe diagnostic",
  origin,
  scenePath,
  camerasFile: camerasPath,
  camerasSha256: sha256(camerasBytes),
  harnessSha256,
  hostCondition,
  hostBeforeBrowser,
  sourceHashesAtStart,
  physicalOrSimulationParametersChanged: false,
  productionPolicy: "One local reflection probe; the second probe exists only inside this measurement",
  screenSpaceReflections: "not implemented or measured",
  pageErrors,
};

try {
  const loadStarted = performance.now();
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`,
    { waitUntil: "domcontentloaded", timeout: 300000 });
  await waitForScene(page);
  report.sceneLoadSeconds = (performance.now() - loadStarted) / 1000;
  report.publishedPath = getHookedPath();
  if (report.publishedPath === null) throw new Error("Reflection measurement did not expose the loaded map owner");

  // The reflection owner reads `buildings.userData.windowMaterials`, which the whole-group
  // calibration writes. Streamed buildings are calibrated per visual, so at startup the
  // group-level registry can be empty and the production probe then owns no facades.
  // Record that state, then re-apply the current time of day through the production
  // presentation path (the same path a user time/weather change takes) once streaming
  // around the first camera has settled, and record the registry again.
  await page.evaluate(({ firstView }) => {
    const map = window.__aeroVisualMap;
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation);
    map.previewAnimation = 0;
    map.previewFollowId = null;
    map.setCameraMode("free");
    map.camera.position.set(...firstView.eye);
    map.controls.target.set(...firstView.target);
    map.controls.update();
    map.renderStreamer?.update(map.camera);
  }, { firstView });
  await page.waitForFunction(() => (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
    undefined, { timeout: 300000, polling: 250 });
  report.facadeRegistry = await page.evaluate(() => {
    const map = window.__aeroVisualMap;
    const registry = () => {
      const materials = map.buildingPresentation?.userData.windowMaterials;
      return { windowMaterials: Array.isArray(materials) ? materials.length : null,
        productionProbeSelectedBuildings: map.localReflections?.selectedBuildings().length ?? null,
        loadedBuildingRoots: map.buildingPresentation?.children.length ?? null };
    };
    const atStartup = registry();
    const timeOfDay = map.root.dataset.cityTimeOfDay;
    map.configureCityPresentation({ timeOfDay });
    return { atStartup, reappliedTimeOfDay: timeOfDay, afterProductionReapply: registry() };
  });
  if (!(report.facadeRegistry.afterProductionReapply.windowMaterials > 0)
      || !(report.facadeRegistry.afterProductionReapply.productionProbeSelectedBuildings > 0)) {
    throw new Error(`Production reflection owner did not bind the streamed facades after the presentation re-apply: ${JSON.stringify(report.facadeRegistry)}`);
  }

  const setup = await page.evaluate(({ firstView, secondView }) => {
    const map = window.__aeroVisualMap;
    if (map?.localReflections === null || map?.buildingPresentation === null) {
      throw new Error("Loaded scene has no production reflection owner or building presentation");
    }
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation);
    map.previewAnimation = 0;
    map.previewFollowId = null;
    map.setCameraMode("free");
    map.camera.position.set(...firstView.eye);
    map.controls.target.set(...firstView.target);
    map.controls.update();
    map.renderStreamer?.update(map.camera);

    const Probe = map.localReflections.constructor;
    const productionEnabled = map.presentationOptions.reflectionsEnabled ?? true;
    map.configureCityPresentation({ reflectionsEnabled: false });
    const first = new Probe(map.renderer, map.scene);
    const firstFocus = map.previewReflectionFocus.clone().set(firstView.target[0], 0, firstView.target[2]);
    first.configure(map.buildingPresentation, firstFocus, true);
    const firstSelection = first.selectedBuildings();
    if (firstSelection.length === 0) throw new Error("First road-edge focus selected no reflective buildings");
    const second = new Probe(map.renderer, map.scene, { excludeBuildings: new Set(firstSelection) });
    const secondFocus = firstFocus.clone().set(secondView.target[0], 0, secondView.target[2]);
    second.configure(map.buildingPresentation, secondFocus, true);
    const secondSelection = second.selectedBuildings();
    if (secondSelection.length === 0) throw new Error("Second road-edge focus selected no reflective buildings");
    const overlap = secondSelection.filter(building => firstSelection.includes(building));
    if (overlap.length > 0) throw new Error("Two-probe selection contains overlapping building owners");

    const gl = map.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    const timer = gl.getExtension("EXT_disjoint_timer_query_webgl2");
    if (timer === null) throw new Error("EXT_disjoint_timer_query_webgl2 is required for probe measurement");
    window.__reflectionMeasurement = { map, first, second, timer, productionEnabled };
    const collisionId = building => building.userData.collisionBox?.building_id ?? null;
    return {
      vendor: String(gl.getParameter(debug?.UNMASKED_VENDOR_WEBGL ?? gl.VENDOR)),
      renderer: String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER)),
      webglVersion: String(gl.getParameter(gl.VERSION)),
      timerExtension: "EXT_disjoint_timer_query_webgl2",
      profile: { ...Probe.profile },
      first: { focus: firstFocus.toArray(), selectionCount: firstSelection.length,
        buildingIds: firstSelection.map(collisionId), objectUuids: firstSelection.map(item => item.uuid) },
      second: { focus: secondFocus.toArray(), selectionCount: secondSelection.length,
        buildingIds: secondSelection.map(collisionId), objectUuids: secondSelection.map(item => item.uuid) },
      focusSeparationM: firstFocus.distanceTo(secondFocus),
      textureCountBeforeWarmup: map.renderer.info.memory.textures,
    };
  }, { firstView, secondView });
  if (/swiftshader|llvmpipe|software/i.test(setup.renderer)) {
    throw new Error(`Hardware GPU is required; browser reported ${setup.renderer}`);
  }
  if (setup.focusSeparationM <= setup.profile.reflectiveRadiusM * 2) {
    throw new Error(`Probe focuses are only ${setup.focusSeparationM} m apart`);
  }
  report.runtime = setup;

  const measurement = await page.evaluate(async ({ trials }) => {
    const state = window.__reflectionMeasurement;
    const { map, first, second, timer } = state;
    const gl = map.renderer.getContext();
    const memory = { beforeWarmup: { ...map.renderer.info.memory } };
    first.invalidate(); first.refresh(); gl.finish();
    memory.afterFirstWarmup = { ...map.renderer.info.memory };
    second.invalidate(); second.refresh(); gl.finish();
    memory.afterSecondWarmup = { ...map.renderer.info.memory };

    const timerQuery = async probes => {
      if (gl.getParameter(timer.GPU_DISJOINT_EXT)) {
        throw new Error("GPU timer was disjoint before a reflection trial");
      }
      const query = gl.createQuery();
      if (query === null) throw new Error("WebGL failed to create a reflection timer query");
      for (const probe of probes) probe.invalidate();
      gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
      const started = performance.now();
      const refreshed = probes.map(probe => probe.refresh());
      const cpuSubmitMs = performance.now() - started;
      gl.endQuery(timer.TIME_ELAPSED_EXT);
      gl.flush();
      const deadline = performance.now() + 15000;
      while (!gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) {
        if (performance.now() >= deadline) {
          gl.deleteQuery(query);
          throw new Error("Reflection GPU timer query did not resolve within 15 seconds");
        }
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      if (gl.getParameter(timer.GPU_DISJOINT_EXT)) {
        gl.deleteQuery(query);
        throw new Error("GPU timer became disjoint during a reflection trial");
      }
      const gpuMs = Number(gl.getQueryParameter(query, gl.QUERY_RESULT)) / 1e6;
      gl.deleteQuery(query);
      if (!refreshed.every(Boolean)) throw new Error("A dirty reflection probe did not refresh");
      return { cpuSubmitMs, gpuMs, probeCount: probes.length,
        cubeFaceCount: probes.length * first.constructor.profile.cubeFacesPerRefresh };
    };

    const samples = [];
    for (let trial = 1; trial <= trials; trial++) {
      const order = trial % 2 === 0 ? [[first, second], [first]] : [[first], [first, second]];
      for (const probes of order) samples.push({ trial, ...(await timerQuery(probes)) });
    }
    return {
      memory,
      samples,
      target: {
        cubeWidthPx: first.target.width,
        cubeHeightPx: first.target.height,
        filteredWidthPx: first.filtered.width,
        filteredHeightPx: first.filtered.height,
        firstCubeTextureUuid: first.target.texture.uuid,
        secondCubeTextureUuid: second.target.texture.uuid,
        firstFilteredTextureUuid: first.filtered.texture.uuid,
        secondFilteredTextureUuid: second.filtered.texture.uuid,
        halfFloat: first.target.texture.type === 1016,
        mipmaps: first.target.texture.generateMipmaps,
        depthBuffer: first.target.depthBuffer,
      },
    };
  }, { trials });
  if (measurement.target.cubeWidthPx !== setup.profile.probeSizePx
      || measurement.target.cubeHeightPx !== setup.profile.probeSizePx
      || !measurement.target.halfFloat || !measurement.target.mipmaps || !measurement.target.depthBuffer) {
    throw new Error(`Reflection target does not match the declared profile: ${JSON.stringify(measurement.target)}`);
  }
  if (measurement.target.firstCubeTextureUuid === measurement.target.secondCubeTextureUuid
      || measurement.target.firstFilteredTextureUuid === measurement.target.secondFilteredTextureUuid) {
    throw new Error("The two measurement probes did not allocate distinct reflection textures");
  }
  report.measurement = measurement;
  const one = measurement.samples.filter(sample => sample.probeCount === 1);
  const two = measurement.samples.filter(sample => sample.probeCount === 2);
  report.cost = rounded({
    trials,
    oneProbe: { gpuMs: summary(one.map(sample => sample.gpuMs)),
      cpuSubmitMs: summary(one.map(sample => sample.cpuSubmitMs)), cubeFacesPerTrial: 6 },
    twoProbes: { gpuMs: summary(two.map(sample => sample.gpuMs)),
      cpuSubmitMs: summary(two.map(sample => sample.cpuSubmitMs)), cubeFacesPerTrial: 12 },
    incrementalSecondProbe: {
      gpuP50Ms: percentile(two.map(sample => sample.gpuMs), 0.5)
        - percentile(one.map(sample => sample.gpuMs), 0.5),
      cpuSubmitP50Ms: percentile(two.map(sample => sample.cpuSubmitMs), 0.5)
        - percentile(one.map(sample => sample.cpuSubmitMs), 0.5),
    },
  });
  report.theoreticalResources = rounded(texturePayloadCeiling(setup.profile, measurement.target));
  report.geometricLimits = {
    selectedReflectiveBuildingsPerProbe: `at most ${setup.profile.maxReflectiveBuildings}`,
    reflectiveSelectionRadiusM: setup.profile.reflectiveRadiusM,
    cubeCaptureFarM: setup.profile.captureFarM,
    cubeFacesPerDirtyProbe: setup.profile.cubeFacesPerRefresh,
    twoProbeFacesPerDirtyTransition: setup.profile.cubeFacesPerRefresh * 2,
    captureScope: "Each cube face renders visible default-layer scene geometry inside the cube camera frustum; the building cap limits enhanced facade ownership, not draw visibility.",
  };

  await page.evaluate(() => {
    const state = window.__reflectionMeasurement;
    state.second.dispose();
    state.second = null;
  });
  const captures = [];
  for (const timeOfDay of ["day", "night"]) {
    for (const weather of ["clear", "rain"]) {
      const measured = await page.evaluate(({ firstView, timeOfDay, weather }) => {
        const { map, first } = window.__reflectionMeasurement;
        map.previewPlaying = false;
        cancelAnimationFrame(map.previewAnimation);
        map.previewAnimation = 0;
        map.camera.position.set(...firstView.eye);
        map.controls.target.set(...firstView.target);
        map.controls.update();
        map.configureCityPresentation({ timeOfDay, reflectionsEnabled: false });
        const preset = map.root.querySelector("[name='render-weather-preset']");
        if (!(preset instanceof HTMLSelectElement)) throw new Error("Visual weather preset control is absent");
        preset.value = weather;
        preset.dispatchEvent(new Event("change", { bubbles: true }));
        first.invalidate();
        if (!first.refresh()) throw new Error("Time/weather transition did not refresh the local probe");
        map.previewSeconds = 34;
        map.renderPreviewFrame();
        const gl = map.renderer.getContext();
        gl.finish();
        const pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
        gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        let luminance = 0;
        for (let index = 0; index < pixels.length; index += 4) {
          luminance += pixels[index] * 0.2126 + pixels[index + 1] * 0.7152 + pixels[index + 2] * 0.0722;
        }
        return {
          requested: { timeOfDay, weather },
          actual: { timeOfDay: map.root.dataset.cityTimeOfDay,
            weather: preset.value, wetness: map.groundWetness.uWetness.value },
          camera: { eye: map.camera.position.toArray(), target: map.controls.target.toArray() },
          meanSrgbCodeLuminance: luminance / (pixels.length / 4) / 255,
          drawingBuffer: { width: gl.drawingBufferWidth, height: gl.drawingBufferHeight },
        };
      }, { firstView, timeOfDay, weather });
      if (measured.actual.timeOfDay !== timeOfDay || measured.actual.weather !== weather) {
        throw new Error(`Viewer did not apply ${timeOfDay}/${weather}`);
      }
      if (weather === "clear" ? measured.actual.wetness !== 0 : measured.actual.wetness <= 0.5) {
        throw new Error(`Viewer reported unexpected ${weather} wetness ${measured.actual.wetness}`);
      }
      captures.push(await screenshot(page, output, `reflection-${timeOfDay}-${weather}`, measured));
    }
  }
  if (new Set(captures.map(capture => capture.sha256)).size !== captures.length) {
    throw new Error("One or more matched reflection frames are byte-identical");
  }
  for (const weather of ["clear", "rain"]) {
    const day = captures.find(capture => capture.requested.timeOfDay === "day"
      && capture.requested.weather === weather);
    const night = captures.find(capture => capture.requested.timeOfDay === "night"
      && capture.requested.weather === weather);
    if (day.meanSrgbCodeLuminance <= night.meanSrgbCodeLuminance + 0.01) {
      throw new Error(`${weather} day/night luminance separation is below 0.01`);
    }
  }
  report.captures = captures;

  const [download, videoStats] = await Promise.all([
    page.waitForEvent("download", { timeout: 120000 }),
    page.evaluate(async ({ firstView, framesPerState }) => {
    const { map, first } = window.__reflectionMeasurement;
    const canvas = map.renderer.domElement;
    if (typeof canvas.captureStream !== "function" || typeof MediaRecorder !== "function") {
      throw new Error("Canvas MediaRecorder is unavailable");
    }
    const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9" : "video/webm";
    const stream = canvas.captureStream(30);
    const chunks = [];
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 8_000_000 });
    recorder.addEventListener("dataavailable", event => { if (event.data.size > 0) chunks.push(event.data); });
    const stopped = new Promise((resolve, reject) => {
      recorder.addEventListener("stop", resolve, { once: true });
      recorder.addEventListener("error", event => reject(event.error), { once: true });
    });
    const states = [
      { timeOfDay: "day", weather: "clear" },
      { timeOfDay: "day", weather: "rain" },
      { timeOfDay: "night", weather: "rain" },
      { timeOfDay: "night", weather: "clear" },
    ];
    const samples = [];
    const recordingStarted = performance.now();
    recorder.start(250);
    const target = map.previewReflectionFocus.clone().set(...firstView.target);
    const offset = map.camera.position.clone().set(...firstView.eye).sub(target);
    for (let stateIndex = 0; stateIndex < states.length; stateIndex++) {
      const state = states[stateIndex];
      map.configureCityPresentation({ timeOfDay: state.timeOfDay, reflectionsEnabled: false });
      const preset = map.root.querySelector("[name='render-weather-preset']");
      preset.value = state.weather;
      preset.dispatchEvent(new Event("change", { bubbles: true }));
      first.invalidate();
      if (!first.refresh()) throw new Error("Recorded transition did not refresh the local probe");
      for (let frame = 0; frame < framesPerState; frame++) {
        const fraction = (stateIndex * framesPerState + frame)
          / (states.length * framesPerState - 1);
        const angle = (fraction - 0.5) * 0.24;
        map.camera.position.set(
          target.x + offset.x * Math.cos(angle) - offset.z * Math.sin(angle),
          firstView.eye[1] + Math.sin(fraction * Math.PI) * 1.5,
          target.z + offset.x * Math.sin(angle) + offset.z * Math.cos(angle),
        );
        map.controls.target.copy(target);
        map.controls.update();
        map.previewSeconds += 1 / 30;
        map.renderPreviewFrame();
        await new Promise(resolve => requestAnimationFrame(resolve));
      }
      samples.push({ ...state, eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        wetness: map.groundWetness.uWetness.value });
    }
    recorder.stop();
    await stopped;
    const recordingElapsedMs = performance.now() - recordingStarted;
    const trackSettings = stream.getVideoTracks()[0]?.getSettings() ?? {};
    for (const track of stream.getTracks()) track.stop();
    const blob = new Blob(chunks, { type: mimeType });
    if (blob.size === 0) throw new Error("Reflection movement recording is empty");
    const href = URL.createObjectURL(blob);
    const decoded = await new Promise((resolve, reject) => {
      const video = document.createElement("video");
      const timeout = setTimeout(() => reject(new Error("Recorded WebM metadata did not load")), 15000);
      video.preload = "metadata";
      video.addEventListener("loadedmetadata", () => {
        clearTimeout(timeout);
        resolve({ width: video.videoWidth, height: video.videoHeight,
          durationSeconds: Number.isFinite(video.duration) ? video.duration : null });
      }, { once: true });
      video.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("Recorded WebM could not be decoded by the producing browser"));
      }, { once: true });
      video.src = href;
    });
    if (!(decoded.width > 0) || !(decoded.height > 0)) {
      throw new Error(`Recorded WebM has invalid decoded dimensions ${decoded.width}x${decoded.height}`);
    }
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = "reflection-weather-time.webm";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return { mimeType, sizeBytes: blob.size, framesPerState, requestedFrames: framesPerState * states.length,
      recordingElapsedMs, trackSettings, decoded, states: samples };
    }, { firstView, framesPerState }),
  ]);
  const videoPath = resolve(output, "reflection-weather-time.webm");
  await download.saveAs(videoPath);
  const videoBytes = await readFile(videoPath);
  report.video = { file: "reflection-weather-time.webm", sha256: sha256(videoBytes),
    savedSizeBytes: videoBytes.length, ...videoStats };
  if (videoBytes.length !== videoStats.sizeBytes) {
    throw new Error(`Saved video size ${videoBytes.length} differs from recorded blob ${videoStats.sizeBytes}`);
  }

  report.sourceHashesAtEnd = await hashSources();
  if (JSON.stringify(report.sourceHashesAtEnd) !== JSON.stringify(sourceHashesAtStart)) {
    throw new Error("A measurement source file changed while the browser run was active");
  }
  report.mainFrameNavigations = navigations;
  if (navigations.length !== 1) {
    throw new Error(`The measurement page navigated ${navigations.length} times; expected one initial load`);
  }
  if (pageErrors.length > 0) throw new Error(`${pageErrors.length} page error(s) occurred`);
  report.acceptanceUse = hostCondition === "uncontended"
    ? "PROVISIONAL_PENDING_AN_INDEPENDENT_UNCONTENDED_CONFIRMATION"
    : "DIAGNOSTIC_ONLY_PENDING_AN_UNCONTENDED_RUN";
  report.secondProbeDecision = "No production enablement. Compare the measured incremental cost with the main U2 budget after an independent uncontended run.";
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL";
  report.failure = error instanceof Error ? error.stack ?? error.message : String(error);
  throw error;
} finally {
  try {
    report.cleanup = await page.evaluate(() => {
      const state = window.__reflectionMeasurement;
      if (state === undefined) return { measurementStateCreated: false };
      state.second?.dispose();
      state.first?.dispose();
      state.map.configureCityPresentation({ reflectionsEnabled: state.productionEnabled });
      delete window.__reflectionMeasurement;
      const selectedBuildings = state.map.localReflections?.selectedBuildings().length ?? null;
      if (state.productionEnabled && !(selectedBuildings > 0)) {
        throw new Error("Production one-probe owner was not restored after the measurement");
      }
      return { measurementStateCreated: true, productionReflectionsEnabled: state.productionEnabled,
        productionProbeSelectedBuildings: selectedBuildings,
        rendererTextureCount: state.map.renderer.info.memory.textures };
    });
  } catch (cleanupError) {
    report.cleanupFailure = cleanupError instanceof Error ? cleanupError.message : String(cleanupError);
    if (report.status === "PASS") report.status = "FAIL";
  }
  report.elapsedSeconds = (performance.now() - runStarted) / 1000;
  await writeFile(resolve(output, "report.json"), `${JSON.stringify(rounded(report), null, 2)}\n`);
  await page.close();
  await context.close();
  await browser.close();
  if (report.status !== "PASS") process.exitCode = 1;
  console.log(JSON.stringify({ status: report.status, output, cost: report.cost ?? null,
    video: report.video?.file ?? null }));
}
