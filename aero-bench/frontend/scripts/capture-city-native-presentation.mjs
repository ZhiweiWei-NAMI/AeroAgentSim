/**
 * Hardware-browser acceptance for the declared Huangpu native-city presentation.
 *
 * This script uses the public City Studio catalog action. It does not compile or
 * start a run. A private frozen build exposes the mounted map for read-only
 * identity, texture, coordinate, and camera measurements. No live modules are patched.
 *
 * Usage:
 *   node scripts/capture-city-native-presentation.mjs \
 *     http://127.0.0.1:5393 \
 *     ../validation/codex-takeover-20261001/N/native-city \
 *     inspection.huangpu-native.v1
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const GPU_ARGS = Object.freeze([
  "--enable-gpu",
  "--use-angle=vulkan",
  "--enable-features=Vulkan",
  "--disable-vulkan-surface",
  "--disable-software-rasterizer",
  "--ignore-gpu-blocklist",
]);
const SHA256 = /^[0-9a-f]{64}$/;
const REGISTRATION_ID = /^[a-z][a-z0-9_.-]*$/;
const EXPECTED = Object.freeze({
  registrationSha256: "fa67a3c5f15b5f1fa37d1b7ab72936e5ca404ced09e9b19459a338a1fa38b962",
  scenarioSha256: "840df3a3eec4f9e70ec7f3e6a4d95209b1824a54cecf8f7e64c88ae32ce51508",
  scenarioSizeBytes: 3_550_661,
  scenarioDigest: "d644f7701eb8d2631fa7611d8bb3134337586cebbf6e42f352d686744edceac1",
  replayMode: "indexed",
  worldId: "world.shanghai-huangpu-east-v1",
  worldDigest: "18e3ac14954a515f85e0753ba050452a58fe933cbcfd1923fcd65b51b4560eed",
  profileId: "inspection.huangpu-native.v1",
  layerKinds: Object.freeze(["entities", "osm_scene", "regions", "roads"]),
  buildingCount: 414,
  presentationAssetCount: 418,
  presentationBytes: 160_364_476,
  knownBuildingId: "building.way.372180501.component.0",
  knownEntityId: "static.building.way.372180501.component.0",
  knownRenderAssetId: "render.building.way.372180501.component.0",
  knownRenderSha256: "d9a29b7351d971312c125e16df012c528013dc3837d4a79e149b20d90561bd63",
  originWgs84: Object.freeze({ latitude_deg: 31.2288, longitude_deg: 121.481,
    ellipsoid_height_m: 50 }),
  spatialExtent: Object.freeze({ min_east_m: -525, max_east_m: 515,
    min_north_m: -525, max_north_m: 518, min_up_m: -5, max_up_m: 200 }),
});

const ANNOTATION_LINES = Object.freeze([
  "原生声明资产 · 414 个已校验建筑 GLB",
  "街道设施：来源量测包络（非源模型网格）",
  "仅静态呈现验收；不代表运行或验证成功",
]);

function object(value, label) {
  assert(value !== null && typeof value === "object" && !Array.isArray(value),
    `${label} must be an object`);
  return value;
}

function array(value, label) {
  assert(Array.isArray(value), `${label} must be an array`);
  return value;
}

function text(value, label) {
  assert.equal(typeof value, "string", `${label} must be a string`);
  assert(value.length > 0, `${label} must not be empty`);
  return value;
}

function number(value, label) {
  assert.equal(typeof value, "number", `${label} must be a number`);
  assert(Number.isFinite(value), `${label} must be finite`);
  return value;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

export function assertHardwareGpuReady(systemInfo) {
  const gpu = object(object(systemInfo, "Chromium SystemInfo").gpu, "Chromium GPU info");
  const auxiliary = object(gpu.auxAttributes, "Chromium GPU auxiliary attributes");
  assert.equal(auxiliary.hardwareSupportsVulkan, true, "Chromium must initialize hardware Vulkan");
  assert.match(text(auxiliary.glImplementationParts, "GL implementation"), /angle=vulkan/);
  const renderer = text(auxiliary.glRenderer, "GPU startup renderer");
  assert(!/swiftshader|llvmpipe|software/i.test(renderer), "GPU startup renderer must be hardware");
  assert.equal(auxiliary.processCrashCount, 0, "GPU process crashed during startup");
  return { renderer, implementation: auxiliary.glImplementationParts,
    initializationTimeSeconds: number(auxiliary.initializationTime, "GPU initialization time"),
    processCrashCount: auxiliary.processCrashCount };
}

export function assertCleanBrowserEvidence(report) {
  assert.deepEqual({ pageErrors: report.pageErrors, consoleErrors: report.consoleErrors,
    requestFailures: report.requestFailures, httpErrors: report.httpErrors,
    webglContextLosses: report.observations.webglContextLosses },
  { pageErrors: [], consoleErrors: [], requestFailures: [], httpErrors: [], webglContextLosses: [] },
  "Static acceptance requires zero browser, transport and context errors");
}

export function parseHarnessArguments(argv) {
  const originUrl = new URL(argv[0] ?? "http://127.0.0.1:5393");
  assert.equal(originUrl.protocol, "http:", "Vite origin must use loopback HTTP");
  assert(["127.0.0.1", "localhost"].includes(originUrl.hostname), "Vite origin must be loopback");
  assert.equal(originUrl.pathname, "/", "Vite origin must not contain a path");
  assert.equal(originUrl.search, "", "Vite origin must not contain a query");
  assert.equal(originUrl.hash, "", "Vite origin must not contain a fragment");
  const outputDir = resolve(argv[1]
    ?? "../validation/codex-takeover-20261001/N/native-city");
  const registrationId = argv[2] ?? "";
  assert.match(registrationId, REGISTRATION_ID,
    "An explicit native-city registration ID is required as the third argument");
  const timeoutMs = argv[3] === undefined ? 1_200_000 : Number(argv[3]);
  assert(Number.isSafeInteger(timeoutMs) && timeoutMs >= 120_000 && timeoutMs <= 3_600_000,
    "Optional timeout must be an integer from 120000 to 3600000 milliseconds");
  return Object.freeze({ origin: originUrl.origin, outputDir, registrationId, timeoutMs });
}

export function derivePresentationInventory(scenario) {
  const root = object(scenario, "PublicScenario");
  assert.equal(root.schema_version, "aero-bench.public-scenario/v1");
  assert.equal(root.scenario_digest, EXPECTED.scenarioDigest);
  assert.equal(root.replay_mode, EXPECTED.replayMode);
  assert.equal(root.world_id, EXPECTED.worldId);
  assert.equal(root.world_digest, EXPECTED.worldDigest);
  const assets = array(root.assets, "PublicScenario.assets");
  const assetById = new Map();
  for (const rawAsset of assets) {
    const asset = object(rawAsset, "PublicScenario asset");
    const id = text(asset.asset_id, "asset_id");
    assert(!assetById.has(id), `duplicate PublicScenario asset ${id}`);
    assert.match(text(asset.sha256, `${id}.sha256`), SHA256);
    assert(Number.isSafeInteger(asset.size_bytes) && asset.size_bytes > 0,
      `${id}.size_bytes must be a positive safe integer`);
    assetById.set(id, asset);
  }
  const layers = array(root.layers, "PublicScenario.layers");
  const layerKinds = layers.map(raw => text(object(raw, "PublicScenario layer").kind, "layer.kind")).sort();
  assert.deepEqual(layerKinds, EXPECTED.layerKinds);
  assert(!layerKinds.includes("osm_mesh"), "native city must not declare osm_mesh");
  const buildings = array(root.buildings, "PublicScenario.buildings");
  assert.equal(buildings.length, EXPECTED.buildingCount);
  const presentationIds = [
    ...layers.map(raw => text(object(raw, "PublicScenario layer").asset_id, "layer.asset_id")),
    ...buildings.map(raw => text(object(raw, "PublicScenario building").render_asset_id,
      "building.render_asset_id")),
  ];
  assert.equal(new Set(presentationIds).size, EXPECTED.presentationAssetCount,
    "presentation assets must be one-to-one");
  const presentationAssets = presentationIds.map(id => {
    const asset = assetById.get(id);
    assert(asset !== undefined, `presentation asset is undeclared: ${id}`);
    return asset;
  });
  const glbs = assets.filter(asset => asset.media_type === "model/gltf-binary");
  assert.equal(glbs.length, EXPECTED.buildingCount);
  assert(glbs.every(asset => presentationIds.includes(asset.asset_id)),
    "PublicScenario contains a foreign GLB");
  const totalBytes = presentationAssets.reduce((total, asset) => total + asset.size_bytes, 0);
  assert.equal(totalBytes, EXPECTED.presentationBytes);
  const digests = presentationAssets.map(asset => asset.sha256);
  assert.equal(new Set(digests).size, EXPECTED.presentationAssetCount,
    "presentation SHA-256 values must be one-to-one");
  const knownBuilding = buildings.find(raw => raw.building_id === EXPECTED.knownBuildingId);
  assert(knownBuilding !== undefined, "known building is absent");
  assert.equal(knownBuilding.entity_id, EXPECTED.knownEntityId);
  assert.equal(knownBuilding.render_asset_id, EXPECTED.knownRenderAssetId);
  const knownAsset = assetById.get(EXPECTED.knownRenderAssetId);
  assert.equal(knownAsset?.sha256, EXPECTED.knownRenderSha256);
  const entities = array(root.entities, "PublicScenario.entities");
  const knownEntity = entities.find(raw => raw.entity_id === EXPECTED.knownEntityId);
  assert(knownEntity !== undefined, "known building entity is absent");
  assert.equal(knownEntity.model_asset_id, EXPECTED.knownRenderAssetId);
  assert.deepEqual(root.frame_authority.origin.wgs84, EXPECTED.originWgs84);
  assert.deepEqual(root.frame_authority.spatial_extent, {
    ...EXPECTED.spatialExtent, vertical_reference: "enu_up",
  });
  return Object.freeze({
    presentationAssets: Object.freeze(presentationAssets),
    expectedDigests: Object.freeze([...digests].sort()),
    totalBytes,
    knownBuilding,
    knownEntity,
  });
}

export function nativeAssetDigest(pathname, registrationId) {
  const prefix = `/authoring/v1/native-scenes/${registrationId}/assets/`;
  if (!pathname.startsWith(prefix)) return null;
  const digest = pathname.slice(prefix.length);
  return SHA256.test(digest) ? digest : null;
}

export function assertNativeRequestInventory(records, registrationId, expectedDigests) {
  const scenePath = `/authoring/v1/native-scenes/${registrationId}/scene`;
  const assetResponses = records.responses.filter(item =>
    nativeAssetDigest(item.pathname, registrationId) !== null);
  const assetDigests = assetResponses.map(item => nativeAssetDigest(item.pathname, registrationId));
  const sortedObservedDigests = [...assetDigests].sort();
  assert.deepEqual(sortedObservedDigests, expectedDigests,
    "browser asset responses differ from the declared presentation inventory");
  assert(assetResponses.every(item => item.status === 200), "one or more native asset responses failed");
  assert.equal(records.responses.filter(item => item.pathname === scenePath && item.status === 200).length, 1,
    "browser must receive the selected native scene exactly once");
  const allowed = new Set([scenePath, ...expectedDigests.map(digest =>
    `/authoring/v1/native-scenes/${registrationId}/assets/${digest}`)]);
  const foreignRequests = records.requests.filter(item => {
    if (item.protocol === "blob:") return false;
    if (item.origin !== records.origin) return true;
    return !allowed.has(item.pathname);
  });
  assert.deepEqual(foreignRequests, [], "native selection requested foreign preview assets");
  assert(records.requests.every(item => !item.pathname.includes(EXPECTED.knownBuildingId)
      && !item.pathname.includes(EXPECTED.knownEntityId)),
  "native asset requests must be digest-addressed, not entity-addressed");
  return {
    sceneResponseCount: 1,
    assetResponseCount: assetResponses.length,
    declaredDigestSetSha256: sha256(Buffer.from(expectedDigests.join("\n"), "utf8")),
    observedDigestSetSha256: sha256(Buffer.from(sortedObservedDigests.join("\n"), "utf8")),
    embeddedTextureRequestCount: records.requests.filter(item => item.protocol === "blob:").length,
    foreignRequestCount: foreignRequests.length,
  };
}

function scenarioEnvelope(building) {
  const points = [...array(building.base_vertices, "known building base vertices"),
    ...array(building.top_vertices, "known building top vertices")]
    .map(raw => object(raw, "known building vertex").enu)
    .map(enu => ({ x: number(enu.east_m, "vertex east"), y: number(enu.up_m, "vertex up"),
      z: -number(enu.north_m, "vertex north") }));
  return {
    min: { x: Math.min(...points.map(point => point.x)), y: Math.min(...points.map(point => point.y)),
      z: Math.min(...points.map(point => point.z)) },
    max: { x: Math.max(...points.map(point => point.x)), y: Math.max(...points.map(point => point.y)),
      z: Math.max(...points.map(point => point.z)) },
  };
}

function expectedLogicalPose(entity) {
  const pose = entity.initial_pose;
  const position = pose.position.enu;
  const orientation = pose.orientation_enu;
  return {
    position: { x: position.east_m, y: position.up_m, z: -position.north_m },
    quaternion: { x: orientation.qx, y: orientation.qz, z: -orientation.qy, w: orientation.qw },
  };
}

function close(actual, expected, tolerance, label) {
  assert(Math.abs(actual - expected) <= tolerance,
    `${label} differs: actual ${actual}, expected ${expected}, tolerance ${tolerance}`);
}

function assertVector(actual, expected, tolerance, label) {
  for (const axis of Object.keys(expected)) close(actual[axis], expected[axis], tolerance, `${label}.${axis}`);
}

export async function verifyFrozenBuild(origin) {
  const response = await fetch(`${origin}/capture-build-receipt.json`, { redirect: "error" });
  assert.equal(response.status, 200, "A private frozen capture build is required");
  const receipt = await response.json();
  assert.equal(receipt.schemaVersion, "aero-bench.native-capture-build/v1");
  assert.deepEqual(receipt.hooks, ["src/app.ts", "src/city-studio.ts"]);
  assert(Array.isArray(receipt.artifacts) && receipt.artifacts.length > 0);
  for (const artifact of receipt.artifacts) {
    assert.match(artifact.path, /^(?:assets\/[\w.-]+|[\w-]+\.html)$/);
    assert.match(artifact.sha256, SHA256);
    const result = await fetch(`${origin}/${artifact.path}`, { redirect: "error" });
    assert.equal(result.status, 200, `Frozen artifact unavailable: ${artifact.path}`);
    const bytes = Buffer.from(await result.arrayBuffer());
    assert.equal(bytes.length, artifact.sizeBytes);
    assert.equal(sha256(bytes), artifact.sha256, `Frozen artifact changed: ${artifact.path}`);
  }
  return receipt;
}

/** Observe native fetch/stream/abort lifecycles without consuming or replacing bodies. */
export function installNativeRequestTracing() {
  const events = window.__aeroNativeRequestEvents = [];
  const streams = new WeakMap(), readers = new WeakMap(), signals = new WeakMap();
  let nextId = 0;
  const record = (kind, id, details = {}) => events.push({ kind, id, at: performance.now(), ...details });
  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = new URL(input instanceof Request ? input.url : String(input), location.href);
    if (!url.pathname.startsWith("/authoring/v1/native-scenes/")) return originalFetch.call(this, input, init);
    const id = ++nextId;
    const signal = init?.signal ?? (input instanceof Request ? input.signal : null);
    record("fetch", id, { pathname: url.pathname, stack: new Error().stack, hasSignal: signal !== null });
    if (signal) {
      signals.set(signal, id);
      signal.addEventListener("abort", () => record("signal-abort", id,
        { reason: String(signal.reason), stack: new Error().stack }), { once: true });
    }
    const promise = originalFetch.call(this, input, init);
    promise.then(response => {
      if (response.body) streams.set(response.body, id);
      record("headers", id, { status: response.status, length: response.headers.get("content-length") });
    }, error => record("fetch-error", id, { error: String(error) }));
    return promise;
  };
  const originalAbort = AbortController.prototype.abort;
  AbortController.prototype.abort = function(reason) {
    const id = signals.get(this.signal);
    if (id) record("controller-abort", id, { reason: String(reason), stack: new Error().stack });
    return originalAbort.call(this, reason);
  };
  const originalGetReader = ReadableStream.prototype.getReader;
  ReadableStream.prototype.getReader = function(...args) {
    const reader = originalGetReader.apply(this, args), id = streams.get(this);
    if (id) readers.set(reader, { id, total: 0, reads: 0 });
    return reader;
  };
  const originalRead = ReadableStreamDefaultReader.prototype.read;
  ReadableStreamDefaultReader.prototype.read = function(...args) {
    const state = readers.get(this), promise = originalRead.apply(this, args);
    if (state) promise.then(result => {
      state.total += result.value?.byteLength ?? 0; state.reads++;
      if (result.done) record("body-end", state.id, { total: state.total, reads: state.reads });
    }, error => record("read-error", state.id, { error: String(error), total: state.total }));
    return promise;
  };
  const originalCancel = ReadableStreamDefaultReader.prototype.cancel;
  ReadableStreamDefaultReader.prototype.cancel = function(...args) {
    const state = readers.get(this);
    if (state) record("reader-cancel", state.id, { total: state.total, stack: new Error().stack });
    return originalCancel.apply(this, args);
  };
  const originalRelease = ReadableStreamDefaultReader.prototype.releaseLock;
  ReadableStreamDefaultReader.prototype.releaseLock = function(...args) {
    const state = readers.get(this);
    if (state) record("reader-release", state.id, { total: state.total, stack: new Error().stack });
    return originalRelease.apply(this, args);
  };
}

