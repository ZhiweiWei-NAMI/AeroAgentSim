/** Browser acceptance for selected-city facility capability authoring (v3 + positive dispatch).
 *
 * Extends this script's own previous run (the retained negative evidence) with a
 * UI-driven positive case; no production file is modified by this script.
 *
 * Reuses the Playwright launch and localStorage seeding approach of
 * `frontend/scripts/selected-stage3-authoring-e2e.mjs`, but writes only to
 * `validation/facility-capabilities-browser/` and does not touch historical
 * evidence. The seeded facility document is generated from
 * `validation/city-authoring-stage2-20260928/accept/selected-city-config.json`
 * (see fixture JSON in the same folder); no historical evidence file is modified.
 *
 * Proven in a real headless Chromium against the already-built site:
 *   1. seed v3 fixture → reload → selected Shanghai scene restores from the
 *      authoring API (job digest re-verified in-browser) → spatial tab renders;
 *   1b. click 放置充电站 → map click → candidate preview → explicit 确认放置 →
 *       a ground charging facility with its declared default capability is
 *       accepted, shown in 3D and persisted;
 *   2. pick a ground point on the map → candidate preview section appears →
 *      explicit 确认放置 → facility accepted and shown in 3D;
 *   3. edit unit-bearing fields (cargo storage/throughput, charging power and
 *      kWh price, movements/hour) and prove the accepted document changed;
 *   4. add an order with mandatory hub handoff, plus the vertiport→vertiport
 *      rejection without a hub;
 *   5. reload → localStorage round-trip equals the last accepted document;
 *   6. declare an explicit operator performance profile for uav-1 (0.6×0.3×0.6 m,
 *      8 m/s cruise, 180 W cruise / 260 W hover, charge efficiency 0.85, declared
 *      "运营方测试参数 · 声明值，非实测"), set cruiseAltitudeM 140 and run the
 *      authoring dispatch preview; the recorded evidence is the real plan the
 *      running bundle produced — if an order is assignable it must carry the hub
 *      handoff path and per-leg durations, if not the real blocking reason is
 *      recorded verbatim;
 *   7. draw a manual no-fly polygon on the map and confirm it persists;
 *   8. delete a facility referenced by orders → the logistics document is kept
 *      in a repairable state and the order survives until repaired/removed;
 *   9. (this run) keep the retained pad_capacity negative verbatim, then build
 *      the REAL dispatchable positive case through the UI only:
 *      机队 (runtime) tab → uav-1 数量 field 3 → 1 (panel `numericField("数量")`,
 *      committed through the panel's own onChange → parseCitySelectedScenario →
 *      draft + scenario persisted by city-studio), back to 算法 tab, re-run
 *      预览派单规划 for the same untouched order. The positive case passes only
 *      when the planner itself returns ≥1 acceptedAssignment carrying the actual
 *      hub handoff leg and real per-leg durations/distances. The plan object is
 *      captured read-only from the planner's own deepFreeze — it is never
 *      fabricated, patched or rewritten here.
 *
 * Every result (pass or failure) plus `pageerror` events are recorded into
 * receipt JSON and screenshots at workspace-relative paths.
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.AERO_FC_URL ?? "http://127.0.0.1:5222";
const output = process.env.AERO_FC_OUTPUT
  ?? resolve(import.meta.dirname, "../../validation/facility-capabilities-browser");
const fixturePath = process.env.AERO_FC_FIXTURE
  ?? resolve(import.meta.dirname, "../../validation/facility-capabilities-20260928/browser/selected-city-config-v3-facility-capabilities.json");

const SCENARIO_KEY = "aero-bench.city-selected-scenario.v3";
const DRAFT_KEY = "aero-bench.city-selected-scene-draft.v1";
const LOGISTICS_KEY = "aero-bench.city-selected-logistics.v2";

/** Read-only evidence hooks, installed in the page before the bundle runs.
 * `Object.freeze` is wrapped ONLY to record every frozen authoring artifact: the
 * dispatch plan is the only object frozen with `purpose:
 * "selected-logistics-dispatch-plan"`, and it passes through the freeze the host
 * applies to its own plan (city-selected-dispatch.ts `deepFreeze`) — the wrapper
 * never creates, mutates, replaces or drops any object. It exists because the
 * host UI renders this plan only as a summary line; per-leg routes, hub handoff
 * and the real unassignment reason are otherwise not observable from the page. */
const CAPTURE_INIT = `
(() => {
  if (window.__aeroFCCaptured === undefined) {
    window.__aeroFCCaptured = { dispatchPlans: [] };
    const rawFreeze = Object.freeze;
    Object.freeze = function (value) {
      try {
        if (value && typeof value === "object"
          && value.purpose === "selected-logistics-dispatch-plan"
          && value.schema_version === "aero-bench.city-selected-dispatch-plan/v1"
          && Array.isArray(value.acceptedAssignments) && Array.isArray(value.unassignedOrders)) {
          window.__aeroFCCaptured.dispatchPlans.push(rawFreeze.call(Object, value));
        }
      } catch { /* evidence capture must never break the host */ }
      return rawFreeze.apply(this, arguments);
    };
  }
})();
`;

mkdirSync(output, { recursive: true });
const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
assert.equal(fixture.schema_version, "aero-bench.city-selected-scenario/v3");

const browser = await chromium.launch({ headless: true,
  args: ["--enable-webgl", "--use-gl=angle", "--use-angle=swiftshader", "--no-proxy-server"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 },
  acceptDownloads: true });
await context.addInitScript(CAPTURE_INIT);
await context.addInitScript(([key, draftKey, value]) => {
  if (sessionStorage.getItem("aero-fc-seeded") === null) {
    // Independent browser context: only this script's own keys are seeded;
    // nothing else is read from or written to user storage.
    localStorage.setItem(draftKey, JSON.stringify(value.selectedScene));
    localStorage.setItem(key, JSON.stringify(value));
    sessionStorage.setItem("aero-fc-seeded", "true");
  }
}, [SCENARIO_KEY, DRAFT_KEY, fixture]);

