import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.AERO_STAGE3_URL;
const fixture = process.env.AERO_STAGE3_SCENARIO_FILE;
const output = process.env.AERO_STAGE3_OUTPUT;
for (const [key, value] of Object.entries({ AERO_STAGE3_URL: baseUrl,
  AERO_STAGE3_SCENARIO_FILE: fixture, AERO_STAGE3_OUTPUT: output })) {
  if (!value) throw new Error(`${key} is required`);
}
mkdirSync(output, { recursive: true });
const scenario = JSON.parse(readFileSync(fixture, "utf8"));
const browser = await chromium.launch({ headless: true,
  args: ["--enable-webgl", "--use-gl=angle", "--use-angle=swiftshader", "--no-proxy-server"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 },
  acceptDownloads: true });
await context.addInitScript(value => {
  if (sessionStorage.getItem("aero-stage3-seeded") === null) {
    localStorage.setItem("aero-bench.city-selected-scene-draft.v1", JSON.stringify(value.selectedScene));
    localStorage.setItem("aero-bench.city-selected-scenario.v2", JSON.stringify(value));
    localStorage.removeItem("aero-bench.city-selected-logistics.v1");
    sessionStorage.setItem("aero-stage3-seeded", "true");
  }
}, scenario);
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));
try {
  await page.goto(new URL("/city-studio.html?tab=algorithm&mode=selected", baseUrl).href,
    { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  await page.locator(".studio-logistics-panel").waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "添加手动订单" }).click();
  await page.getByRole("button", { name: "确认添加订单" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-order-id]").length === 1);
  await page.getByRole("button", { name: "添加手动订单" }).click();
  await page.getByRole("button", { name: "确认添加订单" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-order-id]").length === 2);
  const profileCard = page.locator('[data-fleet-entry-id="uav-1"]');
  await profileCard.getByRole("button", { name: "添加性能档案" }).click();
  const form = profileCard;
  const setField = async (label, value) => {
    const field = form.getByLabel(label, { exact: true });
    await field.fill(String(value));
    await field.press("Tab");
  };
  await setField("来源标签", "运营方测试参数");
  await setField("出处", "用户声明；待实测");
  await setField("机体长度 x / m", 0.6);
  await setField("机体高度 y / m", 0.3);
  await setField("机体宽度 z / m", 0.6);
  await setField("巡航速度 / (m/s)", 8);
  await setField("巡航功率 / W", 180);
  await setField("悬停功率 / W", 260);
  await setField("充电效率 / 0–1", 0.85);
  await form.getByRole("button", { name: "确认添加性能档案" }).click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1") ?? "null")
    ?.performanceProfiles.length === 1);
  const before = await page.evaluate(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1")));
  assert.equal(before.orders.length, 2);
  assert.equal(before.orders[0].sourceFacilityId, "facility-1");
  assert.equal(before.orders[0].destinationFacilityId, "facility-2");
  assert.equal(before.performanceProfiles.length, 1);
  assert.equal(before.executable, false);
  await page.getByLabel("算法参数 JSON").fill('{"cruiseAltitudeM": 140}');
  await page.getByLabel("算法参数 JSON").press("Tab");
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1") ?? "null")
    ?.algorithms.parameters.cruiseAltitudeM === 140);
  await page.getByRole("button", { name: "预览派单规划" }).click();
  await page.getByText(/规划：2 单可分配，0 单不可分配/).waitFor({ timeout: 120000 });
  const dispatchPreview = await page.locator(".studio-logistics-dispatch").innerText();
  assert.match(dispatchPreview, /order-1.*uav-1:1/);
  await page.locator('#studio-tabs a[data-tab="spatial"]').click();
  await page.locator('[data-facility-id="facility-2"]').getByRole("button", { name: "删除设施" }).click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v2") ?? "null")
    ?.facilities.length === 2);
  await page.locator('#studio-tabs a[data-tab="algorithm"]').click();
  await page.getByText(/原物流配置需要修正/).waitFor();
  assert.equal(await page.locator('[data-order-id="order-1"]').count(), 1);
  await page.locator('[data-order-id="order-1"]').getByRole("button", { name: "删除订单" }).click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1") ?? "null")
    ?.orders.length === 1);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  await page.getByText(/物流配置需要修正/).waitFor({ timeout: 30000 });
  assert.equal(await page.locator('[data-order-id="order-2"]').count(), 1);
  await page.locator('[data-order-id="order-2"]').getByRole("button", { name: "删除订单" }).click();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1") ?? "null")
    ?.orders.length === 0);
  const after = await page.evaluate(() => JSON.parse(localStorage.getItem("aero-bench.city-selected-logistics.v1")));
  assert.equal(after.orders.length, 0);
  assert.equal(after.performanceProfiles.length, 1);
  assert.deepEqual(pageErrors, []);
  const receipt = { schema_version: "aero-bench.stage3-authoring-browser-acceptance/v1",
    job_id: scenario.selectedScene.job_id, saved_order: before.orders[0],
    saved_performance_profile: before.performanceProfiles[0],
    scenario_change_not_blocked: true, invalid_order_preserved_until_removed: true,
    invalid_order_repairable_after_reload: true,
    multiple_invalid_orders_repaired_incrementally: true,
    repaired_orders: after.orders.length, real_city_dispatch_preview: dispatchPreview,
    page_errors: pageErrors };
  writeFileSync(resolve(output, "acceptance.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} catch (error) {
  writeFileSync(resolve(output, "failure.json"), JSON.stringify({ error: String(error),
    page_errors: pageErrors, body: await page.locator("body").innerText().catch(() => "unavailable") }, null, 2));
  await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