async function rendererIdentity(page) {
  return page.evaluate(() => {
    const map = window.__aeroStudioMap;
    if (map === undefined) throw new Error("Studio map hook is unavailable");
    const gl = map.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      renderer: String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER)),
      vendor: String(gl.getParameter(debug?.UNMASKED_VENDOR_WEBGL ?? gl.VENDOR)),
      contextLost: gl.isContextLost(),
      error: gl.getError(),
    };
  });
}

async function addEvidenceAnnotation(page) {
  await page.locator("#studio-map").evaluate((root, lines) => {
    document.querySelector("[data-native-evidence-annotation]")?.remove();
    const annotation = document.createElement("div");
    annotation.dataset.nativeEvidenceAnnotation = "fixture-envelope-provenance";
    // Place the note in the open map area below the monitor clock so it never covers monitor panels.
    const rootRect = root.getBoundingClientRect();
    const anchor = root.querySelector(".operations-monitor-header")?.getBoundingClientRect();
    const left = anchor === undefined ? 18 : Math.round(anchor.left - rootRect.left);
    const top = anchor === undefined ? 18 : Math.round(anchor.bottom - rootRect.top + 8);
    annotation.style.cssText = [
      "position:absolute", `left:${left}px`, `top:${top}px`, "z-index:50",
      `max-width:${anchor === undefined ? 520 : Math.max(240, Math.round(anchor.width))}px`,
      "padding:10px 13px", "border:1px solid rgba(139,215,218,.56)", "border-radius:4px",
      "background:rgba(5,22,31,.88)", "color:#e3f4f4", "font:600 12px/1.55 system-ui,sans-serif",
      "letter-spacing:.02em", "box-shadow:0 8px 24px rgba(0,0,0,.34)", "pointer-events:none",
    ].join(";");
    annotation.replaceChildren(...lines.flatMap((line, index) => {
      const item = document.createElement("div");
      item.textContent = line;
      item.style.color = index === 0 ? "#9be1df" : index === 2 ? "#e7c789" : "#d4e7e8";
      return [item];
    }));
    root.append(annotation);
    const noteRect = annotation.getBoundingClientRect();
    const covered = [...root.querySelectorAll(
      ".operations-monitor-header, .operations-monitor-sidebar, .operations-monitor-spatial, .operations-monitor-events",
    )].filter(panel => {
      const rect = panel.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && noteRect.left < rect.right && rect.left < noteRect.right
        && noteRect.top < rect.bottom && rect.top < noteRect.bottom;
    }).map(panel => panel.className);
    if (covered.length > 0) throw new Error(`evidence annotation overlaps monitor panels: ${covered.join(", ")}`);
  }, ANNOTATION_LINES);
}