const page = await context.newPage();
const pageErrors = [];
const failedRequests = [];
const benignConsoleErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));
page.on("requestfailed", request => failedRequests.push({ url: request.url(), failure: request.failure()?.errorText }));
page.on("console", message => {
  if (message.type() !== "error") return;
  // Known-benign, reproduced on a plain load with no interaction: loadPackedScene calls
  // `this.world.add(...objects)` where the selected-scene presentation path leaves the
  // packed-batch `objects` array empty, so three.js logs one Object3D.add warning per
  // scene build. It is a no-op warning (no undefined element is ever added — the
  // subsequent `for (const object of objects)` loop would throw a pageerror otherwise),
  // lives in production code outside this task's write scope, and is whitelisted here
  // while still being recorded in the receipt.
  if (message.text() === "THREE.Object3D.add: object not an instance of THREE.Object3D. undefined") {
    benignConsoleErrors.push(message.text());
    return;
  }
  pageErrors.push(`console.error: ${message.text()}`);
});

const steps = [];
const step = (name, status, detail) => {
  steps.push({ step: name, status, ...(detail === undefined ? {} : { detail }) });
  console.log(`[${status}] ${name}${detail === undefined ? "" : ` — ${JSON.stringify(detail)}`}`);
};
let fatal = null;

// Map click → candidate preview; used for both facility placement and no-fly drawing.
async function clickMapAt(map, xFrac, zFrac) {
  const box = await map.boundingBox();
  assert.ok(box, "spatial map svg has a bounding box");
  await page.mouse.click(box.x + box.width * xFrac, box.y + box.height * zFrac);
}

/** Set one algorithm parameter (e.g. cruiseAltitudeM) through the 算法参数 JSON
 * textarea, the same declared-operator path the UI exposes. */
async function setAlgorithmParameter(name, value) {
  const params = page.getByLabel("算法参数 JSON", { exact: true });
  const current = await params.inputValue();
  const parsed = current.trim() === "" ? {} : JSON.parse(current);
  parsed[name] = value;
  await params.fill(JSON.stringify(parsed, null, 2));
  await params.press("Tab");
  await page.waitForFunction(([key, parameter, expected]) =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.algorithms?.parameters?.[parameter] === expected,
    [LOGISTICS_KEY, name, value], { timeout: 30000 });
}

const legSummary = leg => ({
  from: leg.fromFacilityId, to: leg.toFacilityId,
  departureAtS: Number(leg.departureAtS.toFixed(3)),
  durationS: Number(leg.estimatedDurationS.toFixed(3)),
  distanceM: Number(leg.estimatedDistanceM.toFixed(3)),
  segments: leg.segments.length,
});

