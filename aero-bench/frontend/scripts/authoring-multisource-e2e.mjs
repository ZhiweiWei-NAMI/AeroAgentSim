/** Browser acceptance for selecting, building, restoring and switching two registered OSM maps. */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

const baseUrl = new URL(process.env.BASE_URL ?? "https://127.0.0.1:5210/");
const output = resolve(process.env.AUTHORING_E2E_OUTPUT ?? "test-results/authoring-multisource");
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: [
  "--use-gl=angle", "--use-angle=vulkan", "--enable-gpu", "--ignore-gpu-blocklist",
] });
const context = await browser.newContext({ viewport: { width: 1600, height: 950 },
  deviceScaleFactor: 1, ignoreHTTPSErrors: true });
const page = await context.newPage();
const errors = [];
const scenePosts = [];
const mutableAssetRequests = [];
page.on("pageerror", error => errors.push(error.message));
page.on("request", request => {
  if (new URL(request.url()).pathname.startsWith("/models/")) mutableAssetRequests.push(request.url());
  if (request.method() === "POST" && new URL(request.url()).pathname === "/authoring/v1/scenes") {
    scenePosts.push(request.url());
  }
});
const api = path => new URL(path, baseUrl).href;
const job = async id => {
  const response = await context.request.get(api(`/authoring/v1/scenes/${id}`));
  assert.equal(response.status(), 200);
  return response.json();
};
const readyMap = async expectedJob => {
  await page.waitForFunction(jobId => {
    const map = document.querySelector("#studio-map");
    const failed = Boolean(document.querySelector("#studio-authoring-error")?.textContent?.trim());
    return failed || (map?.dataset.sceneReady === "true"
      && map.dataset.sceneSource === "selected-city-static-presentation"
      && (document.querySelector("#studio-authoring-job")?.textContent ?? "").includes(jobId.slice(0, 16)));
  }, expectedJob, { timeout: 900_000 });
  const error = await page.locator("#studio-authoring-error").textContent();
  assert.equal(error?.trim(), "", `authoring UI failed: ${error}`);
  const data = await page.locator("#studio-map").evaluate(element => ({ ...element.dataset }));
  assert.equal(data.sceneReady, "true");
  assert(Number(data.roadLaneCount) > 0 && Number(data.streetLampCount) > 0);
  assert(Number(data.buildingReplacements) > 0 && Number(data.verifiedVisualFiles) > 0);
  return data;
};

