/** Real OSM selection → complete selected-city bundle → latest static city browser receipt. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

const baseUrl = new URL(process.env.BASE_URL ?? "http://127.0.0.1:5190/");
const outputDir = resolve(process.env.AUTHORING_E2E_OUTPUT ?? "test-results/authoring-studio");
const timeout = Number(process.env.AUTHORING_E2E_TIMEOUT_MS ?? "900000");
if (!Number.isSafeInteger(timeout) || timeout < 30_000) throw Error("AUTHORING_E2E_TIMEOUT_MS must be at least 30000");
mkdirSync(outputDir, { recursive: true });
const screenshot = resolve(outputDir, "ready.png");
const receiptPath = resolve(outputDir, "receipt.json");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 950 }, deviceScaleFactor: 1, ignoreHTTPSErrors: true,
});
const page = await context.newPage();
const pageErrors = [];
const authoringResponses = [];
const selectedRequests = [];
const responseSizes = [];
const pendingResponseSizes = [];
let trackSelectedRequests = false;
let networkPhase = "catalog";
let presentationRequestedAt = null;
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const urlPath = url => new URL(url).pathname;

page.on("pageerror", error => pageErrors.push(error.message));
page.on("request", request => {
  if (!trackSelectedRequests) return;
  const path = urlPath(request.url());
  if (path.startsWith("/authoring/v1/") || path.startsWith("/models/")) {
    selectedRequests.push({ phase: networkPhase, method: request.method(), path });
    if (presentationRequestedAt === null && path.endsWith("/presentation/manifest.json")) {
      presentationRequestedAt = Date.now();
      networkPhase = "load-ready";
    }
  }
});
page.on("response", response => {
  const path = urlPath(response.url());
  if (path.startsWith("/authoring/v1/")) {
    authoringResponses.push({ method: response.request().method(), status: response.status(), path });
  }
  if (!trackSelectedRequests || (!path.startsWith("/authoring/v1/") && !path.startsWith("/models/"))) return;
  const phase = networkPhase;
  pendingResponseSizes.push(response.body().then(body => {
    responseSizes.push({ phase, path, status: response.status(), body_bytes: body.byteLength });
  }).catch(error => {
    responseSizes.push({ phase, path, status: response.status(), error: String(error) });
  }));
});

async function selectRegionAndSubmit(target) {
  await target.goto(new URL("/city-studio.html?tab=region", baseUrl).href);
  await target.locator("#studio-authoring-source").filter({ hasText: "上海中心 OSM" }).waitFor({ timeout: 30_000 });
  assert.match(await target.locator("#studio-sidebar-subtitle").textContent() ?? "", /本次页面/);
  const canvas = target.locator(".city-region-selector canvas");
  const box = await canvas.boundingBox();
  assert(box !== null, "OSM map canvas is missing");
  await target.mouse.move(box.x + box.width * 0.34, box.y + box.height * 0.32);
  await target.mouse.down();
  await target.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.68, { steps: 16 });
  await target.mouse.up();
  assert.equal(await target.locator("#studio-authoring-build").isEnabled(), true);
  const submitted = target.waitForResponse(response => response.request().method() === "POST"
    && urlPath(response.url()) === "/authoring/v1/scenes", { timeout: 30_000 });
  await target.locator("#studio-authoring-build").click();
  const post = await submitted;
  assert.equal(post.status(), 202, `scene POST returned ${post.status()}`);
  const initialJob = await post.json();
  assert.match(initialJob.job_id, /^[0-9a-f]{64}$/);
  return initialJob;
}

try {
  // Catalog and registered OSM source are read as real bytes. Start mutable-model
  // accounting at build submission, after the ordinary city preview is replaced.
  await page.goto(new URL("/city-studio.html?tab=region", baseUrl).href);
  await page.locator("#studio-authoring-source").filter({ hasText: "上海中心 OSM" }).waitFor({ timeout: 30_000 });
  assert.match(await page.locator("#studio-sidebar-subtitle").textContent() ?? "", /本次页面/);
  const canvas = page.locator(".city-region-selector canvas");
  const box = await canvas.boundingBox();
  assert(box !== null, "OSM map canvas is missing");
  await page.mouse.move(box.x + box.width * 0.34, box.y + box.height * 0.32);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.68, { steps: 16 });
  await page.mouse.up();
  assert.equal(await page.locator("#studio-authoring-build").isEnabled(), true);
  trackSelectedRequests = true;
  networkPhase = "build";
  const submittedAt = Date.now();
  const submitted = page.waitForResponse(response => response.request().method() === "POST"
    && urlPath(response.url()) === "/authoring/v1/scenes", { timeout: 30_000 });
  await page.locator("#studio-authoring-build").click();
  const post = await submitted;
  assert.equal(post.status(), 202, `scene POST returned ${post.status()}`);
  const initialJob = await post.json();
  assert.match(initialJob.job_id, /^[0-9a-f]{64}$/);
  await page.waitForFunction(() => {
    const map = document.querySelector("#studio-map");
    const issue = document.querySelector("#studio-authoring-error")?.textContent?.trim();
    return (map?.dataset.sceneReady === "true" && map.dataset.sceneSource === "selected-city-static-presentation")
      || Boolean(issue) || map?.dataset.sceneError === "true";
  }, null, { timeout });
  const issue = await page.locator("#studio-authoring-error").textContent() ?? "";
  const mapIssue = await page.locator("#studio-map").getAttribute("data-scene-error-message") ?? "";
  assert.equal(issue, "", `authoring build failed: ${issue}`);
  assert.equal(mapIssue, "", `static city render failed: ${mapIssue}`);
  networkPhase = "ready";
  const readyAt = Date.now();

  const finalJob = await page.evaluate(async jobId => {
    const response = await fetch(`/authoring/v1/scenes/${jobId}`);
    if (!response.ok) throw new Error(`job GET returned ${response.status}`);
    return response.json();
  }, initialJob.job_id);
  assert.equal(finalJob.state, "ready");
  assert.equal(finalJob.job_id, initialJob.job_id);
  assert.equal(finalJob.source_sha256, initialJob.source_sha256);
  assert(finalJob.pack?.manifest?.sha256 && finalJob.presentation?.manifest?.sha256,
    "ready must publish both pack and complete presentation");

  const manifestResponse = await context.request.get(
    new URL(`${finalJob.presentation.base_url}manifest.json`, baseUrl).href);
  assert.equal(manifestResponse.status(), 200);
  const manifestBytes = await manifestResponse.body();
  assert.equal(manifestBytes.byteLength, finalJob.presentation.manifest.size_bytes);
  assert.equal(sha256(manifestBytes), finalJob.presentation.manifest.sha256);
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  assert.equal(manifest.schema_version, "aero-bench.city-static-presentation/v1");
  assert.equal(manifest.job_id, finalJob.job_id);
  assert.equal(manifest.selection_sha256, finalJob.selection_sha256);
  assert.equal(manifest.raw_source_sha256, finalJob.source_sha256);
  assert.equal(manifest.effective_osm_sha256, finalJob.pack.source_sha256);
  assert.equal(manifest.pack_manifest.sha256, finalJob.pack.manifest.sha256);
  for (const name of ["road", "building_placement", "signal_inventory", "visual_assets"]) {
    assert.match(manifest[name]?.sha256 ?? "", /^[0-9a-f]{64}$/, `${name} reference missing`);
  }
  const signalResponse = await context.request.get(new URL(
    `${finalJob.presentation.base_url}assets/${manifest.signal_inventory.sha256}`, baseUrl).href);
  assert.equal(signalResponse.status(), 200);
  const signalBytes = await signalResponse.body();
  assert.equal(sha256(signalBytes), manifest.signal_inventory.sha256);
  const signals = JSON.parse(signalBytes.toString("utf8"));
  assert.equal(signals.schema_version, "aero-bench.city-static-signal-inventory/v1");
  assert.equal(signals.source_network_sha256, manifest.network.sha256);
  assert.equal(signals.mesh_pack_source_sha256, manifest.effective_osm_sha256);

  const map = await page.locator("#studio-map").evaluate(element => ({ ...element.dataset }));
  assert.equal(map.sceneSource, "selected-city-static-presentation");
  assert.equal(map.sceneReady, "true");
  assert.equal(map.roadVisual, "selected-sumo-topology-bigcity");
  assert.equal(map.buildingStyle, "metered-facades-and-modern-glass");
  assert(Number(map.buildingReplacements) > 0, "selected city has no BigCity buildings");
  assert(Number(map.buildingVisualParts) > 0, "selected city has no BigCity visual parts");
  assert(Number(map.roadLaneCount) > 0, "selected network has no rendered lanes");
  assert(Number(map.streetLampCount) > 0, "selected city has no rendered street lamps");
  assert(Number(map.verifiedVisualFiles) > 0 && Number(map.verifiedVisualBytes) > 0,
    "selected city did not verify actual visual asset bytes");
  assert.equal(Number(map.trafficSignalCount), signals.signals.length);
  assert.equal(Number(map.renderedTrafficSignalCount), signals.signals.length);
  assert.equal(map.previewSource, "selected-city-static-not-running");
  assert.equal(map.previewVisibleVehicles, "0");
  assert.equal(map.previewVisibleBicycles, "0");
  assert.equal(map.previewVisiblePedestrians, "0");
  assert.equal(map.renderedUavCount, "0");
  assert.equal(map.previewPlaying, "false");
  assert(Number(map.meshCount) > 0 && Number(map.drawCalls) > 0);
  assert.match(await page.locator("#studio-preview-state").textContent() ?? "", /尚未运行/);
  for (const id of ["#studio-name", "#studio-save", "#studio-export", "#studio-import"]) {
    assert.equal(await page.locator(id).isDisabled(), true, `${id} must not edit the old draft`);
  }
  const layout = await page.evaluate(() => {
    const panel = document.querySelector(".studio-panel-scroll");
    const toolbar = document.querySelector(".region-toolbar");
    const build = document.querySelector("#studio-authoring-build");
    if (!panel || !toolbar || !build) throw Error("authoring controls are missing");
    return { scrollHeight: panel.scrollHeight, viewportHeight: panel.clientHeight,
      toolbarTop: toolbar.getBoundingClientRect().top,
      buildBottom: build.getBoundingClientRect().bottom };
  });
  assert(layout.scrollHeight <= layout.viewportHeight + 1, "authoring controls overflow the desktop sidebar");
  assert(layout.toolbarTop >= 0 && layout.buildBottom <= 950, "authoring controls are clipped");

  // Capture the initial local probe and verify that the static camera remains usable.
  // An orbit can leave the 75 m reflective-building radius, where recapture is intentionally skipped.
  const capturesBeforeOrbit = Number(map.staticReflectionCaptures ?? "0");
  assert(capturesBeforeOrbit > 0, "selected city did not capture its first local reflection probe");
  await page.screenshot({ path: resolve(outputDir, "scene-before-orbit.png"), fullPage: true });
  const viewCanvas = page.locator("#studio-map canvas").first();
  const viewBox = await viewCanvas.boundingBox();
  assert(viewBox !== null, "3D city canvas is missing");
  await page.mouse.move(viewBox.x + viewBox.width * 0.54, viewBox.y + viewBox.height * 0.52);
  await page.mouse.down();
  await page.mouse.move(viewBox.x + viewBox.width * 0.79, viewBox.y + viewBox.height * 0.60, { steps: 12 });
  await page.mouse.up();
  const cameraAfter = await page.locator("#studio-map").evaluate(element => ({ ...element.dataset }));
  assert(Math.hypot(Number(cameraAfter.previewCameraX) - Number(map.previewCameraX),
    Number(cameraAfter.previewCameraZ) - Number(map.previewCameraZ)) > 8,
  "selected city camera did not move after orbit");
  const capturesAfterOrbit = Number(await page.locator("#studio-map").getAttribute("data-static-reflection-captures"));
  assert(capturesAfterOrbit >= capturesBeforeOrbit);

  await Promise.all(pendingResponseSizes);
  const mutableModelRequests = selectedRequests.filter(item => item.path.startsWith("/models/"));
  assert.deepEqual(mutableModelRequests, [], "selected city fetched mutable /models/ assets");
  assert.deepEqual(pageErrors, []);
  await page.screenshot({ path: screenshot, fullPage: true });

  let rejection = null;
  if (process.env.AUTHORING_E2E_VERIFY_REJECTION !== "0") {
    const rejectedPage = await context.newPage();
    const realManifestPath = `${finalJob.presentation.base_url}manifest.json`;
    const changed = { ...manifest, source_id: `${manifest.source_id}-tampered` };
    let intercepted = 0;
    await rejectedPage.route(`**${realManifestPath}`, async route => {
      intercepted++;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(changed) });
    });
    try {
      const repeatedJob = await selectRegionAndSubmit(rejectedPage);
      assert.equal(repeatedJob.job_id, finalJob.job_id, "same real selection did not reuse its deterministic job");
      await rejectedPage.locator("#studio-authoring-error").filter({ hasText: /SHA-256|哈希|Content-Length/ }).waitFor({ timeout: 60_000 });
      assert(intercepted > 0, "tampered real manifest was not requested");
      assert.notEqual(await rejectedPage.locator("#studio-map").getAttribute("data-scene-source"),
        "selected-city-static-presentation", "tampered manifest rendered a fallback scene");
      rejection = { mode: "modified captured real manifest", intercepted_requests: intercepted,
        displayed_error: await rejectedPage.locator("#studio-authoring-error").textContent() };
      await rejectedPage.screenshot({ path: resolve(outputDir, "rejected-manifest.png"), fullPage: true });
    } finally { await rejectedPage.close(); }
  }

  const resourceTiming = await page.evaluate(() => performance.getEntriesByType("resource")
    .filter(entry => new URL(entry.name).pathname.startsWith("/authoring/v1/"))
    .map(entry => ({ path: new URL(entry.name).pathname,
      transfer_size: entry.transferSize, encoded_body_size: entry.encodedBodySize,
      decoded_body_size: entry.decodedBodySize, duration_ms: entry.duration })));
  const receipt = {
    schema_version: "aero-bench.authoring-browser-receipt/v2",
    base_url: baseUrl.href,
    job_id: finalJob.job_id,
    selection_sha256: finalJob.selection_sha256,
    source_sha256: finalJob.source_sha256,
    pack_manifest_sha256: finalJob.pack.manifest.sha256,
    presentation_manifest_sha256: finalJob.presentation.manifest.sha256,
    effective_osm_sha256: manifest.effective_osm_sha256,
    network_sha256: manifest.network.sha256,
    road_sha256: manifest.road.sha256,
    building_placement_sha256: manifest.building_placement.sha256,
    signal_inventory_sha256: manifest.signal_inventory.sha256,
    signal_count: signals.signals.length,
    visual_inventory_sha256: manifest.visual_assets.sha256,
    selection_display: await page.locator("#studio-authoring-selection").textContent(),
    timings_ms: { submission_to_scene_ready: readyAt - submittedAt,
      presentation_request_to_scene_ready: presentationRequestedAt === null ? null : readyAt - presentationRequestedAt },
    reflection_captures: { before_orbit: capturesBeforeOrbit, after_orbit: capturesAfterOrbit },
    map, layout, selected_requests: selectedRequests, mutable_model_requests: mutableModelRequests,
    authoring_responses: authoringResponses, response_body_sizes: responseSizes,
    browser_resource_timing: resourceTiming,
    response_body_bytes: responseSizes.reduce((sum, item) => sum + (item.body_bytes ?? 0), 0),
    browser_transfer_bytes: resourceTiming.reduce((sum, item) => sum + item.transfer_size, 0),
    page_errors: pageErrors, rejection, screenshot,
  };
  writeFileSync(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
  process.stdout.write(`${receiptPath}\n${screenshot}\n`);
} catch (error) {
  await page.screenshot({ path: resolve(outputDir, "failed.png"), fullPage: true }).catch(() => undefined);
  const finalMap = await page.locator("#studio-map").evaluate(element => ({ ...element.dataset })).catch(() => null);
  writeFileSync(resolve(outputDir, "failure.json"), `${JSON.stringify({ error: String(error),
    page_errors: pageErrors, selected_requests: selectedRequests,
    authoring_responses: authoringResponses, response_body_sizes: responseSizes,
    final_map: finalMap }, null, 2)}\n`);
  throw error;
} finally {
  await browser.close();
}
