/** Real-browser acceptance for Stage 2 against one already published selected city.
 * Supply AERO_STAGE2_URL, AERO_STAGE2_JOB_ID, AERO_STAGE2_SELECTION_FILE and
 * AERO_STAGE2_OUTPUT. The coordinates below are measured safe/blocked sites in
 * the central Shanghai acceptance fixture; override them for another city. */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.AERO_STAGE2_URL;
const jobId = process.env.AERO_STAGE2_JOB_ID;
const selectionFile = process.env.AERO_STAGE2_SELECTION_FILE;
const output = process.env.AERO_STAGE2_OUTPUT;
for (const [name, value] of Object.entries({ AERO_STAGE2_URL: baseUrl,
  AERO_STAGE2_JOB_ID: jobId, AERO_STAGE2_SELECTION_FILE: selectionFile, AERO_STAGE2_OUTPUT: output })) {
  if (!value) throw new Error(`${name} is required`);
}
const site = { x: Number(process.env.AERO_STAGE2_SITE_X ?? 480), z: Number(process.env.AERO_STAGE2_SITE_Z ?? -545) };
const blocked = { x: Number(process.env.AERO_STAGE2_BLOCKED_X ?? 344.78),
  z: Number(process.env.AERO_STAGE2_BLOCKED_Z ?? -468.28) };
if (![site.x, site.z, blocked.x, blocked.z].every(Number.isFinite)) throw new Error("Invalid acceptance coordinates");
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true,
  args: ["--enable-webgl", "--use-gl=angle", "--use-angle=swiftshader"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 }, acceptDownloads: true });
