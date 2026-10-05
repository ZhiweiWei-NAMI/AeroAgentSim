/**
 * Live renderer audit of the default city's source-tagged ground covers (C6b).
 *
 * The viewer exports a construction-time drawn set (`data-city-ground-cover-drawn-set`):
 * it proves that one mesh exists per drawable source polygon, not that the renderer draws
 * it. This script observes the live WebGL renderer instead:
 *
 *   1. Every mesh carrying `userData.groundCoverId` in the live `map.scene` gets an
 *      `onAfterRender` observer. three.js calls it only from the main render path (shadow
 *      passes use `onAfterShadow`), and the observer only counts calls whose camera is the
 *      viewer camera, so reflection-probe cube captures are excluded.
 *   2. For every stored camera, plus one computed full-extent top-down camera, the scene is
 *      frozen at a fixed preview second, streamed to idle, rendered once through the
 *      production `renderPreviewFrame()`, and the observed IDs are compared with the IDs
 *      whose world bounds intersect that camera frustum (per-view omission = expected but
 *      not drawn).
 *   3. The union of renderer-observed IDs is written as a
 *      `aero-bench.city-ground-cover-drawn-set/v1` report (`live-drawn-set.json`) so the
 *      independent `classify-city-ground.py --drawn-set` check can consume it.
 *
 * Matched captures: the plain frame for every audited camera, plus a diagnostic frame with
 * the ground-cover materials temporarily tinted (emissive magenta) at the same camera so
 * the drawn polygons are visually identifiable. Tinted frames are diagnostics only.
 *
 * Usage:
 *   node frontend/scripts/audit-city-ground-cover-live.mjs \
 *     --origin=http://127.0.0.1:5400 \
 *     --output=validation/platform-plan-20261001/E/c6 \
 *     --cameras=validation/platform-plan-20261001/E1-browser-v2/cameras.json
 */
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const GPU_ARGS = [
  "--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
  "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist",
];
const TIMEOUT_MS = 300_000;
const PREVIEW_SECOND = 34;

function parseArgs(argv) {
  const result = {};
  for (const argument of argv) {
    const match = /^--([^=]+)=(.*)$/.exec(argument);
    if (match === null) throw new Error(`Arguments must use --name=value form: ${argument}`);
    result[match[1]] = match[2];
  }
  return result;
}

const options = parseArgs(process.argv.slice(2));
if (options.origin === undefined) throw new Error("--origin is required (a Vite source server)");
const origin = options.origin;
const output = resolve(options.output ?? "validation/platform-plan-20261001/E/c6");
const scenePath = options.scene ?? "/city-presentation/default-scene-v1.json";
// Optional: the classify-city-ground.py report whose parser-accepted green IDs are compared
// with the live grass draw (the green half of the rendering-omission check).
const classificationReportPath = options["classification-report"] === undefined ? null
  : resolve(options["classification-report"]);
const camerasPath = resolve(options.cameras ?? "validation/platform-plan-20261001/E1-browser-v2/cameras.json");
const cameras = JSON.parse(await readFile(camerasPath, "utf8"));
if (!Array.isArray(cameras) || cameras.length === 0 || cameras.some(camera => typeof camera.name !== "string"
    || !Array.isArray(camera.eye) || !Array.isArray(camera.target))) {
  throw new Error(`${camerasPath} must be a non-empty array of named eye/target cameras`);
}

async function capture(page, name, details) {
  const file = resolve(output, "frames", `${name}.png`);
  await mkdir(resolve(output, "frames"), { recursive: true });
  await page.locator("#city-map canvas").first().screenshot({ path: file });
  const bytes = await readFile(file);
  return { file: `frames/${name}.png`, sha256: createHash("sha256").update(bytes).digest("hex"),
    sizeBytes: bytes.length, ...details };
}