async function renderCamera(page, kind, knownBuildingId) {
  return page.evaluate(({ viewKind, buildingId }) => {
    const map = window.__aeroStudioMap;
    const native = map?.nativePresentation;
    if (map === undefined || native === null || native === undefined) {
      throw new Error("mounted native presentation is unavailable");
    }
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation);
    map.previewAnimation = 0;
    map.controls.enableDamping = false;
    map.setCameraMode("free");
    const render = () => {
      map.controls.update();
      map.renderer.render(map.scene, map.camera);
    };
    let subject;
    if (viewKind === "overhead") {
      const bounds = native.bounds;
      const center = bounds.getCenter(map.camera.position.clone());
      const size = bounds.getSize(map.camera.position.clone());
      const span = Math.max(size.x, size.z, 1);
      map.camera.position.set(center.x + span * 0.08, bounds.max.y + span * 0.88,
        center.z + span * 0.12);
      map.controls.target.set(center.x, Math.max(0, center.y * 0.15), center.z);
      subject = { center: { x: center.x, y: center.y, z: center.z }, span };
    } else if (viewKind === "street") {
      const road = native.layers.roads.children.find(child => child.name === "Accepted asphalt roadbed");
      if (road?.geometry?.index === null || road?.geometry?.index === undefined) {
        throw new Error("accepted asphalt roadbed geometry is unavailable");
      }
      const position = road.geometry.getAttribute("position");
      const index = road.geometry.index;
      const sceneCenter = native.bounds.getCenter(map.camera.position.clone());
      const candidates = [];
      for (let offset = 0; offset + 2 < index.count; offset += 3) {
        const ids = [index.getX(offset), index.getX(offset + 1), index.getX(offset + 2)];
        const points = ids.map(id => ({ x: position.getX(id), z: position.getZ(id) }));
        const center = { x: (points[0].x + points[1].x + points[2].x) / 3,
          z: (points[0].z + points[1].z + points[2].z) / 3 };
        const edges = [[points[0], points[1]], [points[1], points[2]], [points[2], points[0]]]
          .map(([a, b]) => ({ x: b.x - a.x, z: b.z - a.z,
            length: Math.hypot(b.x - a.x, b.z - a.z) }))
          .sort((left, right) => right.length - left.length);
        const area = Math.abs((points[1].x - points[0].x) * (points[2].z - points[0].z)
          - (points[1].z - points[0].z) * (points[2].x - points[0].x)) / 2;
        if (area > 1 && edges[0].length > 3) candidates.push({ center, direction: edges[0], area,
          distance: Math.hypot(center.x - sceneCenter.x, center.z - sceneCenter.z) });
      }
      candidates.sort((left, right) => left.distance - right.distance || right.area - left.area);
      const selected = candidates[0];
      if (selected === undefined) throw new Error("no stable native road camera candidate exists");
      const length = selected.direction.length;
      const direction = { x: selected.direction.x / length, z: selected.direction.z / length };
      map.camera.position.set(selected.center.x, 2.4, selected.center.z);
      map.controls.target.set(selected.center.x + direction.x * 90, 3.1,
        selected.center.z + direction.z * 90);
      subject = { roadPoint: selected.center, direction, triangleAreaM2: selected.area };
    } else if (viewKind === "known-building") {
      const visual = native.layers.buildings.children.find(child => child.userData.target?.id === buildingId);
      if (visual === undefined) throw new Error(`known native building is unavailable: ${buildingId}`);
      const bounds = native.bounds.clone().makeEmpty();
      visual.traverse(node => {
        if (!node.isMesh) return;
        node.geometry.computeBoundingBox();
        if (node.geometry.boundingBox !== null) {
          bounds.union(node.geometry.boundingBox.clone().applyMatrix4(node.matrixWorld));
        }
      });
      const center = bounds.getCenter(map.camera.position.clone());
      const size = bounds.getSize(map.camera.position.clone());
      const span = Math.max(size.x, size.y, size.z, 12);
      const cityCenter = native.bounds.getCenter(map.controls.target.clone());
      let outwardX = center.x - cityCenter.x, outwardZ = center.z - cityCenter.z;
      const outwardLength = Math.hypot(outwardX, outwardZ) || 1;
      outwardX /= outwardLength; outwardZ /= outwardLength;
      map.camera.position.set(center.x + outwardX * span * 2.1, center.y + span * 0.72,
        center.z + outwardZ * span * 2.1);
      map.controls.target.set(center.x, center.y * 0.58, center.z);
      subject = { buildingId, center: { x: center.x, y: center.y, z: center.z },
        size: { x: size.x, y: size.y, z: size.z } };
    } else {
      throw new Error(`unknown camera view ${viewKind}`);
    }
    render(); render(); render();
    return {
      kind: viewKind,
      position: { x: map.camera.position.x, y: map.camera.position.y, z: map.camera.position.z },
      target: { x: map.controls.target.x, y: map.controls.target.y, z: map.controls.target.z },
      subject,
      rendererTextures: map.renderer.info.memory.textures,
    };
  }, { viewKind: kind, buildingId: knownBuildingId });
}