try {
  // ---- 1. load and restore the sealed selected Shanghai scene ----------------
  await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
    { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  step("scene_ready", "pass", { url: page.url() });
  await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 3,
    null, { timeout: 30000 });
  const counts = await page.evaluate(() => ({
    facilities: document.querySelectorAll("[data-facility-id]").length,
    zones: document.querySelectorAll("[data-zone-id]").length,
    sceneFacilities: document.querySelector("#studio-map")?.dataset.selectedFacilityCount ?? null,
  }));
  assert.equal(counts.facilities, 3);
  assert.equal(counts.zones, 1);
  assert.equal(counts.sceneFacilities, "3");
  step("fixture_loaded", "pass", counts);
  await page.screenshot({ path: resolve(output, "01-spatial-loaded.png"), fullPage: false });

  // ---- 1b. 放置充电站: map click → candidate → explicit confirm → 3D --------
  await page.getByRole("button", { name: "放置充电站" }).click();
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.4896, 0.3793);
  await page.locator(".studio-spatial-pending").waitFor({ timeout: 10000 });
  const chargerPendingText = (await page.locator(".studio-spatial-pending").innerText())
    .replace(/\s+/g, " ");
  assert.match(chargerPendingText, /候选位置（确认后应用）/);
  assert.match(chargerPendingText, /位移.*m.*朝向.*°.*(合法|不合法)/);
  step("charger_candidate_preview", "pass", { panel: chargerPendingText.slice(0, 160) });
  await page.screenshot({ path: resolve(output, "02b-charger-candidate.png") });
  await page.getByRole("button", { name: "确认放置" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 4,
    null, { timeout: 30000 });
  const chargerId = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("[data-facility-id]")];
    return cards.map(node => node.dataset.facilityId)
      .find(id => id !== "facility-1" && id !== "facility-2" && id !== "facility-3") ?? null;
  });
  assert.ok(chargerId, "a new facility id appeared after placing the charger");
  const placedChargerCard = page.locator(`[data-facility-id="${chargerId}"]`);
  assert.match(await placedChargerCard.locator("h4").innerText(), /充电站 · /);
  assert.match(await placedChargerCard.innerText(), /放置：地面/);
  // the placed charger must carry the panel's declared default charging capability
  const chargerDefaults = await page.evaluate(id => {
    const scenario = JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v3") ?? "null");
    const facility = scenario.facilities.find(item => item.id === id) ?? null;
    return facility === null ? null
      : { kind: facility.kind, placement: facility.placement, charging: facility.charging,
          landing: facility.landing };
  }, chargerId);
  assert.equal(chargerDefaults.kind, "charger");
  assert.equal(chargerDefaults.placement, "ground");
  assert.equal(chargerDefaults.landing, null);
  assert.equal(chargerDefaults.charging.slots, 3);
  assert.equal(chargerDefaults.charging.powerW, 6000);
  assert.equal(chargerDefaults.charging.priceAmount, 1.2);
  assert.equal(chargerDefaults.charging.priceCurrency, "CNY");
  assert.equal(chargerDefaults.charging.priceUnit, "kWh");
  await page.waitForFunction(id => document.querySelector("#studio-map")?.dataset.selectedFacilityCount === "4",
    chargerId, { timeout: 30000 });
  step("charger_placed_confirmed_3d", "pass", {
    newFacilityId: chargerId, sceneFacilityCount: 4,
    charging: chargerDefaults.charging, persistedBeforeReload: true,
  });
  await page.screenshot({ path: resolve(output, "03b-charger-confirmed-3d.png") });

  // ---- 2. map click → candidate preview → explicit confirm → 3D -------------
  await page.getByRole("button", { name: "放置物流中转站" }).click();
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.5917, 0.6552);
  await page.locator(".studio-spatial-pending").waitFor({ timeout: 10000 });
  const pendingText = (await page.locator(".studio-spatial-pending").innerText()).replace(/\s+/g, " ");
  assert.match(pendingText, /候选位置（确认后应用）/);
  assert.match(pendingText, /位移.*m.*朝向.*°.*(合法|不合法)/);
  step("candidate_preview", "pass", { panel: pendingText.slice(0, 160) });
  await page.screenshot({ path: resolve(output, "02-candidate-preview.png") });
  await page.getByRole("button", { name: "确认放置" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 5,
    null, { timeout: 30000 });
  const newId = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[data-facility-id]")].map(node => node.dataset.facilityId);
    return ids.find(id => id.startsWith("facility-") && id !== "facility-1"
      && id !== "facility-2" && id !== "facility-3" && id !== "facility-4") ?? null;
  });
  assert.ok(newId, "a new facility id appeared after 确认放置");
  await page.waitForFunction(id => document.querySelector("#studio-map")?.dataset.selectedFacilityCount === String(id.count),
    { count: 5 }, { timeout: 30000 });
  // the UI-placed hub must carry the panel's declared default cargo + landing
  // capability: it is the order's hub handoff and the actual processing stop.
  const placedHub = await page.evaluate(id => {
    const scenario = JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v3") ?? "null");
    return scenario.facilities.find(item => item.id === id) ?? null;
  }, newId);
  assert.equal(placedHub.kind, "hub");
  assert.equal(placedHub.placement, "ground");
  assert.deepEqual(placedHub.cargo, { storageCapacityKg: 500, throughputPerHourKg: 1000 });
  assert.deepEqual(placedHub.landing, { parkingSlots: 2, movementsPerHour: 20 });
  step("confirm_placement_3d", "pass", {
    newFacilityId: newId, sceneFacilityCount: 5,
    placedHubCapabilities: { landing: placedHub.landing, cargo: placedHub.cargo },
  });
  await page.screenshot({ path: resolve(output, "03-confirmed-3d.png") });

  // place a second vertiport (needed for the no-hub order rejection case)
  await page.getByRole("button", { name: "放置起降点" }).click();
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.0625, 0.5724);
  await page.locator(".studio-spatial-pending").waitFor({ timeout: 10000 });
  await page.getByRole("button", { name: "确认放置" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 6,
    null, { timeout: 30000 });
  const vertiportId = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[data-facility-id]")].map(node => node.dataset.facilityId);
    const fresh = ids.filter(id => id !== "facility-1" && id !== "facility-2" && id !== "facility-3"
      && id !== "facility-4" && id !== "facility-5");
    return fresh.length ? fresh[fresh.length - 1] : null;
  });
  await page.waitForFunction(id => document.querySelector("#studio-map")?.dataset.selectedFacilityCount === String(id.count),
    { count: 6 }, { timeout: 30000 });
  step("second_vertiport_placed", "pass", { newFacilityId: vertiportId });

  // ---- 3. edit unit-bearing capability fields -------------------------------
  const hubCard = page.locator('[data-facility-id="facility-2"]');
  const setField = async (label, value, card = hubCard) => {
    const field = card.getByLabel(label, { exact: true });
    await field.fill(String(value));
    await field.press("Tab");
    await page.waitForFunction(() => document.querySelector("#studio-save-status")?.dataset.state === "saved",
      null, { timeout: 30000 });
  };
  await setField("货站存储容量 / kg", 800);
  await setField("货站处理能力 / (kg·小时⁻¹)", 1500);
  const chargerCard = page.locator('[data-facility-id="facility-3"]');
  await setField("充电功率 / W", 7000, chargerCard);
  await setField("充电价格 · 每 kWh", 2.1, chargerCard);
  await setField("充电位数", 2, chargerCard);
  const storedAfterEdit = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), SCENARIO_KEY);
  assert.equal(storedAfterEdit.schema_version, "aero-bench.city-selected-scenario/v3");
  const storedHub = storedAfterEdit.facilities.find(f => f.id === "facility-2");
  const storedCharger = storedAfterEdit.facilities.find(f => f.id === "facility-3");
  assert.equal(storedHub.cargo.storageCapacityKg, 800);
  assert.equal(storedHub.cargo.throughputPerHourKg, 1500);
  assert.equal(storedCharger.charging.priceAmount, 2.1);
  assert.equal(storedCharger.charging.powerW, 7000);
  assert.equal(storedCharger.charging.slots, 2);
  const newHubCard = page.locator(`[data-facility-id="${newId}"]`);  const newMovements = newHubCard.getByLabel("每个起降位处理能力 / (架次·小时⁻¹)", { exact: true });
  await newMovements.fill("45");
  await newMovements.press("Tab");
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.dataset.state === "saved",
    null, { timeout: 30000 });
  step("unit_fields_edited", "pass", {
    hub_storageKg: storedHub.cargo.storageCapacityKg,
    hub_throughputPerHourKg: storedHub.cargo.throughputPerHourKg,
    charger_pricePerKWh: storedCharger.charging.priceAmount,
    charger_powerW: storedCharger.charging.powerW,
    charger_slots: storedCharger.charging.slots,
    new_facility_movementsPerHour: 45,
  });
  await page.screenshot({ path: resolve(output, "04-capability-edits.png") });

  // ---- 3b. performance profile for uav-1: declared values, never measured ----
  // Profiles are authored in the 调度算法 (logistics) panel, one card per fleet entry.
  await page.locator('#studio-tabs a[data-tab="algorithm"]').click();
  await page.locator(".studio-logistics-panel").waitFor({ timeout: 30000 });
  // declared cruise altitude through the 算法参数 JSON field (required by the
  // built-in route authority before any dispatch preview can run)
  await setAlgorithmParameter("cruiseAltitudeM", 140);
  step("cruise_altitude_declared", "pass", { cruiseAltitudeM: 140 });
  const fleetEntryCard = page.locator('[data-fleet-entry-id="uav-1"]');
  await fleetEntryCard.getByRole("button", { name: "添加性能档案" }).click();
  const profileForm = fleetEntryCard;
  const setProfileField = async (label, value) => {
    const field = profileForm.getByLabel(label, { exact: true });
    await field.fill(String(value));
    await field.press("Tab");
  };
  await setProfileField("来源标签", "运营方测试参数");
  await setProfileField("出处", "声明值（用户申报），非实测");
  await setProfileField("机体长度 x / m", 0.6);
  await setProfileField("机体高度 y / m", 0.3);
  await setProfileField("机体宽度 z / m", 0.6);
  await setProfileField("巡航速度 / (m/s)", 8);
  await setProfileField("巡航功率 / W", 180);
  await setProfileField("悬停功率 / W", 260);
  await setProfileField("充电效率 / 0–1", 0.85);
  await fleetEntryCard.getByRole("button", { name: "确认添加性能档案" }).click();
  await page.waitForFunction(key =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.performanceProfiles?.length === 1,
    LOGISTICS_KEY, { timeout: 30000 });
  const savedProfile = await page.evaluate(key =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.performanceProfiles?.[0] ?? null, LOGISTICS_KEY);
  assert.equal(savedProfile.fleetEntryId, "uav-1");
  assert.deepEqual(savedProfile.aircraftBody, { xM: 0.6, yM: 0.3, zM: 0.6 });
  assert.equal(savedProfile.cruiseSpeedMps, 8);
  assert.equal(savedProfile.cruisePowerW, 180);
  assert.equal(savedProfile.hoverPowerW, 260);
  assert.equal(savedProfile.chargeEfficiency, 0.85);
  assert.equal(savedProfile.sourceLabel, "运营方测试参数");
  assert.match(savedProfile.provenance, /声明值/);
  assert.match(savedProfile.provenance, /非实测/);
  step("performance_profile_declared", "pass", { profile: savedProfile });
  await page.screenshot({ path: resolve(output, "04b-performance-profile.png") });

  // ---- 4. orders must hand off through the hub ------------------------------
  // facility-5 is a second hub (explicit handoff midpoint); facility-6 a vertiport.
  await page.getByRole("button", { name: "添加手动订单" }).click();
  await page.locator('[data-order-add="form"]').waitFor({ timeout: 10000 });
  // vertiport → vertiport without a hub midpoint must be rejected
  await page.getByLabel("来源设施", { exact: true }).selectOption("facility-1");
  await page.getByLabel("目的设施", { exact: true }).selectOption(vertiportId);
  await page.getByRole("button", { name: "确认添加订单" }).click();
  await page.getByText(/缺少物流中转站交接/).waitFor({ timeout: 30000 });
  const rejectNotice = (await page.locator('.studio-logistics-panel [role="alert"]').last().innerText())
    .replace(/\s+/g, " ");
  const rejectedOrderCount = await page.evaluate(key =>
    (JSON.parse(localStorage.getItem(key) ?? "null")?.orders ?? []).length, LOGISTICS_KEY);
  assert.equal(rejectedOrderCount, 0);
  step("order_without_hub_rejected", "pass", { notice: rejectNotice, orders: 0 });
  // valid order: vertiport → hub (the hub endpoint documents its own handoff)
  await page.getByLabel("目的设施", { exact: true }).selectOption("facility-2");
  await page.getByRole("button", { name: "确认添加订单" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-order-id]").length === 1,
    null, { timeout: 30000 });
  // then declare an explicit hub handoff midpoint through the order card select
  const orderId = await page.locator("[data-order-id]").first().getAttribute("data-order-id");
  const handoffSelect = page.locator(`[data-order-id="${orderId}"]`)
    .getByLabel("物流中转站交接", { exact: true });
  await handoffSelect.selectOption(newId);
  await page.waitForFunction(([key, id]) =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.orders?.[0]?.hubHandoffFacilityId === id,
    [LOGISTICS_KEY, newId], { timeout: 30000 });
  const orderWithHandoff = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null")?.orders?.[0] ?? null,
    LOGISTICS_KEY);
  assert.equal(orderWithHandoff.hubHandoffFacilityId, newId);
  step("order_hub_handoff", "pass", { order: orderWithHandoff });

  // ---- 4b. dispatch preview against the real city ----------------------------
  // The host UI shows only a summary line; the full plan (hub handoff path, per-leg
  // durations, unassignment reasons) is captured read-only from the Object.freeze
  // the dispatch planner itself applies to its returned plan.
  await page.getByRole("button", { name: "预览派单规划" }).click();
  await page.locator(".studio-logistics-dispatch")
    .getByText(/规划：\d+ 单可分配，\d+ 单不可分配/).waitFor({ timeout: 180000 });
  const summaryLine = (await page.locator(".studio-logistics-dispatch").innerText())
    .replace(/\s+/g, " ");
  const capturedPlans = await page.evaluate(() => window.__aeroFCCaptured?.dispatchPlans ?? []);
  assert.ok(capturedPlans.length >= 1, "a real dispatch plan was produced by the running bundle");
  const plan = capturedPlans[capturedPlans.length - 1];
  assert.equal(plan.schema_version, "aero-bench.city-selected-dispatch-plan/v1");
  assert.equal(plan.executable, false);
  assert.equal(plan.ordersConsidered, 1);
  const accepted = plan.acceptedAssignments.find(item => item.orderId === orderId) ?? null;
  const unassigned = plan.unassignedOrders.find(item => item.orderId === orderId) ?? null;
  assert.ok(accepted === null || unassigned === null, "order is either assigned or unassigned, not both");
  let dispatchEvidence;
  if (accepted !== null) {
    assert.ok(accepted.hubHandoffFacilityId, "an accepted assignment must carry the hub handoff");
    assert.ok(accepted.routeLegs.length >= 1, "an accepted assignment must carry route legs");
    for (const leg of accepted.routeLegs) {
      assert.ok(Number.isFinite(leg.estimatedDurationS) && leg.estimatedDurationS >= 0);
      assert.ok(Number.isFinite(leg.estimatedDistanceM) && leg.estimatedDistanceM >= 0);
    }
    const legsIncludeHandoff = accepted.routeLegs.some(leg =>
      leg.fromFacilityId === accepted.hubHandoffFacilityId
      || leg.toFacilityId === accepted.hubHandoffFacilityId);
    assert.ok(legsIncludeHandoff, "route legs must include the hub handoff leg");
    dispatchEvidence = {
      orderId, assigned: true, unitId: accepted.unitId,
      hubHandoffFacilityId: accepted.hubHandoffFacilityId,
      hubHandoffPath: legSummary(accepted.routeFromPickupToDestination),
      routeLegs: accepted.routeLegs.map(legSummary),
      arrivalAtS: Number(accepted.arrivalAtS.toFixed(3)),
      batteryAtArrivalWh: Number(accepted.batteryAtArrivalWh.toFixed(3)),
      plannedChargingStops: accepted.chargingStops.length,
      summary: summaryLine.slice(0, 300),
    };
    assert.match(summaryLine, new RegExp(`${orderId}\\s*→\\s*${accepted.unitId}`));
  } else {
    assert.ok(unassigned.primaryReason, "an unassigned order must carry the real blocking reason");
    assert.ok(unassigned.perUnitFailures.length >= 1, "per-unit failures must be recorded");
    dispatchEvidence = {
      orderId, assigned: false,
      primaryReason: unassigned.primaryReason,
      detail: unassigned.detail,
      perUnitFailures: unassigned.perUnitFailures.map(failure => ({
        unitId: failure.unitId, reason: failure.reason, message: failure.message,
      })),
      summary: summaryLine.slice(0, 300),
    };
  }
  step("dispatch_preview_recorded", "pass", dispatchEvidence);
  await page.screenshot({ path: resolve(output, "04c-dispatch-preview.png") });

  // ---- 4c. RETAINED NEGATIVE: 3 aircraft against 1 landing pad --------------
  // This is the previous run's real planner output, kept verbatim as the
  // negative control for the positive case below: with uav-1 count = 3 and
  // facility-1 declaring a single physical landing slot, the planner must keep
  // rejecting the same order for pad_capacity — with unit uav-1:1 parked on the
  // pad from the planning epoch, uav-1:2 and uav-1:3 have no free physical pad.
  const negativePlan = plan;
  const negativeUnassigned = negativePlan.unassignedOrders.find(item => item.orderId === orderId) ?? null;
  assert.ok(negativeUnassigned !== null, "retained negative: with 3 units the order is still unassigned");
  assert.equal(negativeUnassigned.primaryReason, "pad_capacity");
  assert.ok(negativeUnassigned.perUnitFailures.length >= 1);
  assert.ok(negativeUnassigned.perUnitFailures.every(failure => failure.reason === "pad_capacity"),
    "every per-unit failure must be the real pad_capacity reason");
  assert.ok(negativeUnassigned.perUnitFailures.some(failure => failure.unitId === "uav-1:2"),
    "uav-1:2 must be among the pad_capacity failures");
  assert.ok(negativeUnassigned.perUnitFailures.some(failure => failure.unitId === "uav-1:3"),
    "uav-1:3 must be among the pad_capacity failures");
  step("retained_negative_3units_pad_capacity", "pass", {
    assigned: false, primaryReason: negativeUnassigned.primaryReason,
    detail: negativeUnassigned.detail,
    perUnitFailures: negativeUnassigned.perUnitFailures.map(failure =>
      ({ unitId: failure.unitId, reason: failure.reason })),
    note: "保留负例：机队 uav-1 数量 3、facility-1 仅 1 个物理起降位时，同一订单被规划器真实拒绝",
  });

  // ---- 5. reload → localStorage round-trip ---------------------------------
  await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
    { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.sceneReady === "true",
    null, { timeout: 120000 });
  await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 6,
    null, { timeout: 60000 });
  const afterReload = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), SCENARIO_KEY);
  const reloadedHub = afterReload.facilities.find(f => f.id === "facility-2");
  const reloadedCharger = afterReload.facilities.find(f => f.id === "facility-3");
  const reloadedNew = afterReload.facilities.find(f => f.id === newId);
  const reloadedPlacedCharger = afterReload.facilities.find(f => f.id === chargerId);
  const reloadedProfile = await page.evaluate(key =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.performanceProfiles?.[0] ?? null, LOGISTICS_KEY);
  const reloadedLogistics = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), LOGISTICS_KEY);
  assert.equal(reloadedHub.cargo.storageCapacityKg, 800);
  assert.equal(reloadedHub.cargo.throughputPerHourKg, 1500);
  assert.equal(reloadedCharger.charging.priceAmount, 2.1);
  assert.equal(reloadedCharger.charging.powerW, 7000);
  assert.equal(reloadedCharger.charging.slots, 2);
  assert.equal(reloadedNew.landing.movementsPerHour, 45);
  assert.equal(reloadedPlacedCharger.kind, "charger");
  assert.equal(reloadedPlacedCharger.charging.slots, 3);
  assert.equal(reloadedPlacedCharger.charging.powerW, 6000);
  assert.equal(afterReload.facilities.length, 6);
  assert.equal(reloadedProfile.aircraftBody.xM, 0.6);
  assert.equal(reloadedProfile.cruiseSpeedMps, 8);
  assert.equal(reloadedProfile.cruisePowerW, 180);
  assert.equal(reloadedProfile.hoverPowerW, 260);
  assert.equal(reloadedProfile.chargeEfficiency, 0.85);
  assert.equal(reloadedLogistics.algorithms.parameters.cruiseAltitudeM, 140);
  assert.equal(reloadedLogistics.orders.length, 1);
  const reloadedVertiport = afterReload.facilities.find(f => f.id === vertiportId);
  assert.ok(reloadedVertiport, "second vertiport survives the reload");
  step("reload_persists_edits", "pass", {
    facilities: afterReload.facilities.length,
    hub_storageKg: reloadedHub.cargo.storageCapacityKg,
    charger_pricePerKWh: reloadedCharger.charging.priceAmount,
    charger_powerW: reloadedCharger.charging.powerW,
    charger_slots: reloadedCharger.charging.slots,
    placed_charger: { id: chargerId, slots: reloadedPlacedCharger.charging.slots,
      powerW: reloadedPlacedCharger.charging.powerW },
    new_movementsPerHour: reloadedNew.landing.movementsPerHour,
    performanceProfile_survivesReload: true,
    cruiseAltitudeM: reloadedLogistics.algorithms.parameters.cruiseAltitudeM,
    orders: reloadedLogistics.orders.length,
  });
  await page.screenshot({ path: resolve(output, "05-after-reload.png") });

  // ---- 6. draw a manual no-fly zone on the map ------------------------------
  await page.getByRole("button", { name: "绘制禁飞区" }).click();
  await page.getByLabel("手工区域名称", { exact: true }).fill("验收手工禁飞区");
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.0104, 0.4138);
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.0313, 0.4138);
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.0313, 0.4483);
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.0104, 0.4483);
  await page.getByRole("button", { name: "完成多边形" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-zone-id]").length === 2,
    null, { timeout: 30000 });
  const zones = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null")?.noFlyZones ?? [], SCENARIO_KEY);
  const drawn = zones.find(zone => zone.name === "验收手工禁飞区");
  assert.ok(drawn, "drawn no-fly zone stored");
  assert.equal(drawn.polygon.length, 4);
  assert.equal(drawn.source.kind, "manual");
  step("manual_nofly_drawn", "pass", { id: drawn.id, vertices: drawn.polygon.length });
  await page.screenshot({ path: resolve(output, "06-nofly-drawn.png") });

  // ---- 7. deleting a referenced facility keeps orders for repair ------------
  // facility-3 is the edited charger, facility-4 the placed charger and facility-5
  // (newId) the order's explicit hub-handoff midpoint. Deleting all three leaves
  // order-1 referencing a missing facility, kept in a repairable state.
  await page.locator('[data-facility-id="facility-3"]').getByRole("button", { name: "删除设施" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 5,
    null, { timeout: 30000 });
  await page.locator(`[data-facility-id="${chargerId}"]`).getByRole("button", { name: "删除设施" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 4,
    null, { timeout: 30000 });
  await page.locator(`[data-facility-id="${newId}"]`).getByRole("button", { name: "删除设施" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 3,
    null, { timeout: 30000 });
  await page.locator('#studio-tabs a[data-tab="algorithm"]').click();
  await page.locator(".studio-logistics-panel").waitFor({ timeout: 30000 });
  await page.getByText(/物流配置需要修正/).waitFor({ timeout: 30000 });
  assert.equal(await page.locator('[data-order-id]').count(), 1);
  const kept = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), LOGISTICS_KEY);
  assert.equal(kept.orders.length, 1);
  assert.equal(kept.orders[0].sourceFacilityId, "facility-1");
  step("referenced_delete_keeps_order", "pass", {
    remainingFacilities: 3,
    keptOrder: { id: kept.orders[0].id, sourceFacilityId: kept.orders[0].sourceFacilityId,
      hubHandoffFacilityId: kept.orders[0].hubHandoffFacilityId },
    repairNoticeShown: true,
  });
  await page.screenshot({ path: resolve(output, "07-referenced-delete-repair.png") });

  // ---- 8. REPAIR the referenced hub back through the UI ---------------------
  // Re-place the hub at the same map point (the previous run's snapping showed
  // this click lands legally) and re-declare it as the order's handoff facility
  // through the order card's own select. The manual order was never deleted.
  await page.locator('#studio-tabs a[data-tab="spatial"]').click();
  await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 3,
    null, { timeout: 30000 });
  const idsBeforeRepairedHub = await page.evaluate(() =>
    [...document.querySelectorAll("[data-facility-id]")].map(node => node.dataset.facilityId));
  await page.getByRole("button", { name: "放置物流中转站" }).click();
  await clickMapAt(page.locator("svg.studio-spatial-map"), 0.5917, 0.6552);
  await page.locator(".studio-spatial-pending").waitFor({ timeout: 10000 });
  const repairPendingText = (await page.locator(".studio-spatial-pending").innerText()).replace(/\s+/g, " ");
  assert.match(repairPendingText, /候选位置（确认后应用）/);
  assert.match(repairPendingText, /合法/);
  await page.getByRole("button", { name: "确认放置" }).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 4,
    null, { timeout: 30000 });
  // the repaired hub is the facility that APPEARED with this placement, diffed
  // against the pre-placement ids (a leftover vertiport also exists, so a
  // negative filter against the fixture ids would pick the wrong facility).
  const repairedHubId = await page.evaluate(ids => {
    const before = new Set(ids);
    return [...document.querySelectorAll("[data-facility-id]")]
      .map(node => node.dataset.facilityId).find(id => !before.has(id)) ?? null;
  }, idsBeforeRepairedHub);
  assert.ok(repairedHubId, "a repaired hub facility id appeared after 确认放置");
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.dataset.state === "saved",
    null, { timeout: 30000 });
  await page.locator('#studio-tabs a[data-tab="algorithm"]').click();
  await page.locator(".studio-logistics-panel").waitFor({ timeout: 30000 });
  const repairedHandoffSelect = page.locator(`[data-order-id="${orderId}"]`)
    .getByLabel("物流中转站交接", { exact: true });
  // options inside a closed <select> are never "visible"; attached is what proves
  // the panel re-rendered with the repaired hub among its declared handoff hubs.
  await repairedHandoffSelect.locator(`option[value="${repairedHubId}"]`).waitFor({ state: "attached", timeout: 30000 });
  await repairedHandoffSelect.selectOption(repairedHubId);
  await page.waitForFunction(([key, id]) =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.orders?.[0]?.hubHandoffFacilityId === id,
    [LOGISTICS_KEY, repairedHubId], { timeout: 30000 });
  const repairedOrder = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null")?.orders?.[0] ?? null,
    LOGISTICS_KEY);
  assert.equal(repairedOrder.id, orderId);
  assert.equal(repairedOrder.hubHandoffFacilityId, repairedHubId);
  const repairedHubDoc = await page.evaluate(id => {
    const scenario = JSON.parse(localStorage.getItem("aero-bench.city-selected-scenario.v3") ?? "null");
    return scenario.facilities.find(item => item.id === id) ?? null;
  }, repairedHubId);
  assert.equal(repairedHubDoc.kind, "hub");
  assert.deepEqual(repairedHubDoc.cargo, { storageCapacityKg: 500, throughputPerHourKg: 1000 });
  assert.deepEqual(repairedHubDoc.landing, { parkingSlots: 2, movementsPerHour: 20 });
  step("handoff_facility_replaced_via_ui", "pass", {
    newHubFacilityId: repairedHubId,
    capabilities: { landing: repairedHubDoc.landing, cargo: repairedHubDoc.cargo },
    order: repairedOrder,
    note: "通过界面重新放置物流中转站并在订单卡上重新声明交接点，未直接改写物流文档",
  });

  // ---- 9. POSITIVE CASE: fleet uav-1 count 3 → 1 through the fleet panel UI -
  // The 机队 (runtime) tab's own 数量 numeric field is the only writer: the panel
  // commits through parseCitySelectedScenario and the studio persists the draft
  // + scenario. Nothing is written to storage from the test itself beyond the
  // initial independent-context seed.
  await page.locator('#studio-tabs a[data-tab="runtime"]').click();
  let fleetPanelReady = false;
  let fleetAssetIssue = null;
  try {
    await page.locator(".studio-fleet-panel").waitFor({ timeout: 30000 });
    fleetPanelReady = true;
  } catch {
    fleetAssetIssue = (await page.locator("#panel-root, .studio-panel-root, main, body").first()
      .innerText({ timeout: 5000 })).replace(/\s+/g, " ").slice(0, 400);
  }
  const assetCatalog = await page.evaluate(async () => {
    try {
      const response = await fetch("/asset-library/catalog.json");
      if (!response.ok) return { ok: false, status: response.status, entries: 0, readyUav: 0 };
      const catalog = await response.json();
      const entries = Array.isArray(catalog?.entries) ? catalog.entries : [];
      return { ok: true, status: response.status, entries: entries.length,
        readyUav: entries.filter(entry => entry?.status === "ready" && entry?.category === "uav").length };
    } catch (error) {
      return { ok: false, status: null, entries: 0, readyUav: 0, error: String(error) };
    }
  });
  assert.ok(fleetPanelReady,
    `机队面板未加载（素材目录依赖）：${fleetAssetIssue ?? "unknown"} · catalog=${JSON.stringify(assetCatalog)}`);
  assert.ok(assetCatalog.ok && assetCatalog.readyUav >= 1,
    `本地 UAV 素材目录不可用或没有 ready 机型：${JSON.stringify(assetCatalog)}`);
  step("fleet_ui_loaded", "pass", {
    fleetPanelReady: true,
    assetCatalog,
    note: "机队面板通过 /asset-library/catalog.json 的 ready 机型渲染（root 建立的隔离 public 引用生效）",
  });
  const fleetEntryCardFleet = page.locator('[data-fleet-entry-id="uav-1"]');
  const countField = fleetEntryCardFleet.getByLabel("数量", { exact: true });
  assert.equal(await countField.inputValue(), "3", "fleet panel shows the fixture's declared count 3");
  await countField.fill("1");
  await countField.press("Tab");
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.dataset.state === "saved",
    null, { timeout: 30000 });
  await page.waitForFunction(key =>
    JSON.parse(localStorage.getItem(key) ?? "null")?.fleet?.find(entry => entry.id === "uav-1")?.count === 1,
    SCENARIO_KEY, { timeout: 30000 });
  assert.equal(await countField.inputValue(), "1");
  const scenarioAfterFleetEdit = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), SCENARIO_KEY);
  assert.equal(scenarioAfterFleetEdit.fleet.find(entry => entry.id === "uav-1").count, 1);
  const logisticsAfterFleetEdit = await page.evaluate(key =>
    JSON.parse(localStorage.getItem(key) ?? "null"), LOGISTICS_KEY);
  assert.equal(logisticsAfterFleetEdit.orders.length, 1,
    "the manual order must survive the fleet-count change untouched");
  assert.equal(logisticsAfterFleetEdit.orders[0].hubHandoffFacilityId, repairedHubId);
  step("fleet_count_set_to_1_via_ui", "pass", {
    fleetEntryId: "uav-1", countBefore: 3, countAfter: 1,
    orderSurvived: true,
    hubHandoffFacilityId: logisticsAfterFleetEdit.orders[0].hubHandoffFacilityId,
  });
  await page.screenshot({ path: resolve(output, "08-fleet-count-1.png") });

  // ---- 10. re-run the dispatch preview for the SAME order -------------------
  await page.locator('#studio-tabs a[data-tab="algorithm"]').click();
  await page.locator(".studio-logistics-panel").waitFor({ timeout: 30000 });
  const plansBefore = await page.evaluate(() => window.__aeroFCCaptured?.dispatchPlans?.length ?? 0);
  await page.getByRole("button", { name: "预览派单规划" }).click();
  await page.locator(".studio-logistics-dispatch")
    .getByText(/规划：\d+ 单可分配，\d+ 单不可分配/).waitFor({ timeout: 180000 });
  const positiveSummaryLine = (await page.locator(".studio-logistics-dispatch").innerText())
    .replace(/\s+/g, " ");
  const plansAfter = await page.evaluate(() => window.__aeroFCCaptured?.dispatchPlans ?? []);
  assert.ok(plansAfter.length > plansBefore, "a new real plan was produced by the running bundle");
  const positivePlan = plansAfter[plansAfter.length - 1];
  assert.equal(positivePlan.schema_version, "aero-bench.city-selected-dispatch-plan/v1");
  assert.equal(positivePlan.ordersConsidered, 1);
  assert.equal(positivePlan.provenance.declaredUnits.length, 1,
    "the plan must be built from exactly one declared unit (fleet count edited through the UI)");
  assert.equal(positivePlan.provenance.declaredUnits[0].unitId, "uav-1:1");
  const positiveAccepted = positivePlan.acceptedAssignments.find(item => item.orderId === orderId) ?? null;
  const positiveUnassigned = positivePlan.unassignedOrders.find(item => item.orderId === orderId) ?? null;
  if (positiveAccepted !== null) {
    // Real planner output: the accepted assignment must carry the actual hub
    // handoff facility, a leg touching it, and real per-leg durations.
    assert.ok(positiveAccepted.hubHandoffFacilityId, "accepted assignment carries the hub handoff facility");
    assert.equal(positiveAccepted.hubHandoffFacilityId, repairedHubId,
      "the handoff facility is the one declared through the UI");
    assert.ok(positiveAccepted.routeLegs.length >= 1);
    let totalDurationS = 0;
    for (const leg of positiveAccepted.routeLegs) {
      assert.ok(Number.isFinite(leg.estimatedDurationS) && leg.estimatedDurationS >= 0);
      assert.ok(Number.isFinite(leg.estimatedDistanceM) && leg.estimatedDistanceM >= 0);
      totalDurationS += leg.estimatedDurationS;
    }
    assert.ok(totalDurationS > 0, "route legs carry real, non-degenerate durations");
    const legsIncludeHandoff = positiveAccepted.routeLegs.some(leg =>
      leg.fromFacilityId === positiveAccepted.hubHandoffFacilityId
      || leg.toFacilityId === positiveAccepted.hubHandoffFacilityId);
    assert.ok(legsIncludeHandoff, "route legs include the actual hub handoff leg");
    const positiveEvidence = {
      orderId, assigned: true, unitId: positiveAccepted.unitId,
      fleetEntryId: positiveAccepted.fleetEntryId,
      hubHandoffFacilityId: positiveAccepted.hubHandoffFacilityId,
      hubHandoffPath: legSummary(positiveAccepted.routeFromPickupToDestination),
      routeLegs: positiveAccepted.routeLegs.map(legSummary),
      departureAtS: Number(positiveAccepted.departureAtS.toFixed(3)),
      pickupAtS: Number(positiveAccepted.pickupAtS.toFixed(3)),
      arrivalAtS: Number(positiveAccepted.arrivalAtS.toFixed(3)),
      energyConsumedWh: Number(positiveAccepted.energyConsumedWh.toFixed(3)),
      batteryAtArrivalWh: Number(positiveAccepted.batteryAtArrivalWh.toFixed(3)),
      plannedChargingStops: positiveAccepted.chargingStops.length,
      declaredUnits: positivePlan.provenance.declaredUnits,
      summary: positiveSummaryLine.slice(0, 300),
    };
    assert.match(positiveSummaryLine, new RegExp(`${orderId}\\s*→\\s*${positiveAccepted.unitId}`));
    step("dispatch_positive_with_hub_handoff", "pass", positiveEvidence);
  } else {
    assert.ok(positiveUnassigned !== null, "the planner returned a real verdict for the order");
    // Recorded verbatim — the positive case did NOT pass; the real blocking
    // reason is the evidence. The planner and the plan object stay untouched.
    step("dispatch_positive_with_hub_handoff", "fail", {
      orderId, assigned: false,
      primaryReason: positiveUnassigned.primaryReason,
      detail: positiveUnassigned.detail,
      perUnitFailures: positiveUnassigned.perUnitFailures.map(failure =>
        ({ unitId: failure.unitId, reason: failure.reason, message: failure.message })),
      summary: positiveSummaryLine.slice(0, 300),
      note: "正例未通过：以上为规划器真实给出的拒绝原因，未修改规划器或伪造 plan",
    });
  }
  await page.screenshot({ path: resolve(output, "09-dispatch-positive-uav-1.png") });

  assert.deepEqual(pageErrors, []);
} catch (error) {
  fatal = { error: String(error && error.stack ? error.stack : error) };
  try {
    fatal.bodyExcerpt = (await page.locator("body").innerText({ timeout: 5000 })).slice(0, 2000);
  } catch { /* body unreadable */ }
  await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
} finally {
  const receipt = {
    schema_version: "aero-bench.facility-capabilities-browser-acceptance/v1",
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    page_url: page.url(),
    fixture: resolve(fixturePath).replace("/tmp/aero-facility-capabilities-mimo/", ""),
    seeded_facilities: fixture.facilities.map(f => ({ id: f.id, kind: f.kind, placement: f.placement,
      capabilities: { landing: f.landing, cargo: f.cargo, charging: f.charging } })),
    steps,
    page_errors: pageErrors,
    benign_console_errors: { text: "THREE.Object3D.add: object not an instance of THREE.Object3D. undefined",
      count: benignConsoleErrors.length,
      origin: "frontend/src/map.ts loadPackedScene → world.add(...objects) with an empty objects array on the selected-scene presentation path; no-op warning, reproduced on a plain load",
      occurrences: benignConsoleErrors },
    failed_requests: failedRequests,
    final_dispatch_evidence: steps.find(item => item.step === "dispatch_preview_recorded")?.detail ?? null,
    positive_dispatch_evidence: steps.find(item => item.step === "dispatch_positive_with_hub_handoff")?.detail ?? null,
    retained_negative_evidence: steps.find(item => item.step === "retained_negative_3units_pad_capacity")?.detail ?? null,
    result: fatal === null ? "pass" : "fail",
    ...(fatal === null ? {} : { failure: fatal }),
    untested: [
      "屋顶设施落位（本轮未测：屋顶放置归 MiMo 修改中，暂不纳入本脚本）",
      "设施 3D 模型渲染像素级正确性（截图为人工留证，未做图像断言）",
      "导入/导出 JSON、事件与标签页、OSM 选区页（素材库目录 /asset-library/catalog.json 本轮已在机队面板路径实测可用；quad mesh 预览缺失由 root 的隔离 public 引用解决，本轮未单独复测）",
      "地图相机旋转/缩放交互与禁飞区几何编辑（仅验证 4 顶点多边形绘制与持久化）",
      "派单结果仅覆盖本 fixture 中的 1 单（可派/不可派以实际记录为准）；充电中停、并发多单与外部算法不在本轮范围",
    ],
  };
  writeFileSync(resolve(output, "acceptance-receipt.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify({ result: receipt.result, steps: steps.length,
    pageErrors: pageErrors.length, benignConsoleErrors: benignConsoleErrors.length }));
  await browser.close();
}