/** Installed in the page once the city is ready. */
function installObserver() {
  const map = window.__aeroVisualMap;
  if (map === undefined) throw new Error("The source app map hook did not expose the viewer map");
  const meshes = [];
  map.scene.traverse(object => {
    if (object.userData.groundCoverId !== undefined) meshes.push(object);
  });
  const observed = new Map();
  // Per-ground-mesh draw counts, keyed by mesh uuid and kept strictly separate from the
  // cover map `observed` so the internal keys can never leak into the cover drawn set.
  const groundDrawCounts = new Map();
  let recording = false;
  for (const mesh of meshes) {
    const previous = mesh.onAfterRender;
    mesh.onAfterRender = function observedAfterRender(renderer, scene, camera, geometry, material, group) {
      if (recording && camera === map.camera) {
        const id = mesh.userData.groundCoverId;
        observed.set(id, (observed.get(id) ?? 0) + 1);
      }
      return previous.call(this, renderer, scene, camera, geometry, material, group);
    };
  }
  const visibleChain = object => {
    for (let node = object; node !== null; node = node.parent) if (!node.visible) return false;
    return true;
  };
  const attached = object => {
    let node = object;
    while (node.parent !== null) node = node.parent;
    return node === map.scene;
  };
  const tint = new Map();
  // Greens are drawn as one merged mesh per material preset (lawn and woodland floor);
  // vertices are written patch by patch in plan order (three reversed vertices per triangle
  // at plan.config.grassY). Rebuild that order from the live plan and compare it with the
  // live buffer, so a patch counts as drawn only if its exact vertices are inside the draw
  // range of a mesh the renderer actually drew with the viewer camera.
  const environmentPlan = map.cityVegetationLayer?.group.userData.environmentPlan;
  if (environmentPlan === undefined) throw new Error("The live vegetation layer exposes no environment plan");
  // Lawn is drawn as one merged mesh per material preset; each mesh lists its green IDs in
  // buffer order (userData.greenIds). Every plan patch must sit in exactly one such mesh.
  const patchById = new Map([...environmentPlan.grass, ...environmentPlan.woodlandFloor]
    .map(patch => [patch.id, patch]));
  // Ground meshes are selected by the planner role recorded at construction
  // (`userData.groundRole` is "lawn" or "woodland-floor"), never by material preset or
  // mesh name: a `natural=wood` green can be assigned the asphalt preset and a
  // `leisure=park` green the woodland-floor preset (T3 precedence), so the preset says
  // nothing about which plan list the mesh's patches came from.
  const grassMeshes = [];
  map.cityVegetationLayer.group.traverse(object => {
    if (object.isMesh && Array.isArray(object.userData.greenIds)
      && (object.userData.groundRole === "lawn" || object.userData.groundRole === "woodland-floor")
      && object.userData.greenIds.some(id => patchById.has(id))) grassMeshes.push(object);
  });
  const grassPatches = [], woodlandFloorPatches = [];
  const grassY = environmentPlan.config.grassY;
  for (const grassMesh of grassMeshes) {
    // Each mesh records its own draw count in `groundDrawCounts` (keyed by uuid), separate
    // from the cover map `observed`: these internal keys must never enter the cover drawn
    // set, the view union or any cover's `drawn_ids`.
    const position = grassMesh.geometry.getAttribute("position");
    const range = grassMesh.geometry.drawRange;
    const drawEnd = Math.min(position.count, range.start + range.count);
    let vertex = 0;
    // The mesh's role comes from the plan list its patches were built from, set at
    // construction alongside `greenIds`.
    const woodland = grassMesh.userData.groundRole === "woodland-floor";
    const patchesForMesh = woodland ? woodlandFloorPatches : grassPatches;
    for (const id of grassMesh.userData.greenIds) {
      const patch = patchById.get(id);
      if (patch === undefined) throw new Error(`Live lawn mesh ${grassMesh.name} lists a green outside the lawn plan: ${id}`);
      const first = vertex;
      let maxError = 0;
      for (const triangle of patch.triangles) for (const point of [...triangle].reverse()) {
        if (vertex >= position.count) { maxError = Infinity; vertex++; continue; }
        maxError = Math.max(maxError, Math.abs(position.getX(vertex) - point[0]),
          Math.abs(position.getY(vertex) - grassY), Math.abs(position.getZ(vertex) - point[1]));
        vertex++;
      }
      patchesForMesh.push({ id: patch.id, mesh: grassMesh.name, meshUuid: grassMesh.uuid,
        preset: grassMesh.userData.terrainPreset ?? null,
        firstVertex: first, vertexCount: vertex - first,
        maxPositionErrorM: maxError, insideDrawRange: first >= range.start && vertex <= drawEnd,
        areaM2: patch.areaM2 });
    }
    if (vertex !== position.count) throw new Error(`Lawn buffer ${grassMesh.name} has ${position.count} vertices, plan implies ${vertex}`);
    const previous = grassMesh.onAfterRender;
    grassMesh.onAfterRender = function observedGround(renderer, scene, camera, geometry, material, groupParam) {
      if (recording && camera === map.camera) groundDrawCounts.set(grassMesh.uuid,
        (groundDrawCounts.get(grassMesh.uuid) ?? 0) + 1);
      return previous.call(this, renderer, scene, camera, geometry, material, groupParam);
    };
  }
  const listedIds = grassPatches.map(patch => patch.id);
  if (new Set(listedIds).size !== listedIds.length || listedIds.length !== environmentPlan.grass.length) {
    throw new Error(`Live lawn meshes list ${listedIds.length} patches (${new Set(listedIds).size} unique), plan has ${environmentPlan.grass.length}`);
  }
  const floorListedIds = woodlandFloorPatches.map(patch => patch.id);
  if (new Set(floorListedIds).size !== floorListedIds.length
    || floorListedIds.length !== environmentPlan.woodlandFloor.length) {
    throw new Error(`Live woodland-floor meshes list ${floorListedIds.length} patches (${new Set(floorListedIds).size} unique), plan has ${environmentPlan.woodlandFloor.length}`);
  }
  window.__aeroGroundCoverAudit = {
    inventory() {
      return meshes.map(mesh => {
        const position = mesh.geometry.getAttribute("position");
        return { id: mesh.userData.groundCoverId, kind: mesh.userData.groundCoverKind,
          attachedToScene: attached(mesh), visibleChain: visibleChain(mesh),
          layerMaskMatchesCamera: mesh.layers.test(map.camera.layers),
          materialVisible: mesh.material.visible, vertexCount: position?.count ?? 0,
          frustumCulled: mesh.frustumCulled, drawnAreaM2: mesh.userData.drawnAreaM2 ?? null };
      });
    },
    grass() {
      return { meshPresent: grassMeshes.length > 0, meshCount: grassMeshes.length,
        attachedToScene: grassMeshes.length > 0 && grassMeshes.every(attached),
        visibleChain: grassMeshes.length > 0 && grassMeshes.every(visibleChain),
        planPatchCount: environmentPlan.grass.length,
        grassFullyExcludedCount: environmentPlan.stats.grassFullyExcludedCount, patches: grassPatches,
        woodlandFloorPlanPatchCount: environmentPlan.woodlandFloor.length,
        woodlandFloorPatches,
        treePlacementsByGreenId: environmentPlan.trees.reduce((counts, tree) => {
          const greenId = tree.provenance?.greenId;
          if (typeof greenId === "string") counts[greenId] = (counts[greenId] ?? 0) + 1;
          return counts;
        }, {}),
        woodGreenIds: [...new Set(environmentPlan.trees.filter(tree => tree.provenance?.source?.tags?.natural === "wood")
          .map(tree => tree.provenance.greenId))].sort() };
    },
    exportedDrawnSet() { return JSON.parse(map.root.dataset.cityGroundCoverDrawnSet ?? "null"); },
    plan() {
      const plan = map.cityVegetationLayer?.group.userData.groundCoverPlan ?? null;
      return plan === null ? null : { geometryId: plan.geometryId, sourceCount: plan.sourceCount,
        fullyClippedIds: plan.fullyClippedIds, stats: plan.stats };
    },
    extent() {
      let minX = Infinity, minZ = Infinity, maxX = -Infinity, maxZ = -Infinity;
      for (const mesh of meshes) {
        mesh.geometry.computeBoundingBox();
        const bounds = mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrixWorld);
        minX = Math.min(minX, bounds.min.x); maxX = Math.max(maxX, bounds.max.x);
        minZ = Math.min(minZ, bounds.min.z); maxZ = Math.max(maxZ, bounds.max.z);
      }
      return { minX, minZ, maxX, maxZ };
    },
    freeze(view) {
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation);
      map.previewAnimation = 0;
      map.previewFollowId = null;
      map.previewSeconds = view.previewSecond;
      map.setCameraMode("free");
      map.controls.enableDamping = false;
      map.camera.position.set(...view.eye);
      map.controls.target.set(...view.target);
      map.controls.update();
      map.renderStreamer?.update(map.camera);
      return { eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        maxDistance: map.controls.maxDistance };
    },
    renderAndObserve() {
      observed.clear();
      groundDrawCounts.clear();
      recording = true;
      try {
        map.focusSunShadow();
        map.renderPreviewFrame();
        map.renderer.getContext().finish();
      } finally {
        recording = false;
      }
      // Frustum expectation, computed with the same camera matrices after the render.
      const matrix = map.camera.projectionMatrix.clone().multiply(map.camera.matrixWorldInverse);
      const planes = frustumPlanes(matrix.elements);
      const expected = [], culled = [];
      for (const mesh of meshes) {
        mesh.geometry.computeBoundingSphere();
        const sphere = mesh.geometry.boundingSphere.clone().applyMatrix4(mesh.matrixWorld);
        const inside = planes.every(plane => plane[0] * sphere.center.x + plane[1] * sphere.center.y
          + plane[2] * sphere.center.z + plane[3] >= -sphere.radius);
        (inside ? expected : culled).push(mesh.userData.groundCoverId);
      }
      // Every ground mesh (lawn/woodland-floor roles included) records its own draw count
      // keyed by `mesh name + uuid`; nothing is aggregated across meshes, and these counts
      // live outside the cover map so cover IDs stay pure.
      const groundMeshDrawCounts = Object.fromEntries(grassMeshes
        .filter(mesh => groundDrawCounts.has(mesh.uuid))
        .map(mesh => [`${mesh.name}#${mesh.uuid}`, groundDrawCounts.get(mesh.uuid)]));
      const drawn = [...observed.keys()].sort();
      const drawnSet = new Set(drawn);
      return {
        renderCalls: map.renderer.info.render.calls,
        groundMeshDrawCounts,
        expectedInFrustum: expected.sort(),
        outsideFrustum: culled.sort(),
        drawn,
        drawCountsById: Object.fromEntries([...observed.entries()].sort()),
        omittedInFrustum: expected.filter(id => !drawnSet.has(id)).sort(),
        drawnOutsideFrustum: drawn.filter(id => !expected.includes(id)),
        streamActive: map.renderStreamer?.progress.active ?? 0,
        loaderHidden: map.root.querySelector(".city-scene-loading")?.hidden === true,
        camera: { eye: map.camera.position.toArray(), target: map.controls.target.toArray() },
      };

      function frustumPlanes(m) {
        // Gribb-Hartmann extraction from the column-major clip matrix.
        const row = index => [m[index], m[index + 4], m[index + 8], m[index + 12]];
        const r0 = row(0), r1 = row(1), r2 = row(2), r3 = row(3);
        const combine = (a, b, sign) => {
          const plane = a.map((value, index) => value + sign * b[index]);
          const length = Math.hypot(plane[0], plane[1], plane[2]);
          return plane.map(value => value / length);
        };
        return [combine(r3, r0, 1), combine(r3, r0, -1), combine(r3, r1, 1),
          combine(r3, r1, -1), combine(r3, r2, 1), combine(r3, r2, -1)];
      }
    },
    setTint(enabled) {
      for (const mesh of meshes) {
        const material = mesh.material;
        if (enabled) {
          if (!tint.has(material)) tint.set(material, material.emissive.getHex());
          material.emissive.setHex(0xff00ff);
        } else if (tint.has(material)) {
          material.emissive.setHex(tint.get(material));
        }
      }
      if (!enabled) tint.clear();
      map.renderPreviewFrame();
      map.renderer.getContext().finish();
    },
  };
  return meshes.length;
}