async function inspectMountedPresentation(page, inventory) {
  const observation = await page.evaluate(expected => {
    const map = window.__aeroStudioMap;
    const native = map?.nativePresentation;
    const root = document.querySelector("#studio-map");
    if (map === undefined || native === null || native === undefined || !(root instanceof HTMLElement)) {
      throw new Error("native map presentation is not mounted");
    }
    const known = native.layers.buildings.children.find(child =>
      child.userData.target?.id === expected.knownBuildingId);
    if (known === undefined) throw new Error("known building visual is absent");
    const actualBounds = native.bounds.clone().makeEmpty();
    known.traverse(node => {
      if (!node.isMesh) return;
      node.geometry.computeBoundingBox();
      if (node.geometry.boundingBox !== null) {
        actualBounds.union(node.geometry.boundingBox.clone().applyMatrix4(node.matrixWorld));
      }
    });
    const textures = new Map();
    let meshCount = 0, materialCount = 0, texturedBuildingCount = 0;
    const buildingsWithoutTexture = [];
    for (const building of native.layers.buildings.children) {
      let textured = false;
      building.traverse(node => {
        if (!node.isMesh) return;
        meshCount += 1;
        for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
          materialCount += 1;
          for (const value of Object.values(material)) if (value?.isTexture === true) {
            textures.set(value.uuid, value);
            if (value === material.map) textured = true;
          }
        }
      });
      if (textured) texturedBuildingCount += 1;
      else buildingsWithoutTexture.push(building.name);
    }
    const invalidTextureSources = [];
    for (const [uuid, texture] of textures) {
      const data = texture.source?.data ?? texture.image;
      const sources = Array.isArray(data) ? data : [data];
      if (sources.length === 0 || sources.some(source => source === null || source === undefined
          || !(Number(source.width) > 0 && Number(source.height) > 0)
            && !(source.data !== undefined && Number(source.data.length) > 0))) {
        invalidTextureSources.push(uuid);
      }
    }
    const fixture = native.layers.fixtures;
    const fallback = {
      packedScene: map.packedScene !== null,
      buildingPresentation: map.buildingPresentation !== null,
      roadPresentation: map.roadPresentation !== null,
      vegetationPresentation: map.vegetationPresentation !== null,
      cityVegetationLayer: map.cityVegetationLayer !== null,
      trafficPreview: map.trafficPreview !== null,
      renderStreamer: map.renderStreamer !== null,
    };
    const nativeGroups = map.scene.children.filter(child => child.userData.nativeCityPresentation === true);
    const statsAttribute = JSON.parse(root.dataset.nativeCityStats ?? "null");
    return {
      dataset: { ...root.dataset },
      group: {
        name: native.group.name,
        scenarioDigest: native.group.userData.scenarioDigest,
        worldDigest: native.group.userData.worldDigest,
        nativeGroupCount: nativeGroups.length,
        mounted: native.group.parent === map.scene,
      },
      stats: native.stats,
      statsAttribute,
      bounds: { min: { x: native.bounds.min.x, y: native.bounds.min.y, z: native.bounds.min.z },
        max: { x: native.bounds.max.x, y: native.bounds.max.y, z: native.bounds.max.z } },
      knownBuilding: {
        name: known.name,
        entityId: known.userData.entityId,
        renderAssetId: known.userData.renderAssetId,
        renderSha256: known.userData.renderSha256,
        position: { x: known.position.x, y: known.position.y, z: known.position.z },
        quaternion: { x: known.quaternion.x, y: known.quaternion.y,
          z: known.quaternion.z, w: known.quaternion.w },
        bounds: { min: { x: actualBounds.min.x, y: actualBounds.min.y, z: actualBounds.min.z },
          max: { x: actualBounds.max.x, y: actualBounds.max.y, z: actualBounds.max.z } },
      },
      texture: { buildingCount: native.layers.buildings.children.length, meshCount, materialCount,
        texturedBuildingCount, textureObjectCount: textures.size,
        rendererTextureCount: map.renderer.info.memory.textures,
        buildingsWithoutTexture, invalidTextureSources },
      fixture: { name: fixture.name, nativeSource: fixture.userData.nativeSource,
        geometryMeaning: fixture.userData.geometryMeaning,
        fixtureCount: fixture.userData.fixtureCount,
        omittedInteriorRingCount: fixture.userData.omittedFixtureInteriorRingCount },
      fallback,
      sourceKey: map.sourceKey,
      projectionOrigin: map.projectionOrigin,
      entityModelError: root.dataset.entityModelError ?? null,
      webglContextLosses: window.__aeroWebglContextLosses ?? [],
    };
  }, { knownBuildingId: EXPECTED.knownBuildingId });

  assert.equal(observation.dataset.nativeCityPresentation, "verified-declared-assets");
  assert.equal(observation.dataset.sceneReady, "true");
  assert.equal(observation.dataset.texturesReady, "true");
  assert.equal(observation.dataset.sceneError, undefined);
  assert.equal(observation.dataset.buildingRenderTotal, undefined);
  assert.equal(observation.dataset.roadVisual, undefined);
  assert.equal(observation.dataset.sceneSource, undefined);
  assert.deepEqual(observation.statsAttribute, observation.stats);
  assert.equal(observation.stats.buildingCount, EXPECTED.buildingCount);
  assert.equal(observation.stats.verifiedBytes, EXPECTED.presentationBytes);
  assert(observation.stats.roadbedPolygonCount > 0);
  assert(observation.stats.walkbedPolygonCount > 0);
  assert(observation.stats.fixtureCount > 0);
  assert(observation.stats.osmElementCount > 0);
  assert.equal(observation.group.name, `Native city assets: ${EXPECTED.worldId}`);
  assert.equal(observation.group.scenarioDigest, EXPECTED.scenarioDigest);
  assert.equal(observation.group.worldDigest, EXPECTED.worldDigest);
  assert.equal(observation.group.nativeGroupCount, 1);
  assert.equal(observation.group.mounted, true);
  assert.equal(observation.sourceKey, EXPECTED.scenarioDigest);
  assert.deepEqual(observation.projectionOrigin, EXPECTED.originWgs84);
  assert.equal(observation.knownBuilding.name, EXPECTED.knownBuildingId);
  assert.equal(observation.knownBuilding.entityId, EXPECTED.knownEntityId);
  assert.equal(observation.knownBuilding.renderAssetId, EXPECTED.knownRenderAssetId);
  assert.equal(observation.knownBuilding.renderSha256, EXPECTED.knownRenderSha256);
  assertVector(observation.knownBuilding.position, expectedLogicalPose(inventory.knownEntity).position,
    1e-9, "known building logical position");
  assertVector(observation.knownBuilding.quaternion, expectedLogicalPose(inventory.knownEntity).quaternion,
    1e-9, "known building logical quaternion");
  const envelope = scenarioEnvelope(inventory.knownBuilding);
  assertVector(observation.knownBuilding.bounds.min, envelope.min, 0.001,
    "known building measured bounds.min");
  assertVector(observation.knownBuilding.bounds.max, envelope.max, 0.001,
    "known building measured bounds.max");
  assert.equal(observation.texture.buildingCount, EXPECTED.buildingCount);
  assert.equal(observation.texture.texturedBuildingCount, EXPECTED.buildingCount);
  assert.equal(observation.texture.buildingsWithoutTexture.length, 0);
  assert.equal(observation.texture.invalidTextureSources.length, 0);
  assert(observation.texture.textureObjectCount >= EXPECTED.buildingCount,
    "decoded building texture inventory is incomplete");
  assert(observation.texture.rendererTextureCount >= EXPECTED.buildingCount,
    "hardware renderer did not allocate the building texture inventory");
  assert.equal(observation.fixture.name, "Declared effective fixture geometry envelopes");
  assert.equal(observation.fixture.nativeSource, "aero-bench.city-effective-fixture-geometry/v1");
  assert.equal(observation.fixture.geometryMeaning,
    "measured-plan-outer-envelope-extrusion-not-source-model-mesh");
  assert.equal(observation.fixture.fixtureCount, observation.stats.fixtureCount);
  assert.deepEqual(observation.fallback, {
    packedScene: false,
    buildingPresentation: false,
    roadPresentation: false,
    vegetationPresentation: false,
    cityVegetationLayer: false,
    trafficPreview: false,
    renderStreamer: false,
  });
  assert.equal(observation.entityModelError, null);
  assert.deepEqual(observation.webglContextLosses, []);
  return observation;
}

