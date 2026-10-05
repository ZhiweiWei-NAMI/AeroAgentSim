/**
 * Browser acceptance for the completed city platform presentation.
 *
 * This is intentionally stricter than a visual smoke test. A frame is written below
 * `accepted/` only after the city has reported scene/sky/texture readiness, the loading
 * overlay is hidden, the current camera's building stream is idle, and the verified
 * road/ground-cover identities are present. Failure screenshots go to `diagnostics/`
 * and are never counted as accepted evidence.
 *
 * Usage:
 *   node scripts/capture-city-platform-acceptance.mjs \
 *     <origin> <output-dir> [scene-path] [cameras.json] [--viewer-only]
 *
 * The origin can serve a frozen production build. The route hooks expose the existing map
 * instances for measurement; they do not replace any provider, actor, or scene asset.
 */
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, relative, resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5390";
const output = resolve(process.argv[3] ?? "../validation/codex-takeover-20261001/U/acceptance");
const scenePath = process.argv[4] ?? "/city-presentation/default-scene-v1.json";
const cameraFile = resolve(process.argv[5]
  ?? "../validation/platform-plan-20261001/E1-browser-v2/cameras.json");
const timeoutMs = 300_000;
const viewerOnly = process.argv.includes("--viewer-only");

const expected = Object.freeze({
  environmentUrl: "/city-presentation/shanghai-source-ground-cover-v1.json",
  environmentSha256: "075df19e940381f4aa226d3a1c06ab002c7fb1d9e808426628d3c829a00a9f31",
  environmentSizeBytes: 188_867,
  groundCoverGeometryId: "fd2d3b56a7ccd5a40966e46c66509630541cdf72cf4f09fc3c3653c162490f6f",
  groundCoverCount: 19,
  buildingCount: 414,
});
const gpuArgs = [
  "--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
  "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist",
];
const acceptedRoot = resolve(output, "accepted");
const diagnosticsRoot = resolve(output, "diagnostics");
const report = {
  schemaVersion: "aero-bench.city-platform-visual-acceptance/v1",
  status: "RUNNING",
  origin,
  scenePath,
  cameraFile,
  startedAt: new Date().toISOString(),
  scope: viewerOnly ? "viewer-weather-lighting-and-ground-cover" : "viewer-and-studio",
  instrumentedBundles: [],
  acceptancePolicy: {
    timeoutMs,
    acceptedDirectory: "accepted",
    diagnosticDirectory: "diagnostics",
    loadingFramesAreAccepted: false,
    fixedPreviewSecond: 34,
    matrix: "overview/street-1 x day/twilight/night x clear/rain",
  },
  expected,
  checks: [],
  failures: [],
  pageErrors: [],
  consoleErrors: [],
  requestFailures: [],
  viewer: {},
  matrix: [],
  studio: [],
  studioModules: [],
  actors: null,
};

function check(name, passed, details, { fatal = false } = {}) {
  const entry = { name, passed: Boolean(passed), details };
  report.checks.push(entry);
  if (!entry.passed) {
    report.failures.push(name);
    if (fatal) throw new Error(`${name}: ${typeof details === "string" ? details : JSON.stringify(details)}`);
  }
  return entry.passed;
}

function recordPageSignals(page, label) {
  page.on("pageerror", error => report.pageErrors.push(`${label}: ${error.message}`));
  page.on("console", message => {
    if (message.type() === "error") report.consoleErrors.push(`${label}: ${message.text()}`);
  });
  page.on("requestfailed", request => {
    report.requestFailures.push(`${label}: ${request.method()} ${request.url()} — ${request.failure()?.errorText ?? "failed"}`);
  });
}

async function routeAndReplace(page, pattern, token, replacement, label) {
  await page.route(pattern, async route => {
    const response = await route.fetch();
    const source = await response.text();
    if (source.split(token).length !== 2) throw new Error(`${label} hook no longer matches exactly once`);
    await route.fulfill({ response, body: source.replace(token, replacement) });
  });
}

async function hookViewer(page) {
  await routeAndReplace(page, /\/src\/app\.ts(?:\?.*)?$/,
    "this.map = new PublicTraceMap(",
    "window.__aeroVisualMap = this.map = new PublicTraceMap(", "viewer map");
  await hookProductionMap(page, /\/assets\/app-[^/]+\.js$/, "viewer");
}

async function hookStudio(page) {
  await routeAndReplace(page, /\/src\/city-studio\.ts(?:\?.*)?$/,
    "this.map = new PublicTraceMap(",
    "window.__aeroStudioMap = this.map = new PublicTraceMap(", "Studio map");
  await hookProductionMap(page, /\/assets\/cityStudio-[^/]+\.js$/, "studio");
}