const page = await context.newPage();
const pageErrors = [];
const mutableModelRequests = [];
page.on("pageerror", error => pageErrors.push(error.message));
page.on("request", request => {
  if (request.url().includes("/models/")) mutableModelRequests.push(request.url());
});
const setPoint = async (x, z) => {
  await page.getByLabel("选点 X / m").fill(String(x));
  await page.getByLabel("选点 X / m").press("Tab");
  await page.getByLabel("选点 Z / m").fill(String(z));
  await page.getByLabel("选点 Z / m").press("Tab");
};
const changeNumber = async (scope, label, value) => {
  const input = scope.getByLabel(label, { exact: true });
  await input.fill(String(value));
  await input.press("Tab");
};
let receipt;
try {
  const selection = JSON.parse(readFileSync(selectionFile, "utf8"));
  const jobResponse = await context.request.get(new URL(`/authoring/v1/scenes/${jobId}`, baseUrl).href);
  assert.equal(jobResponse.ok(), true, `job response ${jobResponse.status()}`);
  const job = await jobResponse.json();
  assert.equal(job.state, "ready");
  const draft = { purpose: "selected-scene-authoring", schema_version: "aero-bench.city-selected-scene-draft/v1",
    selection, selection_sha256: job.selection_sha256, job_id: job.job_id,
    source_sha256: job.source_sha256, pack_manifest_sha256: job.pack.manifest.sha256,
    presentation_manifest_sha256: job.presentation.manifest.sha256 };
  await context.addInitScript(value => {
    if (sessionStorage.getItem("aero-stage2-e2e-seeded") === null) {
      localStorage.setItem("aero-bench.city-selected-scene-draft.v1", JSON.stringify(value));
      sessionStorage.setItem("aero-stage2-e2e-seeded", "true");
    }
  }, draft);
  await page.goto(new URL("/city-studio.html?tab=region", baseUrl).href, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  // First facility edit must persist both the selected city and its scenario.
  await page.evaluate(() => localStorage.removeItem("aero-bench.city-selected-scene-draft.v1"));
  await page.locator('#studio-tabs a[data-tab="spatial"]').click();
  await page.getByRole("button", { name: "放置起降点" }).click();
  await setPoint(site.x, site.z);
  await page.getByRole("button", { name: "在坐标处放置" }).click();
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.selectedFacilityCount === "1");
  assert.equal(await page.locator("article[data-facility-id]").count(), 1);

  await page.getByRole("button", { name: "放置充电站" }).click();
  await setPoint(blocked.x, blocked.z);
  await page.getByRole("button", { name: "在坐标处放置" }).click();
  await page.waitForFunction(() => Boolean(document.querySelector('.studio-spatial-error[role="alert"]')?.textContent));
  const collision = await page.locator('.studio-spatial-error[role="alert"]').textContent();
  assert.match(collision, /building|建筑/i);
  assert.equal(await page.locator("article[data-facility-id]").count(), 1);
  await page.screenshot({ path: resolve(output, "stage2-spatial.png"), fullPage: true });

  const candidates = [[328, -542], [572, -498], [336, -542], [320, -538],
    [576, -498], [584, -506], [332, -546], [340, -542], [380, -450]];
  const placeAdditional = async (name, countBefore) => {
    const attempts = [];
    for (const [x, z] of candidates) {
      await page.getByRole("button", { name }).click();
      await setPoint(x, z);
      await page.getByRole("button", { name: "在坐标处放置" }).click();
      await page.waitForFunction(previous => Number(document.querySelector("#studio-map")?.dataset.selectedFacilityCount) > previous
        || Boolean(document.querySelector('.studio-spatial-error[role="alert"]')?.textContent), countBefore);
      if (Number(await page.locator("#studio-map").getAttribute("data-selected-facility-count")) > countBefore) {
        return { x, z };
      }
      attempts.push({ x, z, reason: await page.locator('.studio-spatial-error[role="alert"]').textContent() });
    }
    throw new Error(`${name} has no valid acceptance site: ${JSON.stringify(attempts)}`);
  };
  const hubSite = await placeAdditional("放置物流中转站", 1);
  const chargerSite = await placeAdditional("放置充电站", 2);
  assert.equal(await page.locator("article[data-facility-id]").count(), 3);

  await page.getByRole("button", { name: "绘制禁飞区" }).click();
  for (const [x, z] of [[430, -565], [460, -565], [460, -535], [430, -535]]) {
    await setPoint(x, z);
    await page.getByRole("button", { name: "添加顶点" }).click();
  }
  await page.getByRole("button", { name: "完成多边形" }).click();
  await page.waitForFunction(() => document.querySelectorAll("article[data-zone-id]").length === 1);

  await page.locator('#studio-tabs a[data-tab="runtime"]').click();
  await page.locator(".studio-fleet-panel").waitFor({ timeout: 30000 });
  assert.equal(new URL(page.url()).searchParams.get("mode"), "selected");
  // No entry yet, so the model selector exists only in the add form.
  await page.getByRole("button", { name: "添加机队条目" }).click();
  const form = page.locator('[data-fleet-add="form"]');
  const modelOptions = await form.locator('select[aria-label="机型"] option').count();
  assert.equal(modelOptions, 24);
  await form.locator('select[aria-label="机型"]').selectOption("model:holybro-x500");
  await form.locator('select[aria-label="驻地设施"]').selectOption("facility-1");
  await changeNumber(form, "数量", 3);
  await changeNumber(form, "电池电量 / Wh", 600);
  await changeNumber(form, "最大载荷 / kg", 2);
  await page.getByRole("button", { name: "确认添加机队条目" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-fleet-entry-id]").length === 1);
  await changeNumber(page, "背景车辆数", 42);
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2") ?? "null")?.demand.vehicles === 42);
  await changeNumber(page, "背景行人数量", 18);
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2") ?? "null")?.demand.pedestrians === 18);
  await changeNumber(page, "背景自行车数量", 7);
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2") ?? "null")?.demand.bicycles === 7);
  await page.screenshot({ path: resolve(output, "stage2-fleet.png"), fullPage: true });
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2")));
  assert.equal(stored.executable, false);
  assert.equal(stored.facilities.length, 3);
  assert.equal(stored.noFlyZones.length, 1);
  assert.equal(stored.fleet.length, 1);
  assert.deepEqual(stored.demand, { vehicles: 42, pedestrians: 18, bicycles: 7 });
  assert.equal(stored.fleet[0].homeFacilityId, "facility-1");
  assert.equal(stored.fleet[0].batteryWh, 600);
  assert.equal(stored.fleet[0].maxPayloadKg, 2);
  assert.equal(await page.evaluate(() => localStorage.getItem("aero-bench.city-selected-scene-draft.v1") !== null), true);

  await page.locator("#studio-save").click();
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.textContent?.includes("当前选区配置已保存"));
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#studio-export").click();
  const download = await downloadPromise;
  const exportedPath = resolve(output, "selected-city-config.json");
  await download.saveAs(exportedPath);
  const exported = JSON.parse(readFileSync(exportedPath, "utf8"));
  assert.deepEqual(exported, stored);
  await page.locator("#studio-import").setInputFiles(exportedPath);
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.textContent?.includes("已导入并通过当前城市碰撞校验"));
  const wrongCity = structuredClone(exported);
  wrongCity.selectedScene.job_id = "f".repeat(64);
  await page.locator("#studio-import").setInputFiles({ name: "other-city.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(wrongCity)) });
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.textContent?.includes("导入当前选区配置失败"));
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2"))), stored);

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  await page.locator(".studio-fleet-panel").waitFor({ timeout: 30000 });
  assert.equal(await page.locator("[data-fleet-entry-id]").count(), 1);
  assert.equal(await page.getByLabel("背景车辆数").inputValue(), "42");
  await page.locator('#studio-tabs a[data-tab="spatial"]').click();
  assert.equal(await page.locator("article[data-facility-id]").count(), 3);
  assert.equal(await page.locator("article[data-zone-id]").count(), 1);
  assert.equal(await page.locator("#studio-map").getAttribute("data-selected-facility-count"), "3");
  const oldCamera = await page.locator("#studio-map").getAttribute("data-preview-camera-x");
  await page.getByRole("button", { name: "在三维视图定位" }).first().click();
  await page.waitForFunction(before => document.querySelector("#studio-map")?.dataset.previewCameraX !== before,
    oldCamera, { timeout: 10000 });
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(mutableModelRequests, []);
  receipt = { schema_version: "aero-bench.selected-stage2-browser-acceptance/v1", job_id: jobId,
    source_sha256: job.source_sha256, selection_sha256: job.selection_sha256,
    presentation_manifest_sha256: job.presentation.manifest.sha256,
    measured_static_obstacles: Number(await page.locator("#studio-map").getAttribute("data-selected-static-obstacle-count")),
    collision_rejection: collision, facilities: stored.facilities.length,
    facility_sites: { vertiport: site, hub: hubSite, charger: chargerSite },
    no_fly_zones: stored.noFlyZones.length,
    fleet: stored.fleet.length, model_options: modelOptions, background_demand: stored.demand,
    restored_on_reload: true, other_city_import_rejected: true,
    page_errors: pageErrors, mutable_model_requests: mutableModelRequests,
    screenshots: ["stage2-spatial.png", "stage2-fleet.png"], exported_config: "selected-city-config.json" };
  writeFileSync(resolve(output, "acceptance.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt, null, 2));
} finally {
  await browser.close();
}