async function runAcceptance(config) {
  await mkdir(config.outputDir, { recursive: true });
  const report = {
    schemaVersion: "aero-bench.native-city-presentation-browser-acceptance/v1",
    status: "RUNNING",
    scope: "static-native-city-presentation",
    formalRunStatus: "NOT_EXERCISED",
    origin: config.origin,
    registrationId: config.registrationId,
    startedAt: new Date().toISOString(),
    expected: EXPECTED,
    fixtureEnvelopeAnnotation: ANNOTATION_LINES,
    checks: [],
    screenshots: [],
    pageErrors: [],
    consoleErrors: [],
    requestFailures: [],
    httpErrors: [],
    observations: {},
  };
  let browser;
  let context;
  let page;
  let requestStage = "boot";
  const requestStages = new WeakMap();
  const requestStarts = new WeakMap();
  const lifecycle = { playwright: [], cdp: [], browser: [] };
  const nativeNetwork = { origin: config.origin, requests: [], responses: [] };

  const record = async () => {
    report.checkCounts = {
      passed: report.checks.filter(item => item.status === "PASS").length,
      failed: report.checks.filter(item => item.status === "FAIL").length,
      total: report.checks.length,
    };
    await writeFile(resolve(config.outputDir, "report.json"), `${JSON.stringify(report, null, 2)}\n`,
      { encoding: "utf8", mode: 0o600 });
  };

  const check = async (name, action) => {
    const started = performance.now();
    try {
      const details = await action();
      report.checks.push({ name, status: "PASS", durationMs: Math.round(performance.now() - started),
        details: details ?? null });
      await record();
      return details;
    } catch (error) {
      report.checks.push({ name, status: "FAIL", durationMs: Math.round(performance.now() - started),
        error: error instanceof Error ? error.message : String(error) });
      await record();
      throw error;
    }
  };

  const screenshot = async (name, locator = page) => {
    const path = resolve(config.outputDir, name);
    await locator.screenshot({ path, animations: "disabled" });
    const bytes = await readFile(path);
    assert(bytes.length >= 20_000, `${name} is too small to be useful visual evidence`);
    const item = { file: relative(config.outputDir, path), sizeBytes: bytes.length,
      sha256: sha256(bytes) };
    report.screenshots.push(item);
    return item;
  };

  try {
    report.observations.frozenBuild = await verifyFrozenBuild(config.origin);
    browser = await chromium.launch({ channel: "chromium", headless: true, timeout: 60_000,
      args: GPU_ARGS });
    // Complete and record Chromium's GPU initialization before creating the page's
    // WebGL context. The controlled startup probe reproduced loss without this receipt.
    const browserCdp = await browser.newBrowserCDPSession();
    report.observations.gpuStartup = assertHardwareGpuReady(await browserCdp.send("SystemInfo.getInfo"));
    await browserCdp.detach();
    context = await browser.newContext({ ignoreHTTPSErrors: true,
      viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
    page = await context.newPage();
    await page.addInitScript(installNativeRequestTracing);
    await page.addInitScript(() => {
      window.__aeroWebglContextLosses = [];
      document.addEventListener("webglcontextlost", event => {
        window.__aeroWebglContextLosses.push({ at: performance.now(), target: event.target?.tagName ?? null,
          id: event.target?.id ?? null, connected: event.target?.isConnected ?? false,
          width: event.target?.width ?? null, height: event.target?.height ?? null,
          studioRenderer: event.target === window.__aeroStudioMap?.renderer?.domElement });
      }, true);
    });
    page.on("pageerror", error => report.pageErrors.push(error.message));
    page.on("console", message => {
      if (message.type() === "error") report.consoleErrors.push(message.text());
    });
    page.on("request", request => {
      requestStages.set(request, requestStage);
      requestStarts.set(request, performance.now());
      if (requestStage !== "native-selection") return;
      const url = new URL(request.url());
      nativeNetwork.requests.push({ method: request.method(), protocol: url.protocol, origin: url.origin,
        pathname: url.pathname, resourceType: request.resourceType() });
    });
    page.on("response", response => {
      const request = response.request();
      const url = new URL(response.url());
      if (response.status() >= 400) report.httpErrors.push({ stage: requestStages.get(request) ?? "unknown",
        method: request.method(), pathname: url.pathname, status: response.status() });
      if (requestStages.get(request) === "native-selection") {
        nativeNetwork.responses.push({ method: request.method(), protocol: url.protocol, origin: url.origin,
          pathname: url.pathname, status: response.status(), resourceType: request.resourceType() });
      }
    });
    page.on("requestfailed", request => {
      const url = new URL(request.url());
      report.requestFailures.push({ stage: requestStages.get(request) ?? "unknown",
        method: request.method(), origin: url.origin, pathname: url.pathname,
        error: request.failure()?.errorText ?? "failed", at: performance.now(),
        durationMs: performance.now() - requestStarts.get(request) });
    });
    page.on("requestfinished", request => {
      if (new URL(request.url()).pathname.startsWith("/authoring/v1/native-scenes/")) {
        lifecycle.playwright.push({ kind: "finished", pathname: new URL(request.url()).pathname,
          stage: requestStages.get(request), at: performance.now(),
          durationMs: performance.now() - requestStarts.get(request) });
      }
    });
    const cdp = await context.newCDPSession(page);
    const tracked = new Set();
    await cdp.send("Network.enable");
    cdp.on("Network.requestWillBeSent", event => {
      if (new URL(event.request.url).pathname.startsWith("/authoring/v1/native-scenes/")) {
        tracked.add(event.requestId);
        lifecycle.cdp.push({ kind: "request", requestId: event.requestId, timestamp: event.timestamp,
          pathname: new URL(event.request.url).pathname, initiator: event.initiator });
      }
    });
    for (const [eventName, kind] of [["Network.loadingFinished", "finished"],
      ["Network.loadingFailed", "failed"], ["Network.responseReceived", "headers"]]) {
      cdp.on(eventName, event => {
        if (!tracked.has(event.requestId)) return;
        lifecycle.cdp.push({ kind, requestId: event.requestId, timestamp: event.timestamp,
          errorText: event.errorText, canceled: event.canceled, encodedDataLength: event.encodedDataLength,
          response: event.response && { status: event.response.status, headers: event.response.headers,
            protocol: event.response.protocol } });
      });
    }

    let catalog;
    await check("load the public Studio and its native catalog", async () => {
      const catalogResponse = page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === "GET" && url.pathname === "/authoring/v1/native-scenes";
      }, { timeout: config.timeoutMs });
      await page.goto(`${config.origin}/city-studio.html?tab=compile`, {
        waitUntil: "domcontentloaded", timeout: 60_000,
      });
      assert.equal(await page.evaluate(() => window.__aeroNativeCaptureBuild),
        "aero-bench.native-capture-build/v1", "Only a frozen instrumented build may be captured");
      const response = await catalogResponse;
      assert.equal(response.status(), 200, `native catalog HTTP ${response.status()}`);
      catalog = await response.json();
      assert.equal(catalog.schema_version, "aero-bench.city-scene-registration-catalog/v1");
      const registration = catalog.registrations.find(item => item.registration_id === config.registrationId);
      assert(registration !== undefined, `catalog does not publish ${config.registrationId}`);
      assert.equal(registration.registration_sha256, EXPECTED.registrationSha256);
      assert.equal(registration.profile_id, EXPECTED.profileId);
      assert.equal(registration.scene_sha256, EXPECTED.scenarioSha256);
      assert.equal(registration.scene_size_bytes, EXPECTED.scenarioSizeBytes);
      assert.equal(registration.world_id, EXPECTED.worldId);
      assert.equal(registration.world_digest, EXPECTED.worldDigest);
      assert.equal(registration.scene_url,
        `/authoring/v1/native-scenes/${config.registrationId}/scene`);
      const selection = page.getByLabel("原生场景注册", { exact: true });
      await selection.waitFor({ state: "visible", timeout: config.timeoutMs });
      await selection.selectOption(config.registrationId);
      assert.equal(await selection.inputValue(), config.registrationId);
      await screenshot("00-native-catalog-selected.png");
      return { registrationId: registration.registration_id, registrationSha256: registration.registration_sha256,
        profileId: registration.profile_id, catalogCount: catalog.registrations.length };
    });

    await check("settle the initial default city before measuring native requests", async () => {
      await page.locator('#studio-map[data-scene-ready="true"][data-textures-ready="true"]')
        .waitFor({ timeout: config.timeoutMs });
      await page.locator("#studio-map .city-scene-loading")
        .waitFor({ state: "hidden", timeout: config.timeoutMs });
      await page.waitForFunction(() => window.__aeroStudioMap !== undefined
          && (window.__aeroStudioMap.renderStreamer?.progress.active ?? 0) === 0,
      undefined, { timeout: config.timeoutMs, polling: 500 });
      await page.waitForTimeout(800);
      return { ready: true };
    });

    let scenario;
    let inventory;
    await check("read and hash the exact public scenario in the browser", async () => {
      const registration = catalog.registrations.find(item => item.registration_id === config.registrationId);
      requestStage = "scenario-preflight";
      const loaded = await page.evaluate(async sceneUrl => {
        const response = await fetch(sceneUrl, { redirect: "error", headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`native scenario HTTP ${response.status}`);
        const bytes = await response.arrayBuffer();
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
          byte => byte.toString(16).padStart(2, "0")).join("");
        const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        return { sizeBytes: bytes.byteLength, sha256: digest, scenario: JSON.parse(text) };
      }, registration.scene_url);
      assert.equal(loaded.sizeBytes, EXPECTED.scenarioSizeBytes);
      assert.equal(loaded.sha256, EXPECTED.scenarioSha256);
      scenario = loaded.scenario;
      inventory = derivePresentationInventory(scenario);
      return { scenarioSha256: loaded.sha256, scenarioSizeBytes: loaded.sizeBytes,
        scenarioDigest: scenario.scenario_digest, worldId: scenario.world_id,
        worldDigest: scenario.world_digest, presentationAssets: inventory.presentationAssets.length,
        presentationBytes: inventory.totalBytes };
    });

    await check("select and load the exact declared native city through Studio", async () => {
      const registration = catalog.registrations.find(item => item.registration_id === config.registrationId);
      requestStage = "native-selection";
      const sceneResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === "GET" && url.pathname === registration.scene_url;
      }, { timeout: config.timeoutMs });
      await page.getByRole("button", { name: "载入所选注册的参考草稿", exact: true }).click();
      const sceneResponse = await sceneResponsePromise;
      assert.equal(sceneResponse.status(), 200, `native scenario HTTP ${sceneResponse.status()}`);
      await page.locator('#studio-preview-state[data-state="ready"]')
        .filter({ hasText: "原生城市呈现已就绪，尚未运行" })
        .waitFor({ timeout: config.timeoutMs });
      await page.locator('#studio-map[data-native-city-presentation="verified-declared-assets"]'
        + '[data-scene-ready="true"][data-textures-ready="true"]')
        .waitFor({ timeout: config.timeoutMs });
      await page.locator("#studio-map .city-scene-loading")
        .waitFor({ state: "hidden", timeout: config.timeoutMs });
      await page.waitForTimeout(500);
      return { registrationId: config.registrationId, status: "ready",
        nativeCityPresentation: await page.locator("#studio-map")
          .getAttribute("data-native-city-presentation") };
    });

    await check("capture a declared-asset overhead frame", async () => {
      await addEvidenceAnnotation(page);
      const camera = await renderCamera(page, "overhead", EXPECTED.knownBuildingId);
      await page.waitForTimeout(180);
      const image = await screenshot("01-native-city-overhead.png", page.locator("#studio-map"));
      return { camera, image };
    });

    const mounted = await check("verify mounted identities, ENU placement, fixture provenance, and GPU textures",
      async () => {
        const observation = await inspectMountedPresentation(page, inventory);
        const renderer = await rendererIdentity(page);
        assert(!/swiftshader|llvmpipe|software/i.test(renderer.renderer),
          `software renderer reported: ${renderer.renderer}`);
        assert.equal(renderer.contextLost, false);
        assert.equal(renderer.error, 0, `WebGL error ${renderer.error}`);
        return { ...observation, renderer };
      });
    report.observations.presentation = mounted;

    await check("capture a declared-road street frame", async () => {
      const camera = await renderCamera(page, "street", EXPECTED.knownBuildingId);
      await page.waitForTimeout(180);
      const image = await screenshot("02-native-city-street.png", page.locator("#studio-map"));
      return { camera, image };
    });

    await check("capture the recorded known-building frame", async () => {
      const camera = await renderCamera(page, "known-building", EXPECTED.knownBuildingId);
      await page.waitForTimeout(180);
      const image = await screenshot("03-native-city-known-building.png", page.locator("#studio-map"));
      return { camera, image, buildingId: EXPECTED.knownBuildingId,
        entityId: EXPECTED.knownEntityId, renderSha256: EXPECTED.knownRenderSha256 };
    });

    const network = await check("verify the browser fetched only the selected digest-addressed presentation", async () => {
      const details = assertNativeRequestInventory(nativeNetwork, config.registrationId,
        inventory.expectedDigests);
      return { ...details, requests: nativeNetwork.requests.length,
        responses: nativeNetwork.responses.length };
    });
    report.observations.network = network;
    requestStage = "complete";

    await check("confirm the static acceptance has no browser or request errors", async () => {
      report.observations.webglContextLosses = await page.evaluate(() =>
        window.__aeroWebglContextLosses ?? []);
      assertCleanBrowserEvidence(report);
      return { pageErrors: 0, consoleErrors: 0, requestFailures: 0, httpErrors: 0,
        formalRunStatus: report.formalRunStatus };
    });
  } catch (error) {
    report.failure = error instanceof Error ? error.stack ?? error.message : String(error);
  } finally {
    if (page && !page.isClosed()) {
      lifecycle.browser = await page.evaluate(() => window.__aeroNativeRequestEvents ?? [])
        .catch(error => [{ kind: "diagnostic-read-error", error: String(error) }]);
      report.observations.webglContextLosses = await page.evaluate(() =>
        window.__aeroWebglContextLosses ?? [])
        .catch(error => [{ kind: "diagnostic-read-error", error: String(error) }]);
    }
    await writeFile(resolve(config.outputDir, "request-lifecycle.json"), JSON.stringify(lifecycle, null, 2) + "\n");
    requestStage = "cleanup";
    await context?.close().catch(() => undefined);
    await browser?.close().catch(() => undefined);
    report.completedAt = new Date().toISOString();
    report.status = report.failure === undefined && report.checks.length > 0
      && report.checks.every(item => item.status === "PASS") ? "PASS" : "FAIL";
    await record();
    process.stdout.write(`${JSON.stringify({ status: report.status, output: config.outputDir,
      checks: report.checkCounts, screenshots: report.screenshots.length,
      failure: report.failure ?? null }, null, 2)}\n`);
    if (report.status !== "PASS") process.exitCode = 1;
  }
}

const isMain = process.argv[1] !== undefined
  && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) {
  let config;
  try {
    config = parseHarnessArguments(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 2;
  }
  if (config !== undefined) await runAcceptance(config);
}