await mkdir(output, { recursive: true });
const report = {
  schemaVersion: "aero-bench.city-ground-cover-live-audit/v1",
  status: "RUNNING",
  origin, scenePath, camerasFile: camerasPath,
  previewSecond: PREVIEW_SECOND,
  presentation: { timeOfDay: "day", weather: "clear (startup default)" },
  method: "onAfterRender observers on every live ground-cover mesh; only calls with the viewer camera count",
  failures: [], pageErrors: [], consoleErrors: [],
  views: [],
};
const browser = await chromium.launch({ channel: "chromium", headless: true, args: GPU_ARGS });
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("console", message => { if (message.type() === "error") report.consoleErrors.push(message.text()); });
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const token = "this.map = new PublicTraceMap(";
    if (source.split(token).length !== 2) throw new Error("Source app map hook no longer matches exactly once");
    await route.fulfill({ response, body: source.replace(token, "window.__aeroVisualMap = this.map = new PublicTraceMap(") });
  });
  const loadStarted = Date.now();
  await page.goto(`${origin}/?city=${encodeURIComponent(scenePath)}`, { waitUntil: "domcontentloaded", timeout: TIMEOUT_MS });
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return (data?.sceneReady === "true" && data?.skyReady === "true" && data?.texturesReady === "true")
      || data?.sceneError === "true";
  }, undefined, { timeout: TIMEOUT_MS });
  const sceneError = await page.locator("#city-map").getAttribute("data-scene-error-message");
  if (sceneError) throw new Error(`City scene failed: ${sceneError}`);
  await page.locator("#city-map .city-scene-loading").waitFor({ state: "hidden", timeout: TIMEOUT_MS });
  report.sceneLoadSeconds = (Date.now() - loadStarted) / 1000;
  report.renderer = await page.evaluate(() => {
    const gl = window.__aeroVisualMap.renderer.getContext();
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(info?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
  if (/swiftshader|llvmpipe|software/i.test(report.renderer)) throw new Error(`Hardware GPU required, got ${report.renderer}`);

  report.liveMeshCount = await page.evaluate(installObserver);
  report.inventory = await page.evaluate(() => window.__aeroGroundCoverAudit.inventory());
  report.exportedDrawnSet = await page.evaluate(() => window.__aeroGroundCoverAudit.exportedDrawnSet());
  report.plan = await page.evaluate(() => window.__aeroGroundCoverAudit.plan());
  if (report.exportedDrawnSet === null || report.plan === null) {
    throw new Error("The viewer did not export a ground-cover drawn set or plan");
  }
  const extent = await page.evaluate(() => window.__aeroGroundCoverAudit.extent());
  // Top-down camera: the viewer camera has a 46 degree vertical field of view and the 1600x1000
  // viewport gives aspect 1.6, so the height covers the ground-cover extent (x across the
  // screen, z along it) with a 10% margin. The 1 m z offset avoids the orbit pole singularity.
  const centerX = (extent.minX + extent.maxX) / 2, centerZ = (extent.minZ + extent.maxZ) / 2;
  const halfVisible = Math.max((extent.maxZ - extent.minZ) / 2, (extent.maxX - extent.minX) / 2 / 1.6) * 1.1;
  const height = halfVisible / Math.tan((46 / 2) * Math.PI / 180);
  report.fullExtent = { extent, camera: { eye: [centerX, height, centerZ + 1], target: [centerX, 0, centerZ] } };
  const views = [
    ...cameras.map(camera => ({ name: camera.name, eye: camera.eye, target: camera.target, stored: true })),
    { name: "ground-cover-full-extent", ...report.fullExtent.camera, stored: false },
  ];
  const union = new Set();
  for (const view of views) {
    const applied = await page.evaluate(arg => window.__aeroGroundCoverAudit.freeze(arg),
      { ...view, previewSecond: PREVIEW_SECOND });
    await page.waitForFunction(() => (window.__aeroVisualMap.renderStreamer?.progress.active ?? 0) === 0,
      undefined, { timeout: TIMEOUT_MS, polling: 250 });
    const observation = await page.evaluate(() => window.__aeroGroundCoverAudit.renderAndObserve());
    for (const id of observation.drawn) union.add(id);
    const plain = await capture(page, view.name, { classification: "matched-camera-frame" });
    await page.evaluate(() => window.__aeroGroundCoverAudit.setTint(true));
    const tinted = await capture(page, `${view.name}-ground-cover-tinted`,
      { classification: "diagnostic-ground-cover-highlight" });
    await page.evaluate(() => window.__aeroGroundCoverAudit.setTint(false));
    report.views.push({ name: view.name, storedCamera: view.stored, applied, ...observation, plain, tinted });
    if (observation.omittedInFrustum.length > 0) {
      report.failures.push(`${view.name}: ${observation.omittedInFrustum.length} in-frustum ground cover(s) not drawn`);
    }
    if (observation.streamActive !== 0 || !observation.loaderHidden) {
      report.failures.push(`${view.name}: frame was not captured after streaming/loading settled`);
    }
  }

  const exported = report.exportedDrawnSet;
  const drawable = [...exported.drawable_ids].sort();
  const drawn = [...union].sort();
  const omitted = drawable.filter(id => !union.has(id));
  const unexpected = drawn.filter(id => !drawable.includes(id));
  const liveDrawnSet = {
    schema_version: "aero-bench.city-ground-cover-drawn-set/v1",
    status: omitted.length === 0 && unexpected.length === 0 ? "pass" : "fail",
    geometry_id: exported.geometry_id,
    parser_accepted_ids: [...exported.parser_accepted_ids].sort(),
    drawable_ids: drawable,
    fully_clipped_ids: [...exported.fully_clipped_ids].sort(),
    drawn_ids: drawn,
    omitted_ids: omitted,
    unexpected_ids: unexpected,
    omission_count: omitted.length,
  };
  report.liveDrawnSet = { ...liveDrawnSet,
    provenance: "drawn_ids = union of IDs whose live mesh the WebGL renderer drew with the viewer camera across all audited views" };
  await writeFile(resolve(output, "live-drawn-set.json"), `${JSON.stringify(liveDrawnSet, null, 2)}\n`);
  if (liveDrawnSet.status !== "pass") report.failures.push(`live drawn-set omission count ${omitted.length}, unexpected ${unexpected.length}`);
  // Green half of the rendering-omission check.
  const grass = await page.evaluate(() => window.__aeroGroundCoverAudit.grass());
  // A patch is live only if the mesh holding it was drawn with the viewer camera in at least
  // one view (the view's `groundMeshDrawCounts` keys are `mesh name#mesh uuid`; the patch
  // stores its mesh's uuid), the patch sits inside that mesh's draw range, its vertices match
  // the plan within 1e-3 m and it has a nonzero vertex count. Lawn and woodland floor are
  // checked separately against their own plan patch counts.
  const drawnMeshUuids = new Set(report.views.flatMap(view => Object.keys(view.groundMeshDrawCounts))
    .map(key => key.slice(key.lastIndexOf("#") + 1)));
  const patchIsLive = patch => drawnMeshUuids.has(patch.meshUuid) && patch.insideDrawRange
    && patch.maxPositionErrorM <= 1e-3 && patch.vertexCount > 0;
  const livePatchIds = patches => patches.filter(patch => patchIsLive(patch)).map(patch => patch.id).sort();
  const liveGreenIds = livePatchIds(grass.patches);
  const liveWoodlandFloorIds = livePatchIds(grass.woodlandFloorPatches);
  const green = { ...grass, groundMeshesDrawnInViews: report.views
    .filter(view => Object.keys(view.groundMeshDrawCounts).length > 0)
    .map(view => ({ name: view.name, groundMeshDrawCounts: view.groundMeshDrawCounts })),
    liveDrawnGreenIds: liveGreenIds, liveDrawnWoodlandFloorIds: liveWoodlandFloorIds };
  // Woodland-floor plan-count check, the floor analogue of the lawn one: every plan floor
  // patch must be inspected on a floor mesh, no lawn patch may appear as a floor patch.
  const lawnPlanIds = new Set(grass.patches.map(patch => patch.id));
  green.woodlandFloorStatus = grass.woodlandFloorPatches.length === grass.woodlandFloorPlanPatchCount
    && grass.woodlandFloorPatches.every(patch => !lawnPlanIds.has(patch.id))
    ? "measured-pass" : "measured-fail";
  if (green.woodlandFloorStatus !== "measured-pass") {
    report.failures.push(`woodland-floor patch inspection failed: ${JSON.stringify({
      planPatches: grass.woodlandFloorPlanPatchCount, inspected: grass.woodlandFloorPatches.length })}`);
  }
  if (classificationReportPath !== null) {
    const classification = JSON.parse(await readFile(classificationReportPath, "utf8"));
    const accepted = classification.rendering_omission_check?.parser_accepted_green_ids;
    if (!Array.isArray(accepted)) throw new Error("Classification report has no parser_accepted_green_ids");
    const planIds = new Set(grass.patches.map(patch => patch.id));
    green.classificationReport = classificationReportPath;
    green.parserAcceptedGreenIds = [...accepted].sort();
    // Wood greens get a woodland-floor surface instead of lawn (T9a); its live state is
    // checked from the same mesh draw evidence (`liveDrawnWoodlandFloorIds`). A wood
    // without a floor patch (fully clipped) but with live tree placements still draws as
    // trees; a wood with an undrawn floor patch, or with neither surface nor trees, is an
    // omission. Tags come from the scene's published environment source.
    const sources = await page.evaluate(async scene => {
      const manifest = await (await fetch(scene)).json();
      const environment = await (await fetch(manifest.environment_source.url)).json();
      return { url: manifest.environment_source.url,
        greens: environment.greens.map(item => ({ id: item.id, tags: item.provenance.tags })) };
    }, scenePath);
    const tagsById = new Map(sources.greens.map(item => [item.id, item.tags]));
    const notLawn = accepted.filter(id => !planIds.has(id)).sort();
    const isWood = id => tagsById.get(id)?.natural === "wood";
    const trees = id => grass.treePlacementsByGreenId[id] ?? 0;
    // woodland-floor patches carry the same measured-live fields as lawn patches; a wood green
    // with a live floor patch is drawn ground, one without a floor patch (fully clipped) but
    // with live tree placements still draws as trees. A wood green whose floor patch exists
    // but was never drawn in any audited view is an omission: the patch is in the plan, so
    // tree placements alone do not excuse it, and neither case may count as drawn.
    const woodlandFloorPatchById = new Map(grass.woodlandFloorPatches.map(patch => [patch.id, patch]));
    const liveWoodlandFloorIdSet = new Set(liveWoodlandFloorIds);
    green.environmentSourceUrl = sources.url;
    green.woodlandFloorGreenIds = liveWoodlandFloorIds.map(id => ({
      id, liveTreePlacements: trees(id) }));
    green.treeOnlyGreenIds = notLawn.filter(id => isWood(id) && !woodlandFloorPatchById.has(id)
      && trees(id) > 0).map(id => ({ id, liveTreePlacements: trees(id) }));
    green.omittedWoodGreenIds = notLawn.filter(id => isWood(id)
      && (woodlandFloorPatchById.has(id) ? !liveWoodlandFloorIdSet.has(id) : trees(id) === 0))
      .map(id => ({ id, tags: tagsById.get(id), liveLawnPatch: false,
        liveTreePlacements: trees(id), hasFloorPatch: woodlandFloorPatchById.has(id),
        reason: woodlandFloorPatchById.has(id)
          ? "woodland-floor patch exists in the plan but its mesh was never drawn in any audited view"
          : "natural=wood produced no live woodland-floor patch and no green-tree placement survived" }));
    green.fullyClippedGreenIds = notLawn.filter(id => !isWood(id));
    green.omittedGreenIds = [...accepted.filter(id => planIds.has(id) && !liveGreenIds.includes(id)),
      ...green.omittedWoodGreenIds.map(item => item.id)].sort();
    green.unexpectedGreenIds = liveGreenIds.filter(id => !accepted.includes(id));
    green.omissionCount = green.omittedGreenIds.length;
    green.status = green.omissionCount === 0 && green.unexpectedGreenIds.length === 0
      && green.fullyClippedGreenIds.length === grass.grassFullyExcludedCount ? "measured-pass" : "measured-fail";
    if (green.status !== "measured-pass") report.failures.push(`green rendering omission check failed: ${JSON.stringify({
      omitted: green.omittedGreenIds, unexpected: green.unexpectedGreenIds, fullyClipped: green.fullyClippedGreenIds,
      planFullyExcluded: grass.grassFullyExcludedCount })}`);
  } else {
    green.status = "not_compared";
    green.reason = "No --classification-report supplied; parser-accepted green IDs were not compared.";
  }
  report.greenRendering = green;
  const fullView = report.views.find(view => view.name === "ground-cover-full-extent");
  if (fullView.drawn.length !== drawable.length) {
    report.failures.push(`full-extent view drew ${fullView.drawn.length} of ${drawable.length} drawable covers`);
  }
  const badInventory = report.inventory.filter(item => !item.attachedToScene || !item.visibleChain
    || !item.layerMaskMatchesCamera || !item.materialVisible || item.vertexCount === 0);
  if (badInventory.length > 0) report.failures.push(`${badInventory.length} live ground-cover mesh(es) not drawable`);
  if (report.pageErrors.length > 0) report.failures.push("browser page errors occurred");
  await context.close();
} catch (error) {
  report.failures.push(`uncaught: ${error?.message ?? String(error)}`);
  report.failure = String(error?.stack ?? error);
} finally {
  await browser.close();
  report.status = report.failures.length === 0 ? "PASS" : "FAIL";
  report.completedAt = new Date().toISOString();
  await writeFile(resolve(output, "live-audit-report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify({ status: report.status, failures: report.failures,
    liveDrawn: report.liveDrawnSet?.drawn_ids.length, omission: report.liveDrawnSet?.omission_count,
    views: report.views.map(view => [view.name, view.expectedInFrustum.length, view.drawn.length]) }));
  if (report.status !== "PASS") process.exitCode = 1;
}