export function instrumentCityMapBundle(source, kind) {
  if (!["viewer", "studio"].includes(kind)) throw new Error(`Unknown map kind: ${kind}`);
  const pattern = kind === "viewer"
    ? /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/g
    : /this\.map=new [\w$]+\(this\.mapRoot,\{/g;
  if ([...source.matchAll(pattern)].length !== 1) throw new Error(`${kind} production map hook must match exactly once`);
  const name = kind === "viewer" ? "__aeroVisualMap" : "__aeroStudioMap";
  return source.replace(pattern, match => match.replace("this.map=", `window.${name}=this.map=`));
}

async function hookProductionMap(page, pattern, kind) {
  await page.route(pattern, async route => {
    const response = await route.fetch();
    const bytes = await response.body();
    const body = instrumentCityMapBundle(bytes.toString("utf8"), kind);
    report.instrumentedBundles.push({ kind, url: route.request().url(),
      originalSha256: createHash("sha256").update(bytes).digest("hex"), originalSizeBytes: bytes.length,
      instrumentedSha256: createHash("sha256").update(body).digest("hex"),
      instrumentation: "Expose the existing map instance; no scene or provider substitution" });
    await route.fulfill({ response, body });
  });
}

async function screenshot(pageOrLocator, file, details = {}) {
  await mkdir(resolve(file, ".."), { recursive: true });
  await pageOrLocator.screenshot({ path: file });
  const bytes = await readFile(file);
  return {
    file: relative(output, file),
    sha256: createHash("sha256").update(bytes).digest("hex"),
    sizeBytes: bytes.length,
    ...details,
  };
}

async function diagnostic(page, label) {
  try {
    const safe = label.replace(/[^A-Za-z0-9_.-]+/g, "-");
    const file = resolve(diagnosticsRoot, `${safe}.png`);
    return await screenshot(page, file, { classification: "loading-or-failure-diagnostic", accepted: false });
  } catch (error) {
    return { classification: "diagnostic-capture-failed", error: String(error) };
  }
}

async function waitForCompleteCity(page, selector, globalName) {
  const started = Date.now();
  await page.waitForFunction(({ selector, globalName }) => {
    const data = document.querySelector(selector)?.dataset;
    return (window[globalName] !== undefined && data?.sceneReady === "true"
      && data?.skyReady === "true" && data?.texturesReady === "true")
      || data?.sceneError === "true";
  }, { selector, globalName }, { timeout: timeoutMs, polling: 500 });
  const state = await page.locator(selector).evaluate(root => ({ ...root.dataset }));
  check(`${selector} scene has no load error`, state.sceneError !== "true",
    state.sceneErrorMessage ?? state, { fatal: true });
  await page.locator(`${selector} .city-scene-loading`).waitFor({ state: "hidden", timeout: timeoutMs });
  await page.waitForFunction(globalName => {
    const map = window[globalName];
    return map !== undefined && (map.renderStreamer?.progress.active ?? 0) === 0;
  }, globalName, { timeout: timeoutMs, polling: 250 });
  return { loadMs: Date.now() - started, dataset: state };
}

async function readVerifiedSceneGate(page) {
  return page.evaluate(async ({ scenePath }) => {
    const map = window.__aeroVisualMap;
    const root = map.root;
    const data = { ...root.dataset };
    const gl = map.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
    const digest = async bytes => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
      .map(byte => byte.toString(16).padStart(2, "0")).join("");
    const sceneResponse = await fetch(scenePath);
    if (!sceneResponse.ok) throw new Error(`Scene fetch failed: ${sceneResponse.status}`);
    const sceneBytes = await sceneResponse.arrayBuffer();
    const scene = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(sceneBytes));
    const environmentResponse = await fetch(scene.environment_source.url);
    if (!environmentResponse.ok) throw new Error(`Environment fetch failed: ${environmentResponse.status}`);
    const environmentBytes = await environmentResponse.arrayBuffer();
    const groundCoverPlan = map.cityVegetationLayer?.group.userData.groundCoverPlan ?? null;
    const drawnSet = JSON.parse(data.cityGroundCoverDrawnSet ?? "null");
    const vegetation = JSON.parse(data.cityVegetation ?? "null");
    return {
      renderer,
      loaderHidden: root.querySelector(".city-scene-loading")?.hidden === true,
      source: {
        sceneSha256: await digest(sceneBytes),
        environmentRef: scene.environment_source,
        environmentActualSha256: await digest(environmentBytes),
        environmentActualSizeBytes: environmentBytes.byteLength,
      },
      readiness: {
        sceneReady: data.sceneReady,
        skyReady: data.skyReady,
        texturesReady: data.texturesReady,
        renderStreamActive: map.renderStreamer?.progress.active ?? null,
      },
      buildings: {
        manifestCount: map.renderStreamer?.manifest.buildings.length ?? null,
        total: Number(data.buildingRenderTotal),
        loaded: Number(data.buildingRenderLoaded),
        failed: Number(data.buildingRenderFailed),
        errors: JSON.parse(data.buildingRenderErrors ?? "[]"),
        replacements: Number(data.buildingReplacements),
        visualParts: Number(data.buildingVisualParts),
        packMissingObjects: Number(data.buildingRenderPackMissing),
        blocks: Number(data.buildingRenderBlocks),
      },
      roads: {
        verified: data.roadAssetsVerified,
        surfaceSha256: data.roadAssetsSurfaceSha256,
        roadSha256: data.roadAssetsRoadSha256,
        trafficSha256: data.roadAssetsTrafficSha256,
        flightSha256: data.roadAssetsFlightSha256,
        effectiveFixturesSha256: data.roadAssetsEffectiveFixturesSha256,
        laneCount: Number(data.roadLaneCount),
        streetLampCount: Number(data.streetLampCount),
        trafficSignalCount: Number(data.trafficSignalCount),
        renderedTrafficSignalCount: Number(data.renderedTrafficSignalCount),
        signalObjectCount: map.trafficPreview?.lamps.length ?? null,
      },
      vegetation,
      groundCover: {
        plan: groundCoverPlan === null ? null : {
          schemaVersion: groundCoverPlan.schemaVersion,
          geometryId: groundCoverPlan.geometryId,
          sourceCount: groundCoverPlan.sourceCount,
          fullyClippedIds: groundCoverPlan.fullyClippedIds,
          stats: groundCoverPlan.stats,
        },
        drawnSet,
      },
    };
  }, { scenePath });
}

function auditSceneGate(gate) {
  check("viewer uses hardware NVIDIA rendering", /NVIDIA/i.test(gate.renderer)
    && !/swiftshader|llvmpipe|software/i.test(gate.renderer), gate.renderer);
  check("scene, sky, and textures are ready with the loader hidden",
    gate.readiness.sceneReady === "true" && gate.readiness.skyReady === "true"
      && gate.readiness.texturesReady === "true" && gate.loaderHidden
      && gate.readiness.renderStreamActive === 0, { readiness: gate.readiness, loaderHidden: gate.loaderHidden });
  check("environment source reference has the published identity",
    gate.source.environmentRef?.url === expected.environmentUrl
      && gate.source.environmentRef?.sha256 === expected.environmentSha256
      && gate.source.environmentRef?.size_bytes === expected.environmentSizeBytes,
    gate.source.environmentRef);
  check("downloaded environment bytes match the published identity",
    gate.source.environmentActualSha256 === expected.environmentSha256
      && gate.source.environmentActualSizeBytes === expected.environmentSizeBytes,
    { sha256: gate.source.environmentActualSha256, sizeBytes: gate.source.environmentActualSizeBytes });
  check("all 414 source buildings are represented by the render manifest and replacement layer",
    gate.buildings.manifestCount === expected.buildingCount
      && gate.buildings.total === expected.buildingCount
      && gate.buildings.replacements === expected.buildingCount,
    gate.buildings);
  check("the current camera loaded building assets without stream errors",
    gate.buildings.loaded > 0 && gate.buildings.failed === 0 && gate.buildings.errors.length === 0,
    gate.buildings);
  check("canonical road and fixture assets are verified and nonempty",
    gate.roads.verified === "true" && gate.roads.surfaceSha256 === expected.groundCoverGeometryId
      && gate.roads.laneCount > 0 && gate.roads.streetLampCount > 0
      && gate.roads.trafficSignalCount > 0
      && gate.roads.signalObjectCount > 0
      && gate.roads.signalObjectCount <= gate.roads.trafficSignalCount
      && gate.roads.renderedTrafficSignalCount >= 0
      && gate.roads.renderedTrafficSignalCount <= gate.roads.trafficSignalCount,
    gate.roads);
  check("OSM vegetation and source-ground-cover summaries are nonempty",
    gate.vegetation !== null && gate.vegetation.greens > 0 && gate.vegetation.trees > 0
      && gate.vegetation.clumps > 0 && gate.vegetation.groundCovers === expected.groundCoverCount
      && gate.vegetation.groundCoverAreaM2 > 0,
    gate.vegetation);
  const plan = gate.groundCover.plan, drawn = gate.groundCover.drawnSet;
  check("ground-cover plan is bound to the displayed canonical geometry",
    plan?.geometryId === expected.groundCoverGeometryId && plan?.sourceCount === expected.groundCoverCount
      && plan?.stats?.sourceAreaM2 > 0 && plan?.stats?.drawnAreaM2 > 0,
    plan);
  check("the full measured ground-cover drawn set has no omissions",
    drawn?.status === "pass" && drawn?.geometry_id === expected.groundCoverGeometryId
      && drawn?.parser_accepted_ids?.length === expected.groundCoverCount
      && drawn?.drawn_ids?.length === drawn?.drawable_ids?.length
      && drawn?.omission_count === 0 && drawn?.omitted_ids?.length === 0
      && drawn?.unexpected_ids?.length === 0,
    drawn);
}

async function focusAudit(page, scope, selectors) {
  await page.locator("body").click({ position: { x: 2, y: 2 } });
  let result = null;
  for (let index = 0; index < 16; index += 1) {
    await page.keyboard.press("Tab");
    result = await page.evaluate(selectors => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !selectors.some(selector => active.matches(selector))) return null;
      const style = getComputedStyle(active);
      const rect = active.getBoundingClientRect();
      return {
        tag: active.tagName,
        id: active.id,
        className: active.className,
        text: (active.textContent ?? "").trim().slice(0, 80),
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        outlineColor: style.outlineColor,
        boxShadow: style.boxShadow,
        visible: rect.width > 0 && rect.height > 0,
      };
    }, selectors);
    if (result !== null && result.visible
        && (result.outlineStyle !== "none" && Number.parseFloat(result.outlineWidth) > 0
          || result.boxShadow !== "none")) break;
  }
  check(`${scope} exposes a visible keyboard focus treatment`, result !== null && result.visible
    && (result.outlineStyle !== "none" && Number.parseFloat(result.outlineWidth) > 0
      || result.boxShadow !== "none"), result);
  return result;
}