try {
  await page.goto(api("/city-studio.html?tab=region"));
  await page.waitForFunction(() => document.querySelectorAll("#studio-authoring-source-select option").length === 2);
  const sourceSelect = page.locator("#studio-authoring-source-select");
  assert.equal(await sourceSelect.inputValue(), "shanghai-central-osm-v1");
  await sourceSelect.selectOption("shanghai-jingan-osm-v1");
  await page.locator("#studio-authoring-source").filter({ hasText: "上海静安 OSM" }).waitFor();
  const osmStatus = await page.locator(".city-region-selector .region-status").textContent();
  assert.match(osmStatus ?? "", /SHA-256 已核验/);

  const canvas = page.locator(".city-region-selector canvas");
  const box = await canvas.boundingBox();
  assert(box !== null, "selected OSM canvas missing");
  await page.mouse.move(box.x + box.width * .20, box.y + box.height * .35);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .65, box.y + box.height * .80, { steps: 12 });
  await page.mouse.up();
  assert.equal(await page.locator("#studio-authoring-build").isEnabled(), true);
  const submitted = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/authoring/v1/scenes");
  await page.locator("#studio-authoring-build").click();
  const initial = await submitted;
  assert.equal(initial.status(), 202);
  const jinganJob = await initial.json();
  const jinganMap = await readyMap(jinganJob.job_id);
  const jinganReady = await job(jinganJob.job_id);
  assert.equal(jinganReady.state, "ready");
  assert.notEqual(jinganReady.source_sha256, "d0f3f30f846e8145aecd497e20ee95bc58e5cb0b3d4b4f46d1339e285217b847");
  await page.screenshot({ path: resolve(output, "jingan-ready.png"), fullPage: true });
  await page.locator("#studio-selected-save").click();
  await page.locator("#studio-selected-draft-status").filter({ hasText: "已保存" }).waitFor();
  const postsBeforeRefresh = scenePosts.length;
  await page.reload();
  await readyMap(jinganJob.job_id);
  assert.equal(await sourceSelect.inputValue(), "shanghai-jingan-osm-v1");
  assert.equal(scenePosts.length, postsBeforeRefresh, "restoring a ready draft rebuilt the city");
  await page.screenshot({ path: resolve(output, "jingan-after-refresh.png"), fullPage: true });

  await sourceSelect.selectOption("shanghai-central-osm-v1");
  await page.locator("#studio-authoring-source").filter({ hasText: "上海中心 OSM" }).waitFor();
  assert.match(await page.locator("#studio-authoring-selection").textContent() ?? "", /尚未框选区域/);
  assert.notEqual(await page.locator("#studio-map").getAttribute("data-scene-ready"), "true",
    "switching the OSM source retained the previous city");

  const catalogResponse = await context.request.get(api("/authoring/v1/sources"));
  const catalog = await catalogResponse.json();
  const central = catalog.sources.find(item => item.source_id === "shanghai-central-osm-v1");
  assert(central);
  const centralJobId = process.env.CENTRAL_JOB_ID;
  assert.match(centralJobId ?? "", /^[0-9a-f]{64}$/, "CENTRAL_JOB_ID is required");
  const centralReady = await job(centralJobId);
  assert.equal(centralReady.state, "ready");
  const centralDraft = {
    purpose: "selected-scene-authoring",
    schema_version: "aero-bench.city-selected-scene-draft/v1",
    selection: {
      schema_version: "aero-bench.scene-selection/v1", source_id: central.source_id,
      source_sha256: central.sha256, origin: central.origin,
      bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
    },
    selection_sha256: centralReady.selection_sha256,
    job_id: centralJobId, source_sha256: central.sha256,
    pack_manifest_sha256: centralReady.pack.manifest.sha256,
    presentation_manifest_sha256: centralReady.presentation.manifest.sha256,
  };
  await page.locator("#studio-selected-import").setInputFiles({
    name: "central-selected-draft.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(centralDraft)),
  });
  const centralMap = await readyMap(centralJobId);
  assert.equal(await sourceSelect.inputValue(), "shanghai-central-osm-v1");
  assert.equal(scenePosts.length, postsBeforeRefresh, "importing a ready draft rebuilt the city");
  assert.notEqual(centralMap.roadLaneCount, jinganMap.roadLaneCount);
  await page.screenshot({ path: resolve(output, "central-after-import.png"), fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(mutableAssetRequests, [], "selected cities fetched unverified /models/ assets");
  const receipt = { schema_version: "aero-bench.multi-source-browser-acceptance/v1",
    jingan: { job_id: jinganJob.job_id, source_sha256: jinganReady.source_sha256,
      buildings: jinganMap.buildingReplacements, lanes: jinganMap.roadLaneCount,
      street_lamps: jinganMap.streetLampCount, render_backend: jinganMap.renderBackend },
    central: { job_id: centralJobId, source_sha256: centralReady.source_sha256,
      buildings: centralMap.buildingReplacements, lanes: centralMap.roadLaneCount,
      street_lamps: centralMap.streetLampCount, render_backend: centralMap.renderBackend },
    scene_post_count: scenePosts.length, refresh_and_import_post_count: scenePosts.length - postsBeforeRefresh,
    page_errors: errors, mutable_asset_requests: mutableAssetRequests };
  writeFileSync(resolve(output, "browser-receipt.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} finally {
  await browser.close();
}