async function captureViewer(browser, cameras) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  recordPageSignals(page, "viewer");
  await hookViewer(page);
  try {
    await page.goto(`${origin}/?city=${encodeURIComponent(scenePath)}`,
      { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const ready = await waitForCompleteCity(page, "#city-map", "__aeroVisualMap");
    const gate = await readVerifiedSceneGate(page);
    const gateFailureCount = report.failures.length;
    auditSceneGate(gate);
    if (report.failures.length !== gateFailureCount) {
      throw new Error("Verified scene/asset gates failed; no viewer frame is accepted");
    }
    const shell = await page.evaluate(() => Object.fromEntries(
      [".app", ".topbar", ".sidebar", ".map", ".rightbar", ".timeline"].map(selector => {
        const node = document.querySelector(selector), rect = node?.getBoundingClientRect();
        return [selector, node === null ? null : { hidden: node.hidden,
          display: getComputedStyle(node).display, width: rect?.width ?? 0, height: rect?.height ?? 0 }];
      })));
    const shellReady = check("final viewer chrome is visible", Object.values(shell).every(value => value !== null
      && !value.hidden && value.display !== "none" && value.width > 0 && value.height > 0), shell);
    const provenance = await page.locator(".provenance-chip[data-provenance]").evaluateAll(nodes => nodes.map(node => ({
      value: node.getAttribute("data-provenance"), text: (node.textContent ?? "").trim(),
      visible: node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0,
    })));
    const visibleProvenance = provenance.filter(item => item.visible);
    const provenanceReady = check("viewer chrome renders visible, labelled provenance chips",
      visibleProvenance.length >= 2 && visibleProvenance.every(item => item.value && item.text), provenance);
    const focusFailureCount = report.failures.length;
    const focus = await focusAudit(page, "viewer", ["button", "a[href]", "input", "select"]);
    if (!shellReady || !provenanceReady || report.failures.length !== focusFailureCount) {
      throw new Error("Viewer chrome acceptance failed; its screenshot is diagnostic only");
    }
    const chrome = await screenshot(page, resolve(acceptedRoot, "viewer", "viewer-chrome.png"), {
      classification: "accepted-complete-city", sceneReady: true, loaderHidden: true,
    });
    report.viewer = { loadMs: ready.loadMs, renderer: gate.renderer, shell, provenance, focus, gate, chrome };

    await page.evaluate(async () => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation);
      map.previewAnimation = 0;
      map.controls.enableDamping = false;
    });
    const views = ["overview", "street-1"].map(name => cameras.find(camera => camera.name === name));
    check("stored overview and street-1 cameras are available", views.every(Boolean),
      { requested: ["overview", "street-1"], available: cameras.map(camera => camera.name) }, { fatal: true });
    for (const view of views) {
      for (const timeOfDay of ["day", "twilight", "night"]) {
        for (const weather of ["clear", "rain"]) {
          await page.evaluate(({ view, timeOfDay, weather }) => {
            const map = window.__aeroVisualMap;
            map.previewPlaying = false;
            cancelAnimationFrame(map.previewAnimation);
            map.previewAnimation = 0;
            map.previewFollowId = null;
            map.previewSeconds = 34;
            map.setCameraMode("free");
            map.camera.position.set(...view.eye);
            map.controls.target.set(...view.target);
            map.controls.update();
            map.configureCityPresentation({ timeOfDay });
            const preset = map.root.querySelector("[name='render-weather-preset']");
            if (!(preset instanceof HTMLSelectElement)) throw new Error("Public weather preset control is missing");
            preset.value = weather;
            preset.dispatchEvent(new Event("change", { bubbles: true }));
            map.previewPlaying = false;
            cancelAnimationFrame(map.previewAnimation);
            map.previewAnimation = 0;
            map.previewSeconds = 34;
            map.renderStreamer?.update(map.camera);
          }, { view, timeOfDay, weather });
          await page.waitForFunction(() => (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
            undefined, { timeout: timeoutMs, polling: 250 });
          const measured = await page.evaluate(() => {
            const map = window.__aeroVisualMap;
            map.focusSunShadow();
            map.renderPreviewFrame();
            const gl = map.renderer.getContext();
            gl.finish();
            const width = gl.drawingBufferWidth, height = gl.drawingBufferHeight;
            const pixels = new Uint8Array(width * height * 4);
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            const histogram = new Uint32Array(256);
            let sum = 0, samples = 0;
            for (let offset = 0; offset < pixels.length; offset += 4) {
              const luminance = Math.max(0, Math.min(255, Math.round(
                pixels[offset] * 0.2126 + pixels[offset + 1] * 0.7152 + pixels[offset + 2] * 0.0722)));
              histogram[luminance] += 1; sum += luminance; samples += 1;
            }
            const percentile = fraction => {
              const target = Math.ceil(samples * fraction); let count = 0;
              for (let value = 0; value < histogram.length; value += 1) {
                count += histogram[value]; if (count >= target) return value / 255;
              }
              return 1;
            };
            const data = map.root.dataset;
            const preset = map.root.querySelector("[name='render-weather-preset']");
            const note = map.root.querySelector("[data-role='render-weather-note']");
            const road = map.roadPresentation;
            const lights = road?.userData.streetLampLights ?? [];
            const halos = road?.userData.streetLampHalos ?? [];
            const pools = road?.userData.streetLampGroundPools;
            const storefront = map.buildingPresentation?.userData.storefrontLights;
            const lampParts = new Map();
            road?.traverse(node => {
              for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
                if (!material?.name?.startsWith("street_light_8SG")) continue;
                lampParts.set(material.name, { name: material.name, color: material.color?.getHexString(),
                  emissive: material.emissive?.getHexString(), emissiveIntensity: material.emissiveIntensity,
                  metalness: material.metalness, roughness: material.roughness });
              }
            });
            return {
              camera: { eye: map.camera.position.toArray(), target: map.controls.target.toArray() },
              actualMode: {
                timeOfDay: data.cityTimeOfDay,
                mood: data.cityMood,
                weatherValue: preset?.value ?? null,
                weatherLabel: preset?.selectedOptions?.[0]?.textContent?.trim() ?? null,
                weatherNote: note?.textContent?.trim() ?? null,
                weatherNoteMode: note?.dataset.mode ?? null,
              },
              luminance: { method: "WebGL RGBA8 Rec.709 sRGB-code-value", width, height,
                sampleCount: samples, mean: sum / samples / 255,
                p10: percentile(0.1), p50: percentile(0.5), p90: percentile(0.9) },
              surfaceWetness: map.groundWetness.uWetness.value,
              roadWetnessMaterialCount: road?.userData.surfaceWetnessMaterialCount ?? 0,
              roadWetnessUniformShared: road?.userData.surfaceWetnessUniforms === map.groundWetness,
              lighting: {
                lampParts: [...lampParts.values()],
                lensOffset: road?.userData.streetLampLensOffset ?? null,
                streetLampPoolVisible: pools?.visible ?? false,
                activeStreetLampLights: lights.filter(light => light.intensity > 0).length,
                visibleStreetLampHalos: halos.filter(halo => halo.visible).length,
                storefrontVisible: storefront?.visible ?? false,
                storefrontCount: Number(data.storefrontLightCount ?? "0"),
                headlightBeamCount: Number(data.headlightBeamCount ?? "0"),
                vehicleLampMaterialCount: Number(data.vehicleLampMaterialCount ?? "0"),
              },
              actors: {
                vehicles: Number(data.previewVisibleVehicles ?? "0"),
                bicycles: Number(data.previewVisibleBicycles ?? "0"),
                pedestrians: Number(data.previewVisiblePedestrians ?? "0"),
                uavs: Number(data.renderedUavCount ?? "0"),
              },
              streamActive: map.renderStreamer?.progress.active ?? 0,
              loaderHidden: map.root.querySelector(".city-scene-loading")?.hidden === true,
            };
          });
          const requestedLabel = weather === "clear" ? "晴" : "雨";
          const modeReady = check(`${view.name}/${timeOfDay}/${weather} reports its actual requested modes`,
            measured.actualMode.timeOfDay === timeOfDay
              && measured.actualMode.weatherValue === weather
              && measured.actualMode.weatherLabel === requestedLabel,
            measured.actualMode);
          const loadReady = check(`${view.name}/${timeOfDay}/${weather} is captured after streaming and loading settle`,
            measured.streamActive === 0 && measured.loaderHidden, measured);
          const weatherReady = check(`${view.name}/${timeOfDay}/${weather} uses the shared verified road wetness uniform`,
            measured.roadWetnessMaterialCount > 0 && measured.roadWetnessUniformShared
              && (weather === "clear" ? measured.surfaceWetness === 0 : measured.surfaceWetness > 0.5),
            { wetness: measured.surfaceWetness, materialCount: measured.roadWetnessMaterialCount,
              shared: measured.roadWetnessUniformShared });
          const lightingReady = check(`${view.name}/${timeOfDay}/${weather} applies day/night fixture visibility`,
            timeOfDay === "day"
              ? !measured.lighting.streetLampPoolVisible
                && (measured.lighting.storefrontCount === 0 || !measured.lighting.storefrontVisible)
              : measured.lighting.streetLampPoolVisible
                && (measured.lighting.storefrontCount === 0 || measured.lighting.storefrontVisible),
            measured.lighting);
          const parts = measured.lighting.lampParts;
          const lens = parts.find(part => part.name === "street_light_8SG6");
          const plate = parts.find(part => part.name === "street_light_8SG1");
          const offset = measured.lighting.lensOffset;
          const correctedLamps = check(`${view.name}/${timeOfDay}/${weather} uses corrected lens-only lamp emission and measured placement`,
            parts.length === 4 && lens?.emissive === "ffb66a"
              && (timeOfDay === "day" ? lens.emissiveIntensity === 0 : lens.emissiveIntensity > 0)
              && parts.filter(part => part.name !== "street_light_8SG6").every(part =>
                part.emissive === "000000" && part.emissiveIntensity === 0)
              && plate?.color === "78828a" && plate.metalness === 0.2 && plate.roughness === 0.7
              && Math.abs(Math.abs(offset?.alongArmM) - 3.52) < 0.05
              && Math.abs(offset?.heightM - 7.09) < 0.05,
            { parts, offset });
          if (!modeReady || !loadReady || !weatherReady || !lightingReady || !correctedLamps) {
            throw new Error(`${view.name}/${timeOfDay}/${weather} failed its pre-capture acceptance gates`);
          }
          const file = resolve(acceptedRoot, "matrix", `${view.name}-${timeOfDay}-${weather}.png`);
          const frame = await screenshot(page.locator("#city-map canvas").first(), file, {
            classification: "accepted-matched-camera", view: view.name, requestedTimeOfDay: timeOfDay,
            requestedWeather: weather, actualMode: measured.actualMode, previewSecond: 34,
          });
          report.matrix.push({ ...frame, ...measured });
        }
      }
    }
    for (const view of ["overview", "street-1"]) for (const weather of ["clear", "rain"]) {
      const frames = Object.fromEntries(report.matrix.filter(frame => frame.view === view
        && frame.requestedWeather === weather).map(frame => [frame.requestedTimeOfDay, frame]));
      check(`${view}/${weather} day frame is measurably brighter than night`,
        frames.day.luminance.mean > frames.night.luminance.mean + 0.01,
        Object.fromEntries(Object.entries(frames).map(([mode, frame]) => [mode, frame.luminance.mean])));
      check(`${view}/${weather} twilight and night are not identical renders`,
        Math.abs(frames.twilight.luminance.mean - frames.night.luminance.mean) > 0.001,
        { twilight: frames.twilight.luminance.mean, night: frames.night.luminance.mean });
    }
    const darkStreet = report.matrix.filter(frame => frame.view === "street-1"
      && frame.requestedTimeOfDay !== "day");
    check("street twilight/night frames activate local lamps, halos, and vehicle lamp materials",
      darkStreet.every(frame => frame.lighting.activeStreetLampLights > 0
        && frame.lighting.visibleStreetLampHalos > 0)
      && darkStreet.every(frame => frame.lighting.vehicleLampMaterialCount > 0),
      darkStreet.map(frame => ({ mode: frame.actualMode, lighting: frame.lighting })));

    const headlight = await page.evaluate(() => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      map.previewSeconds = 34;
      const frame = map.trafficPreview.data.frames.reduce((closest, candidate) =>
        Math.abs(candidate.second - 34) < Math.abs(closest.second - 34) ? candidate : closest,
      map.trafficPreview.data.frames[0]);
      const actor = frame?.vehicles.find(row => row[4] !== "bicycle")?.[0] ?? null;
      if (actor === null) return null;
      map.previewFollowId = actor;
      map.configureCityPresentation({ timeOfDay: "night" });
      const preset = map.root.querySelector("[name='render-weather-preset']");
      if (!(preset instanceof HTMLSelectElement)) throw new Error("Public weather preset control is missing");
      preset.value = "clear";
      preset.dispatchEvent(new Event("change", { bubbles: true }));
      map.previewSeconds = 34;
      map.renderPreviewFrame();
      return { actor, position: map.trafficPreview.entityPosition(actor)?.toArray() ?? null,
        camera: map.camera.position.toArray(), target: map.controls.target.toArray(),
        headlightBeamCount: Number(map.root.dataset.headlightBeamCount ?? "0"),
        vehicleLampMaterialCount: Number(map.root.dataset.vehicleLampMaterialCount ?? "0") };
    });
    await page.waitForFunction(() => (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
      undefined, { timeout: timeoutMs, polling: 250 });
    const headlightMeasured = await page.evaluate(value => {
      const map = window.__aeroVisualMap;
      map.previewSeconds = 34; map.renderPreviewFrame(); map.renderer.getContext().finish();
      return { ...value, headlightBeamCount: Number(map.root.dataset.headlightBeamCount ?? "0"),
        vehicleLampMaterialCount: Number(map.root.dataset.vehicleLampMaterialCount ?? "0"),
        loaderHidden: map.root.querySelector(".city-scene-loading")?.hidden === true,
        streamActive: map.renderStreamer?.progress.active ?? 0 };
    }, headlight);
    check("night motor follow activates recorded-traffic headlight beams",
      headlightMeasured?.actor?.startsWith("vehicle.") && headlightMeasured.position !== null
        && headlightMeasured.headlightBeamCount > 0 && headlightMeasured.vehicleLampMaterialCount > 0
        && headlightMeasured.loaderHidden && headlightMeasured.streamActive === 0,
      headlightMeasured, { fatal: true });
    report.viewer.headlightFrame = {
      ...await screenshot(page.locator("#city-map canvas").first(),
        resolve(acceptedRoot, "viewer", "night-motor-headlights.png"), {
          classification: "accepted-real-actor-lighting", previewSecond: 34, timeOfDay: "night", weather: "clear",
        }),
      ...headlightMeasured,
    };

    const actors = await page.evaluate(() => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      map.previewSeconds = 34; map.renderPreviewFrame();
      const frame = map.trafficPreview.data.frames.reduce((closest, candidate) =>
        Math.abs(candidate.second - map.previewSeconds) < Math.abs(closest.second - map.previewSeconds)
          ? candidate : closest, map.trafficPreview.data.frames[0]);
      const bicycle = frame?.vehicles.find(row => row[4] === "bicycle")?.[0] ?? null;
      const uav = map.trafficPreview.flightData.vehicle_ids.find(id => id.startsWith("uav.")) ?? null;
      return {
        previewSecond: map.previewSeconds,
        bicycle: bicycle === null ? null : { id: bicycle,
          position: map.trafficPreview.entityPosition(bicycle)?.toArray() ?? null },
        uav: uav === null ? null : { id: uav,
          position: map.trafficPreview.entityPosition(uav)?.toArray() ?? null },
        flightSourceKind: map.trafficPreview.flightData.source_kind,
      };
    });
    check("real recorded bicycle and UAV follow targets are available at the fixed preview time",
      actors.bicycle?.id.startsWith("bicycle.") && actors.bicycle.position !== null
        && actors.uav?.id.startsWith("uav.") && actors.uav.position !== null,
      actors);
    report.actors = actors;
    await writeFile(resolve(output, "follow-actors.json"),
      `${JSON.stringify([actors.bicycle?.id, actors.uav?.id].filter(Boolean), null, 2)}\n`);
  } catch (error) {
    report.viewerDiagnostic = await diagnostic(page, "viewer-failure");
    throw error;
  } finally {
    await context.close();
  }
}

async function captureIntegratedStudioModule(page, tab, tabIndex) {
  const prefix = String(tabIndex + 1).padStart(2, "0");
  if (tab === "runtime") {
    await page.waitForFunction(() => {
      const panel = document.querySelector("#studio-panel .traffic-preview-panel");
      const select = panel?.querySelector("select");
      const status = panel?.querySelector(".studio-note[aria-live='polite']")?.textContent ?? "";
      return panel !== null && select instanceof HTMLSelectElement && !select.disabled
        && select.options.length > 1 && status.length > 0 && !status.includes("正在读取");
    }, undefined, { timeout: timeoutMs, polling: 250 });
    const module = page.locator("#studio-panel .traffic-preview-panel");
    const state = await module.evaluate(panel => {
      const select = panel.querySelector("select");
      return {
        module: "traffic-preview",
        heading: panel.querySelector("h2")?.textContent?.trim() ?? "",
        profileOptions: select instanceof HTMLSelectElement ? select.options.length : 0,
        selectedProfile: select instanceof HTMLSelectElement ? select.value : "",
        catalogStatus: panel.querySelector(".studio-note[aria-live='polite']")?.textContent?.trim() ?? "",
        resultState: panel.querySelector(".traffic-preview-result")?.dataset.state ?? "",
        provenance: panel.querySelector(".provenance-chip[data-provenance]")?.getAttribute("data-provenance") ?? "",
        panelText: (panel.textContent ?? "").trim().slice(0, 1200),
      };
    });
    const ready = check("Studio integrated traffic-preview module is catalog-ready and requires explicit selection",
      state.heading === "SUMO 交通预览" && state.profileOptions > 1 && state.selectedProfile === ""
        && state.resultState === "idle" && state.provenance === "unknown"
        && !state.catalogStatus.includes("不可用") && !state.catalogStatus.includes("失败"), state);
    if (!ready) throw new Error("Studio traffic-preview module failed its pre-capture acceptance gates");
    const file = resolve(acceptedRoot, "studio", `${prefix}-runtime-traffic-preview.png`);
    report.studioModules.push({ ...await screenshot(module, file, {
      classification: "accepted-studio-module", tab, module: "traffic-preview",
    }), ...state });
  } else if (tab === "spatial") {
    const module = page.locator("#studio-panel section.studio-card").filter({ hasText: "创作景观" }).first();
    await module.waitFor({ state: "visible", timeout: timeoutMs });
    const state = await module.evaluate(panel => ({
      module: "authored-landscape",
      heading: panel.querySelector("h2")?.textContent?.trim() ?? "",
      buttonLabels: [...panel.querySelectorAll("button")].map(button => button.textContent?.trim() ?? ""),
      panelText: (panel.textContent ?? "").trim().slice(0, 1200),
    }));
    const ready = check("Studio integrated authored-landscape module is visible and provenance-labelled",
      state.heading === "创作景观" && state.panelText.includes("authored")
        && state.buttonLabels.includes("添加创作景观"), state);
    if (!ready) throw new Error("Studio authored-landscape module failed its pre-capture acceptance gates");
    const file = resolve(acceptedRoot, "studio", `${prefix}-spatial-authored-landscape.png`);
    report.studioModules.push({ ...await screenshot(module, file, {
      classification: "accepted-studio-module", tab, module: "authored-landscape",
    }), ...state });
  } else if (tab === "algorithm") {
    const module = page.locator("#studio-panel .studio-logistics-panel");
    await module.waitFor({ state: "visible", timeout: timeoutMs });
    const state = await module.evaluate(panel => ({
      module: "orders-and-performance",
      heading: panel.querySelector("h2")?.textContent?.trim() ?? "",
      sectionCount: panel.querySelectorAll(":scope > section").length,
      buttonCount: panel.querySelectorAll("button").length,
      alert: panel.querySelector("[role='alert']")?.textContent?.trim() ?? "",
      panelText: (panel.textContent ?? "").trim().slice(0, 1200),
    }));
    const ready = check("Studio integrated orders and performance module is populated without a blocking error",
      state.heading === "订单与机队性能" && state.sectionCount >= 3
        && state.buttonCount > 0 && state.alert === "", state);
    if (!ready) throw new Error("Studio orders module failed its pre-capture acceptance gates");
    const file = resolve(acceptedRoot, "studio", `${prefix}-algorithm-orders-performance.png`);
    report.studioModules.push({ ...await screenshot(module, file, {
      classification: "accepted-studio-module", tab, module: "orders-and-performance",
    }), ...state });
  } else if (tab === "events") {
    const module = page.locator("#studio-panel [data-role='event-timeline']");
    await module.waitFor({ state: "visible", timeout: timeoutMs });
    // Studio refreshes the inner timeline from the playback clock every 180 ms.
    // Its parent host is stable across those updates and frames the same module.
    const moduleHost = module.locator("..");
    if (await module.locator("[data-event-id]").count() === 0) {
      await page.getByRole("button", { name: "添加事件", exact: true }).click();
      await page.getByLabel("目标 ID", { exact: true }).fill("district.preview");
      await page.getByLabel("事件载荷（JSON 对象）", { exact: true })
        .fill('{"zeta":2,"alpha":{"wind":3}}');
    }
    await module.locator("[data-event-id]").first().waitFor({ state: "visible", timeout: timeoutMs });
    const state = await module.evaluate(panel => ({
      module: "event-timeline",
      heading: panel.querySelector("h3")?.textContent?.trim() ?? "",
      disclaimer: panel.querySelector('[data-role="event-timeline-disclaimer"]')?.textContent?.trim() ?? "",
      summary: panel.querySelector('[data-role="event-timeline-summary"]')?.textContent?.trim() ?? "",
      rows: [...panel.querySelectorAll("[data-event-id]")].map(row => ({
        id: row.getAttribute("data-event-id") ?? "",
        status: row.getAttribute("data-timeline-status") ?? "",
        text: row.textContent?.trim() ?? "",
      })),
      fixture: "browser-local unsaved draft event; no Provider action and no formal evidence",
    }));
    const ready = check("Studio integrated event timeline shows deterministic payload JSON and preview-only status",
      state.heading === "计划事件时间线" && state.rows.length > 0
        && state.rows[0].text.includes('负载 {"alpha":{"wind":3},"zeta":2}')
        && state.rows[0].text.includes("（预览）") && state.disclaimer.includes("不表示 Provider"), state);
    if (!ready) throw new Error("Studio event-timeline module failed its pre-capture acceptance gates");
    const file = resolve(acceptedRoot, "studio", `${prefix}-events-timeline.png`);
    report.studioModules.push({ ...await screenshot(moduleHost, file, {
      classification: "accepted-studio-module", tab, module: "event-timeline",
    }), ...state });
  }
}

async function captureStudio(browser) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  recordPageSignals(page, "studio");
  await hookStudio(page);
  try {
    await page.goto(`${origin}/city-studio.html?tab=runtime`,
      { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const ready = await waitForCompleteCity(page, "#studio-map", "__aeroStudioMap");
    await page.waitForFunction(() => ["ready", "warning"].includes(
      document.querySelector("#studio-preview-state")?.dataset.state ?? ""),
    undefined, { timeout: timeoutMs, polling: 250 });
    const tabs = ["runtime", "spatial", "algorithm", "events", "compile"];
    const knownTabs = new Set([...tabs, "region"]);
    const discoveredTabs = await page.locator("#studio-tabs a[data-tab]").evaluateAll(links => links.map((link, index) => ({
      index,
      tab: link instanceof HTMLElement ? link.dataset.tab ?? "" : "",
      label: link.textContent?.trim() ?? "",
    })));
    const tabIds = discoveredTabs.map(item => item.tab);
    const tabsValid = check("Studio tab identifiers are complete, unique and safe for evidence filenames",
      tabIds.length >= knownTabs.size && new Set(tabIds).size === tabIds.length
        && [...knownTabs].every(tab => tabIds.includes(tab))
        && tabIds.every(tab => /^[a-z][a-z0-9-]*$/.test(tab)), discoveredTabs);
    if (!tabsValid) throw new Error("Studio tab discovery failed its acceptance gates");
    const tabOrder = new Map(discoveredTabs.map(item => [item.tab, item.index]));
    for (const [index, tab] of tabs.entries()) {
      if (index > 0) await page.locator(`#studio-tabs a[data-tab='${tab}']`).click();
      await page.waitForFunction(tab => {
        const link = document.querySelector(`#studio-tabs a[data-tab='${tab}']`);
        const panel = document.querySelector("#studio-panel");
        return link?.classList.contains("is-active") && panel?.childElementCount > 0
          && panel.querySelector(".studio-panel-loading") === null;
      }, tab, { timeout: timeoutMs, polling: 250 });
      await page.waitForTimeout(250);
      const state = await page.evaluate(tab => ({
        tab,
        activeLabel: document.querySelector(`#studio-tabs a[data-tab='${tab}']`)?.textContent?.trim() ?? "",
        title: document.querySelector("#studio-sidebar-title")?.textContent?.trim() ?? "",
        subtitle: document.querySelector("#studio-sidebar-subtitle")?.textContent?.trim() ?? "",
        previewState: document.querySelector("#studio-preview-state")?.textContent?.trim() ?? "",
        previewStateKind: document.querySelector("#studio-preview-state")?.dataset.state ?? "",
        panelText: (document.querySelector("#studio-panel")?.textContent ?? "").trim().slice(0, 500),
        panelScrollHeight: document.querySelector("#studio-panel")?.scrollHeight ?? 0,
        loaderHidden: document.querySelector("#studio-map .city-scene-loading")?.hidden === true,
      }), tab);
      const panelReady = check(`Studio ${tab} panel is populated after city readiness`, state.panelText.length > 0
        && state.loaderHidden && ["ready", "warning"].includes(state.previewStateKind), state);
      if (!panelReady) throw new Error(`Studio ${tab} panel failed its pre-capture acceptance gates`);
      const file = resolve(acceptedRoot, "studio",
        `${String(tabOrder.get(tab) + 1).padStart(2, "0")}-${tab}.png`);
      report.studio.push({ ...await screenshot(page, file, {
        classification: "accepted-studio-panel", tab, sceneReady: true, loaderHidden: true,
      }), ...state });
      await captureIntegratedStudioModule(page, tab, tabOrder.get(tab));
    }

    await page.locator("#studio-tabs a[data-tab='region']").click();
    await page.waitForFunction(() => document.querySelector("#studio-authoring-source-select") !== null
      && document.querySelector("#studio-authoring-source") !== null,
    undefined, { timeout: timeoutMs, polling: 250 });
    await page.waitForFunction(() => {
      const issue = document.querySelector("#studio-authoring-error")?.textContent?.trim() ?? "";
      const select = document.querySelector("#studio-authoring-source-select");
      const source = document.querySelector("#studio-authoring-source")?.textContent ?? "";
      return issue.length > 0 || (select instanceof HTMLSelectElement && !select.disabled
        && select.value !== "" && document.querySelector("#studio-region-map canvas") !== null
        && !source.includes("正在") && !source.includes("尚未就绪"));
    }, undefined, { timeout: timeoutMs, polling: 500 });
    const region = await page.evaluate(() => ({
      tab: "region",
      activeLabel: document.querySelector("#studio-tabs a[data-tab='region']")?.textContent?.trim() ?? "",
      title: document.querySelector("#studio-sidebar-title")?.textContent?.trim() ?? "",
      source: document.querySelector("#studio-authoring-source")?.textContent?.trim() ?? "",
      selectedSource: document.querySelector("#studio-authoring-source-select")?.value ?? "",
      stage: document.querySelector("#studio-authoring-job")?.textContent?.trim() ?? "",
      error: document.querySelector("#studio-authoring-error")?.textContent?.trim() ?? "",
      selectorCanvas: document.querySelector("#studio-region-map canvas") !== null,
      panelText: (document.querySelector("#studio-panel")?.textContent ?? "").trim().slice(0, 900),
      panelScrollHeight: document.querySelector("#studio-panel")?.scrollHeight ?? 0,
      loaderHidden: document.querySelector("#studio-map .city-scene-loading")?.hidden === true,
    }));
    const regionReady = check("Studio region panel has a verified registered source and usable selector",
      region.error === "" && region.selectedSource !== "" && region.selectorCanvas
        && region.source.includes("SHA-256") && region.loaderHidden, region);
    if (!regionReady) throw new Error("Studio region panel failed its pre-capture acceptance gates");
    const regionFile = resolve(acceptedRoot, "studio",
      `${String(tabOrder.get("region") + 1).padStart(2, "0")}-region.png`);
    report.studio.push({ ...await screenshot(page, regionFile, {
      classification: "accepted-studio-panel", tab: "region", loaderHidden: true,
    }), ...region });

    for (const item of discoveredTabs.filter(candidate => !knownTabs.has(candidate.tab))) {
      await page.locator(`#studio-tabs a[data-tab='${item.tab}']`).click();
      await page.waitForFunction(tab => {
        const link = document.querySelector(`#studio-tabs a[data-tab='${tab}']`);
        const panel = document.querySelector("#studio-panel");
        if (!link?.classList.contains("is-active") || panel?.childElementCount === 0
            || panel.querySelector(".studio-panel-loading") !== null) return false;
        const trafficPreview = panel.querySelector(".traffic-preview-panel");
        if (trafficPreview === null) return true;
        const status = trafficPreview.querySelector(".studio-note[aria-live='polite']")?.textContent ?? "";
        return status.length > 0 && !status.includes("正在读取");
      }, item.tab, { timeout: timeoutMs, polling: 250 });
      await page.waitForTimeout(250);
      const state = await page.evaluate(tab => {
        const panel = document.querySelector("#studio-panel");
        const trafficPreview = panel?.querySelector(".traffic-preview-panel") ?? null;
        const profileSelect = trafficPreview?.querySelector("select") ?? null;
        return {
          tab,
          activeLabel: document.querySelector(`#studio-tabs a[data-tab='${tab}']`)?.textContent?.trim() ?? "",
          title: document.querySelector("#studio-sidebar-title")?.textContent?.trim() ?? "",
          subtitle: document.querySelector("#studio-sidebar-subtitle")?.textContent?.trim() ?? "",
          previewState: document.querySelector("#studio-preview-state")?.textContent?.trim() ?? "",
          previewStateKind: document.querySelector("#studio-preview-state")?.dataset.state ?? "",
          panelText: (panel?.textContent ?? "").trim().slice(0, 900),
          panelScrollHeight: panel?.scrollHeight ?? 0,
          loaderHidden: document.querySelector("#studio-map .city-scene-loading")?.hidden === true,
          trafficPreview: trafficPreview === null ? null : {
            profileOptions: profileSelect instanceof HTMLSelectElement ? profileSelect.options.length : 0,
            selectedProfile: profileSelect instanceof HTMLSelectElement ? profileSelect.value : "",
            catalogStatus: trafficPreview.querySelector(".studio-note[aria-live='polite']")?.textContent?.trim() ?? "",
            resultState: trafficPreview.querySelector(".traffic-preview-result")?.dataset.state ?? "",
          },
        };
      }, item.tab);
      const trafficReady = state.trafficPreview === null || (state.trafficPreview.profileOptions > 1
        && state.trafficPreview.selectedProfile === "" && state.trafficPreview.resultState === "idle"
        && !state.trafficPreview.catalogStatus.includes("不可用")
        && !state.trafficPreview.catalogStatus.includes("失败"));
      const panelReady = check(`Studio ${item.tab} panel is populated after city readiness`,
        state.panelText.length > 0 && state.loaderHidden
          && ["ready", "warning"].includes(state.previewStateKind) && trafficReady, state);
      if (!panelReady) throw new Error(`Studio ${item.tab} panel failed its pre-capture acceptance gates`);
      const file = resolve(acceptedRoot, "studio",
        `${String(item.index + 1).padStart(2, "0")}-${item.tab}.png`);
      report.studio.push({ ...await screenshot(page, file, {
        classification: "accepted-studio-panel", tab: item.tab, sceneReady: true, loaderHidden: true,
      }), ...state });
    }

    const focus = await focusAudit(page, "Studio", ["button", "a[href]", "input", "select"]);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const reducedMotion = await page.evaluate(() => [".studio-button", ".studio-tab", ".studio-card"]
      .map(selector => {
        const node = document.querySelector(selector);
        if (node === null) return { selector, missing: true };
        const style = getComputedStyle(node);
        return { selector, transitionDuration: style.transitionDuration,
          animationDuration: style.animationDuration };
      }));
    const seconds = value => value.split(",").every(part => {
      const item = part.trim();
      return item.endsWith("ms") ? Number.parseFloat(item) <= 1 : Number.parseFloat(item) <= 0.001;
    });
    check("Studio honors reduced-motion preference", reducedMotion.every(item => !item.missing
      && seconds(item.transitionDuration) && seconds(item.animationDuration)), reducedMotion);
    report.studioSummary = { loadMs: ready.loadMs, discoveredTabs, focus, reducedMotion };
  } catch (error) {
    report.studioDiagnostic = await diagnostic(page, "studio-failure");
    throw error;
  } finally {
    await context.close();
  }
}

async function main() {
  await Promise.all([
    mkdir(resolve(acceptedRoot, "viewer"), { recursive: true }),
    mkdir(resolve(acceptedRoot, "matrix"), { recursive: true }),
    mkdir(resolve(acceptedRoot, "studio"), { recursive: true }),
    mkdir(diagnosticsRoot, { recursive: true }),
  ]);
  const cameras = JSON.parse(await readFile(cameraFile, "utf8"));
  if (!Array.isArray(cameras)) throw new Error(`${basename(cameraFile)} must contain a JSON camera array`);
  const browser = await chromium.launch({ channel: "chromium", headless: true,
    timeout: 60_000, args: gpuArgs });
  try {
    await captureViewer(browser, cameras);
    if (!viewerOnly) await captureStudio(browser);
  } finally {
    await browser.close();
  }
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
try {
  await main();
} catch (error) {
  report.failure = String(error?.stack ?? error);
  report.failures.push(`uncaught acceptance failure: ${error?.message ?? String(error)}`);
} finally {
  if (report.pageErrors.length > 0) report.failures.push("browser page errors occurred");
  report.failures = [...new Set(report.failures)];
  report.status = report.failures.length === 0 ? "PASS" : "FAIL";
  report.completedAt = new Date().toISOString();
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify({ status: report.status, output, checks: report.checks.length,
    failures: report.failures,
    acceptedFrames: (report.viewer.chrome === undefined ? 0 : 1)
      + (report.viewer.headlightFrame === undefined ? 0 : 1) + report.matrix.length
      + report.studio.length + report.studioModules.length,
    actors: report.actors }, null, 2));
  if (report.status !== "PASS") process.exitCode = 1;
}
}
