/** Rooftop vertiport browser acceptance for the selected Shanghai scene (v3).
 *
 * Reuses the Playwright launch and localStorage seeding approach of
 * `frontend/scripts/selected-facility-capabilities-e2e.mjs` (read-only reuse:
 * that script, its fixture and all historical evidence are NOT modified).
 * Writes only to `validation/facility-rooftop-browser/`.
 *
 * Proven in a real headless Chromium against the already-built site:
 *   1. seed the sealed v3 fixture → reload → the selected Shanghai scene
 *      restores (job digest re-verified in-browser) → spatial tab renders;
 *   2. switch 起降点放置 to 屋顶（仅限核验建筑）→ read the 核验支撑建筑
 *      dropdown (measured roof supports with their verified top height);
 *   3. pick a support building that can host the DEFAULT vertiport pad
 *      (14 × 10 × 4.5 m, never resized) → map click → candidate preview →
 *      explicit 确认放置. An illegal candidate is recorded with its exact
 *      reason and never confirmed, and the pad is never shrunk;
 *   4. prove the accepted document stores buildingId + supportHeightM equal to
 *      the dropdown-verified roof-top height, and that the real 3D facility
 *      instance sits at the same base height with an unshrunk footprint;
 *   5. reload → the rooftop facility survives the full re-verification, the
 *      card still reads 屋顶（建筑 …，支撑 … m）, and the rebuilt 3D instance
 *      again sits on the verified support plane;
 *   6. repeat the dropdown flow on a DIFFERENT support building and prove both
 *      rooftop facilities survive one final reload.
 *
 * 3D-instance evidence channel (read-only, no production change): the site
 * ships no debug handle, so an init script dynamically imports the very
 * three.js chunk the page already loads (same module instance, resolved from
 * the served HTML, not hardcoded) and wraps `Object3D.prototype.add` to record
 * facility visuals' `userData` (baseY, footprint, landing surface) as they are
 * added to the scene. Nothing is mutated on disk and no scene object is
 * touched by the probe.
 *
 * Every result (pass, blocked or failure) plus `pageerror` events are recorded
 * into the receipt JSON and screenshots at workspace-relative paths. The
 * script never claims success for a step it could not actually observe.
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.AERO_FC_URL ?? "http://127.0.0.1:5222";
const output = process.env.AERO_ROOFTOP_OUTPUT
  ?? resolve(import.meta.dirname, "../../validation/facility-rooftop-browser");
// Read-only reuse of the sealed v3 fixture owned by the facility-capabilities E2E.
const fixturePath = process.env.AERO_FC_FIXTURE
  ?? resolve(import.meta.dirname, "../../validation/facility-capabilities-20260928/browser/selected-city-config-v3-facility-capabilities.json");

const SCENARIO_KEY = "aero-bench.city-selected-scenario.v3";
const DRAFT_KEY = "aero-bench.city-selected-scene-draft.v1";

/** Default vertiport pad the rooftop candidate must host (never resized). */
const PAD = { widthM: 14, depthM: 10, heightM: 4.5 };
/** Upper bound on distinct support buildings tried in the dropdown ("少量":
 * a small, bounded sample — ordered by verified top height descending, since
 * only the dropdown's own data is used and a bigger roof is more plausible on
 * a taller building; every attempt and every rejection is recorded). */
const MAX_BUILDING_ATTEMPTS = 8;
/** Roof-top height tolerance: three.js stores matrices in float32. */
const HEIGHT_TOLERANCE_M = 0.01;

mkdirSync(output, { recursive: true });
const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
assert.equal(fixture.schema_version, "aero-bench.city-selected-scenario/v3");
const fixtureSha256 = createHash("sha256").update(readFileSync(fixturePath)).digest("hex");

// The three.js chunk filename is read from the served HTML (never hardcoded).
const studioHtml = await (await fetch(new URL("/city-studio.html", baseUrl))).text();
const threeChunk = studioHtml.match(/assets\/(three-[A-Za-z0-9_-]+\.js)/)?.[1] ?? null;

const browser = await chromium.launch({ headless: true,
  args: ["--enable-webgl", "--use-gl=angle", "--use-angle=swiftshader", "--no-proxy-server"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 900 },
  acceptDownloads: true });

// Read-only in-page 3D probe, installed at document start on EVERY navigation
// (so scene rebuilds after confirmations and reloads are all captured). It
// imports the same three.js chunk the page itself loads and wraps
// Object3D.prototype.add to observe facility visuals' userData. It mutates
// nothing: no scene object, no document, no file on disk.
if (threeChunk !== null) {
  await context.addInitScript(async chunkName => {
    try {
      const mod = await import(new URL(`/assets/${chunkName}`, location.href).href);
      // The bundle does not export Object3D by name; locate the shared Object3D
      // prototype by its own method trio, falling back to the isObject3D marker.
      let proto = null;
      outer: for (const value of Object.values(mod)) {
        if (typeof value !== "function" || !value.prototype) continue;
        let node = value.prototype;
        for (let depth = 0; depth < 4 && node; depth++) {
          if (Object.hasOwn(node, "add") && Object.hasOwn(node, "remove")
            && Object.hasOwn(node, "updateMatrixWorld")) { proto = node; break outer; }
          node = Object.getPrototypeOf(node);
        }
      }
      if (proto === null) {
        proto = Object.values(mod).find(value => typeof value === "function"
          && value.prototype?.isObject3D === true)?.prototype ?? null;
      }
      if (proto === null || typeof proto.add !== "function") {
        window.__aeroRooftopProbeState = { chunk: chunkName, ok: false,
          error: "three chunk 中未找到 isObject3D===true 的类原型" };
        return;
      }
      const ctorPrototype = proto;
      if (ctorPrototype.__aeroRooftopProbe === true) {
        window.__aeroRooftopProbeState = { chunk: chunkName, ok: true };
        return;
      }
      ctorPrototype.__aeroRooftopProbe = true;
      const original = ctorPrototype.add;
      ctorPrototype.add = function (...objects) {
        try {
          for (const object of objects) {
            const userData = object && object.userData;
            const target = userData && userData.target;
            if (target && target.kind === "entity" && typeof target.id === "string"
                && target.id.startsWith("facility-") && userData.facilityKind) {
              const store = window.__aeroRooftopFacilities
                ?? (window.__aeroRooftopFacilities = new Map());
              store.set(target.id, {
                id: target.id, kind: userData.facilityKind,
                baseY: object.position ? object.position.y : null,
                footprint: userData.facilityFootprint ?? null,
                landingSurfaceY: userData.landingSurfaceY ?? null,
              });
            }
          }
        } catch { /* the probe must never break the production add() */ }
        return original.apply(this, objects);
      };
      window.__aeroRooftopProbeState = { chunk: chunkName, ok: true };
    } catch (error) {
      window.__aeroRooftopProbeState = { chunk: chunkName, ok: false, error: String(error).slice(0, 300) };
    }
  }, threeChunk);
}

// Seed the sealed v3 fixture into the independent browser context (same
// approach as `selected-facility-capabilities-e2e.mjs`): only this script's
// own keys are written, nothing else is read from or written to user storage.
await context.addInitScript(([key, draftKey, value]) => {
  if (sessionStorage.getItem("aero-fc-seeded") === null) {
    localStorage.setItem(draftKey, JSON.stringify(value.selectedScene));
    localStorage.setItem(key, JSON.stringify(value));
    sessionStorage.setItem("aero-fc-seeded", "true");
  }
}, [SCENARIO_KEY, DRAFT_KEY, fixture]);

const page = await context.newPage();
const pageErrors = [];
const failedRequests = [];
const benignConsoleErrors = [];
const authoringRequests = [];
page.on("request", request => {
  if (request.url().includes("/authoring/")) authoringRequests.push(request.url());
});
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

/** The scene build is heavier than any other page work; while another E2E
 * session is hammering the same server the very first load can stall before
 * issuing any authoring request. Each wait is bounded, every attempt's exact
 * evidence (elapsed, authoring request count, probe state) is recorded, and a
 * retry re-navigates — never a blind or indefinite wait. */
async function waitForSceneReady(label, { attempts = 3, timeoutMs = 120000 } = {}) {
  const evidence = [];
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if (attempt > 1) {
      await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
        { waitUntil: "domcontentloaded" }).catch(() => {});
    }
    const startedAt = Date.now();
    const authoringBefore = authoringRequests.length;
    try {
      await page.waitForFunction(() =>
        document.querySelector("#studio-map")?.dataset.sceneReady === "true", null,
        { timeout: timeoutMs });
      const detail = { attempt, elapsed_ms: Date.now() - startedAt,
        authoring_requests: authoringRequests.length - authoringBefore,
        probe_state: await probeState() };
      evidence.push(detail);
      return { ok: true, evidence };
    } catch (error) {
      const diag = await page.evaluate(() => ({
        url: location.href,
        local_keys: Object.keys(localStorage),
        sessionStorage_seeded: sessionStorage.getItem("aero-fc-seeded"),
        draft_len: (localStorage.getItem("aero-bench.city-selected-scene-draft.v1") ?? "").length,
        body_head: document.body?.innerText?.slice(0, 120) ?? null,
      })).catch(e => ({ diagError: String(e).slice(0, 120) }));
      const detail = { attempt, elapsed_ms: Date.now() - startedAt,
        authoring_requests_total: authoringRequests.length,
        authoring_requests_this_attempt: authoringRequests.length - authoringBefore,
        probe_state: await probeState(), diag,
        error: String(error).split("\n")[0].slice(0, 160) };
      evidence.push(detail);
      step(`scene_ready_wait_${label}_attempt_${attempt}`, "blocked", detail);
    }
  }
  return { ok: false, evidence };
}

/** Map click → placeAt (same coordinate contract as the baseline E2E). The
 * metric map can extend below the fold inside the panel, so it is first
 * scrolled into view, and the click is refused (asserted) if anything but the
 * SVG itself sits on top of the target point — an invisible click would fake
 * a "no candidate" result. */
async function clickMapAt(xFrac, zFrac) {
  const map = page.locator("svg.studio-spatial-map");
  await map.evaluate(element => element.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(150);
  const box = await map.boundingBox();
  assert.ok(box, "spatial map svg has a bounding box");
  const target = { x: box.x + box.width * xFrac, y: box.y + box.height * zFrac };
  const topTag = await page.evaluate(([x, y]) =>
    document.elementFromPoint(x, y)?.tagName?.toLowerCase?.() ?? null, [target.x, target.y]);
  assert.ok(["svg", "rect", "circle", "path", "line", "polyline"].includes(topTag),
    `map click point (${target.x.toFixed(0)}, ${target.y.toFixed(0)}) is occluded by <${topTag}>`);
  await page.mouse.click(target.x, target.y);
}

async function waitForSaved(timeout = 60000) {
  await page.waitForFunction(() =>
    document.querySelector("#studio-save-status")?.dataset.state === "saved", null, { timeout });
}

/** Panel-level alert + card errors + save status, for precise blocking evidence. */
async function panelErrors() {
  return page.evaluate(() => ({
    alerts: [...document.querySelectorAll(".studio-spatial-error")]
      .map(node => node.textContent?.trim()).filter(text => text !== "" && text !== null),
    save_status: document.querySelector("#studio-save-status")?.dataset.state ?? null,
  }));
}

/** Read the persisted selected-scenario document (unit-bearing truth). */
const storedScenario = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), SCENARIO_KEY);

/** Read the read-only in-page 3D probe store (facility visual userData). */
function readProbe() {
  return page.evaluate(() => {
    const store = window.__aeroRooftopFacilities;
    return store === undefined ? null : Object.fromEntries([...store.entries()]
      .map(([id, value]) => [id, JSON.parse(JSON.stringify(value))]));
  });
}

/** Wait until the 3D probe has observed the given facility (scene rebuild is async). */
async function waitForProbeFacility(facilityId, timeout = 30000) {
  await page.waitForFunction(id => {
    const store = window.__aeroRooftopFacilities;
    return store !== undefined && store.has(id);
  }, facilityId, { timeout });
}

const probeState = () => page.evaluate(() => window.__aeroRooftopProbeState ?? null).catch(() => null);

/** Parse the 核验支撑建筑 dropdown options into [{ buildingId, topY, label }]. */
async function readSupportOptions() {
  return page.locator(".studio-spatial-placement select").nth(1).evaluate(element =>
    [...element.querySelectorAll("option")].map(option => {
      const match = option.textContent?.match(/^(.*)（顶面 ([0-9.eE+-]+) m）$/);
      return match ? { buildingId: match[1], topY: Number(match[2]), label: option.textContent }
        : { buildingId: option.value, topY: null, label: option.textContent };
    }));
}

/** Enter rooftop mode, then run one full dropdown → click → candidate →
 * confirm flow for one support building. Returns the accepted facility, or a
 * precise blocker for an illegal candidate (never confirmed, never resized). */
async function placeRooftopVertiport(support, screenshotStem) {
  await page.locator(".studio-spatial-placement select").nth(1).selectOption(support.buildingId);
  await clickMapAt(0.62, 0.60);
  await page.locator(".studio-spatial-pending").waitFor({ timeout: 10000 });
  const pendingText = (await page.locator(".studio-spatial-pending").innerText()).replace(/\s+/g, " ");
  // Panel format: `位移 X m · 朝向 Y° · 合法：…` / `· 不合法：…`
  const legal = pendingText.includes("· 合法：") && !pendingText.includes("· 不合法：");
  const supportHeight = pendingText.match(/支撑高度 ([0-9.eE+-]+) m/)?.[1] ?? null;
  await page.screenshot({ path: resolve(output, `${screenshotStem}-candidate.png`) });
  if (!legal) {
    return { ok: false, blocker: { buildingId: support.buildingId,
      dropdown_option: support.label, candidate_reason: pendingText.slice(0, 400) } };
  }
  assert.ok(supportHeight !== null, `legal rooftop candidate must carry 支撑高度, got: ${pendingText}`);
  await page.getByRole("button", { name: "确认放置" }).click();
  await page.waitForFunction(count => document.querySelectorAll("[data-facility-id]").length === count,
    support.expectedFacilityCount, { timeout: 60000 })
    .catch(async error => {
      throw new Error(`确认放置后设施数量未达到 ${support.expectedFacilityCount}：${error.message.split("\n")[0]}；`
        + `面板状态：${JSON.stringify(await panelErrors())}`);
    });
  await page.waitForFunction(count =>
    document.querySelector("#studio-map")?.dataset.selectedFacilityCount === String(count),
    support.expectedFacilityCount, { timeout: 60000 });
  await waitForSaved();
  const ids = await page.evaluate(() => [...document.querySelectorAll("[data-facility-id]")]
    .map(node => node.dataset.facilityId));
  const newId = ids.find(id => id.startsWith("facility-") && !support.knownFacilityIds.includes(id));
  assert.ok(newId, "a new rooftop facility id appeared after 确认放置");
  const stored = await storedScenario();
  const facility = stored.facilities.find(item => item.id === newId);
  const probeEntry = (await readProbe())?.[newId] ?? null;
  await page.screenshot({ path: resolve(output, `${screenshotStem}-confirmed.png`) });
  return { ok: true, facilityId: newId, facility, probeEntry,
    supportHeightM: Number(supportHeight), pendingText: pendingText.slice(0, 300) };
}

try {
  // ---- 1. load and restore the sealed selected Shanghai scene ----------------
  await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
    { waitUntil: "domcontentloaded" });
  const ready = await waitForSceneReady("initial");
  if (!ready.ok) {
    throw new Error(`选区城市在 3 次尝试（每次 ${120000 / 1000}s）内未就绪；精确等待证据：`
      + `${JSON.stringify(ready.evidence)}`);
  }
  step("scene_ready", "pass", { url: page.url(), wait_evidence: ready.evidence });
  await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 3,
    null, { timeout: 30000 });
  const counts = await page.evaluate(() => ({
    facilities: document.querySelectorAll("[data-facility-id]").length,
    sceneFacilities: document.querySelector("#studio-map")?.dataset.selectedFacilityCount ?? null,
    renderBackend: document.querySelector("#studio-map")?.dataset.renderBackend ?? null,
  }));
  assert.equal(counts.facilities, 3);
  assert.equal(counts.sceneFacilities, "3");
  step("fixture_loaded", "pass", { ...counts, fixture_sha256: fixtureSha256 });
  await page.screenshot({ path: resolve(output, "01-spatial-loaded.png"), fullPage: false });

  // ---- 2. 3D probe state (installed at document start) -----------------------
  const initialProbeState = await probeState();
  const probeReady = initialProbeState?.ok === true;
  step("three_probe_installed", probeReady ? "pass" : "blocked", {
    chunk: threeChunk,
    ...(probeReady ? { note: "只读探针：包装 Object3D.prototype.add 记录设施实例 userData，不修改任何生产模块" }
      : { reason: initialProbeState ?? `无法定位 three chunk（${threeChunk}）` }),
  });

  // ---- 3. 起降点放置模式 → 屋顶（仅限核验建筑） -------------------------------
  await page.getByRole("button", { name: "放置起降点" }).click();
  const placementSelect = page.locator(".studio-spatial-placement select").nth(0);
  await placementSelect.waitFor({ timeout: 10000 });
  assert.equal(await placementSelect.inputValue(), "ground");
  await placementSelect.selectOption("rooftop");
  await page.waitForFunction(() =>
    document.querySelector(".studio-spatial-placement select")?.value === "rooftop", null,
    { timeout: 10000 });
  const supportOptions = await readSupportOptions();
  await page.screenshot({ path: resolve(output, "02-roof-mode-dropdown.png") });
  step("roof_mode_selected", supportOptions.length > 0 ? "pass" : "fail", {
    placement_mode: "rooftop",
    support_building_count: supportOptions.length,
    support_options: supportOptions,
    ...(supportOptions.length === 0
      ? { note: "核验支撑建筑下拉为空：站点未能从真实网格测得任何平屋面支撑，屋顶放置不可用" } : {}),
  });
  if (supportOptions.length === 0) throw new Error("核验支撑建筑下拉为空，屋顶放置流程无法继续");

  // ---- 4. pick supports that can host the DEFAULT 14×10 m pad ----------------
  // The dropdown exposes buildingId + verified top height only. Fit capability is
  // proven by the candidate itself: a LEGAL rooftop candidate means the snapping
  // code compared the DEFAULT 14×10×4.5 pad against the measured support
  // rectangle and roof volume and accepted it. Illegal candidates are recorded
  // and never confirmed; the pad is never shrunk below its default size.
  const knownFacilityIds = await page.evaluate(() => [...document.querySelectorAll("[data-facility-id]")]
    .map(node => node.dataset.facilityId));
  const ordered = [...supportOptions].sort((a, b) => (b.topY ?? -1) - (a.topY ?? -1));
  const candidates = ordered.slice(0, MAX_BUILDING_ATTEMPTS)
    .map(option => ({ ...option, expectedFacilityCount: 4, knownFacilityIds }));
  const untriedCount = ordered.length - candidates.length;

  let accepted = null;
  const rejectedCandidates = [];
  for (const [index, support] of candidates.entries()) {
    const stem = `03-attempt-${index + 1}-${support.buildingId.replace(/[^\w.-]/g, "_")}`;
    const outcome = await placeRooftopVertiport(support, stem);
    if (outcome.ok) {
      accepted = { ...outcome, support };
      step("rooftop_candidate_accepted", "pass", {
        attempt: index + 1, buildingId: support.buildingId,
        dropdown_top_height_m: support.topY, candidate_support_height_m: outcome.supportHeightM,
        facility_id: outcome.facilityId, pending: outcome.pendingText,
      });
      break;
    }
    rejectedCandidates.push({ attempt: index + 1, ...outcome.blocker });
    step("rooftop_candidate_rejected", "blocked", { attempt: index + 1, ...outcome.blocker });
  }
  if (accepted === null) {
    throw new Error(`已尝试 ${rejectedCandidates.length} 个核验支撑建筑（按核验顶面高度降序，`
      + `另有 ${untriedCount} 个更低/相等的建筑未尝试），没有任何屋顶能容纳默认 14×10 m 起降点`
      + `（未确认、未缩放、未伪造）；精确拒绝原因：${JSON.stringify(rejectedCandidates)}`);
  }

  // ---- 5. stored document vs dropdown-verified top height vs 3D instance -----
  const facility = accepted.facility;
  const storedProbe = accepted.probeEntry;
  const heightChecks = {
    stored_buildingId: facility.buildingId,
    stored_supportHeightM: facility.supportHeightM,
    dropdown_topY: accepted.support.topY,
    candidate_supportHeightM: accepted.supportHeightM,
    probe_baseY: storedProbe?.baseY ?? null,
    probe_footprint: storedProbe?.footprint ?? null,
    probe_landingSurfaceY: storedProbe?.landingSurfaceY ?? null,
  };
  assert.equal(facility.placement, "rooftop");
  assert.equal(facility.buildingId, accepted.support.buildingId);
  assert.ok(Math.abs(facility.supportHeightM - accepted.support.topY) <= 1e-6,
    `supportHeightM ${facility.supportHeightM} must equal the dropdown-verified top ${accepted.support.topY}`);
  assert.equal(facility.widthM, PAD.widthM);
  assert.equal(facility.depthM, PAD.depthM);
  assert.equal(facility.heightM, PAD.heightM);
  step("stored_document_rooftop_truth", "pass", heightChecks);
  if (!probeReady) {
    step("three_instance_height_matches", "blocked",
      { reason: "三维实例证据通道不可用", ...heightChecks });
  } else {
    assert.ok(storedProbe, `3D probe observed no instance for ${accepted.facilityId}`);
    assert.equal(storedProbe.kind, "vertiport");
    assert.ok(Math.abs(storedProbe.baseY - facility.supportHeightM) <= HEIGHT_TOLERANCE_M,
      `3D instance baseY ${storedProbe.baseY} must sit on the verified support ${facility.supportHeightM}`);
    assert.ok(Math.abs(storedProbe.footprint.baseY - facility.supportHeightM) <= HEIGHT_TOLERANCE_M,
      `3D footprint baseY ${storedProbe.footprint.baseY} must equal the verified support`);
    assert.ok(storedProbe.footprint.widthM >= PAD.widthM - 1e-6
      && storedProbe.footprint.depthM >= PAD.depthM - 1e-6,
      `3D footprint ${storedProbe.footprint.widthM}×${storedProbe.footprint.depthM} `
        + `must not shrink below the default pad ${PAD.widthM}×${PAD.depthM}`);
    // The footprint userData carries the world-space centre as x/z.
    assert.ok(Math.abs(storedProbe.footprint.x - facility.position.x) <= HEIGHT_TOLERANCE_M
      && Math.abs(storedProbe.footprint.z - facility.position.z) <= HEIGHT_TOLERANCE_M,
      `3D footprint centre (${storedProbe.footprint.x}, ${storedProbe.footprint.z}) `
        + `must equal the stored position (${facility.position.x}, ${facility.position.z})`);
    if (storedProbe.landingSurfaceY !== null) {
      // landingPads are LOCAL to the facility instance root, which itself sits
      // at the verified support height (proven via baseY above); the vertiport
      // pad surface is 0.72 m above that origin.
      assert.ok(Math.abs(storedProbe.landingSurfaceY - 0.72) <= HEIGHT_TOLERANCE_M,
        `landing pad surface offset ${storedProbe.landingSurfaceY} should sit 0.72 m `
          + `above the instance origin on the verified support plane`);
    }
    step("three_instance_height_matches", "pass", {
      tolerance_m: HEIGHT_TOLERANCE_M, ...heightChecks,
      instance_baseY_equals_verified_roof:
        Math.abs(storedProbe.baseY - accepted.support.topY) <= HEIGHT_TOLERANCE_M,
    });
  }
  await page.screenshot({ path: resolve(output, "04-rooftop-placed.png") });

  // ---- 6. reload → full re-verification, rooftop facility persists -----------
  const first = { id: accepted.facilityId, buildingId: facility.buildingId,
    supportHeightM: facility.supportHeightM, position: facility.position };
  await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
    { waitUntil: "domcontentloaded" });
  const readyAfterFirstPlace = await waitForSceneReady("reload_1");
  assert.ok(readyAfterFirstPlace.ok,
    `刷新后选区城市未就绪：${JSON.stringify(readyAfterFirstPlace.evidence)}`);
  await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });  await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 4,
    null, { timeout: 60000 });
  // After a reload the studio save chip keeps the old-draft "dirty" state (the
  // restore path does not perform a save); what proves the re-verification is
  // that every facility is re-rendered from the freshly inspected scenario.
  const afterReload = await storedScenario();
  const reloaded = afterReload.facilities.find(item => item.id === first.id);
  assert.ok(reloaded, "rooftop facility survives the reload");
  assert.equal(reloaded.placement, "rooftop");
  assert.equal(reloaded.buildingId, first.buildingId);
  assert.ok(Math.abs(reloaded.supportHeightM - first.supportHeightM) <= 1e-6);
  assert.ok(Math.abs(reloaded.position.x - first.position.x) <= 1e-6
    && Math.abs(reloaded.position.z - first.position.z) <= 1e-6);
  const cardText = (await page.locator(`[data-facility-id="${first.id}"]`).innerText())
    .replace(/\s+/g, " ");
  assert.match(cardText,
    new RegExp(`屋顶（建筑 ${first.buildingId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}，支撑 ${first.supportHeightM} m）`));
  // The SVG map draws in real metres (viewBox units = m): the rendered rooftop
  // rect must still measure exactly 14×10 and sit at the stored position.
  // Card title is `起降点 · 起降点 2`; the map rect aria-label is `选中起降点 2`.
  const mapRect = await page.evaluate(id => {
    const title = document.querySelector(`[data-facility-id="${id}"] .studio-card-title`)
      ?.textContent ?? "\u0000";
    const name = title.split("·").pop()?.trim();
    const rect = [...document.querySelectorAll("svg.studio-spatial-map rect[aria-label]")]
      .find(node => (node.getAttribute("aria-label") ?? "") === `选中${name}`);
    return rect === undefined ? null : {
      aria_label: rect.getAttribute("aria-label"),
      x: Number(rect.getAttribute("x")), y: Number(rect.getAttribute("y")),
      widthM: Number(rect.getAttribute("width")), depthM: Number(rect.getAttribute("height")),
      transform: rect.getAttribute("transform"),
    };
  }, first.id);
  assert.ok(mapRect !== null, "the rooftop facility is drawn on the metric map");
  assert.ok(Math.abs(mapRect.widthM - PAD.widthM) <= 1e-6 && Math.abs(mapRect.depthM - PAD.depthM) <= 1e-6,
    `map rect ${mapRect.widthM}×${mapRect.depthM} must stay ${PAD.widthM}×${PAD.depthM}`);
  // After the reload the scene rebuilt every facility instance; the probe (still
  // installed) must show the rebuilt 3D instance back on the verified plane.
  const reloadedProbe = (await readProbe())?.[first.id] ?? null;
  if (probeReady) {
    await waitForProbeFacility(first.id);
    assert.ok(Math.abs((reloadedProbe?.baseY ?? 1e9) - reloaded.supportHeightM) <= HEIGHT_TOLERANCE_M,
      `rebuilt 3D instance baseY ${reloadedProbe?.baseY} must sit on ${reloaded.supportHeightM} after reload`);
  }
  step("reload_reverified_rooftop_persists", "pass", {
    facility_id: first.id, card_line: cardText.slice(0, 160), map_rect_metric_units: mapRect,
    persisted: { buildingId: reloaded.buildingId, supportHeightM: reloaded.supportHeightM,
      position: reloaded.position },
    rebuilt_probe_baseY: reloadedProbe?.baseY ?? null,
  });
  await page.screenshot({ path: resolve(output, "05-after-reload.png") });

  // ---- 7. try a DIFFERENT support building through the same dropdown flow ----
  const remaining = supportOptions.filter(option => option.buildingId !== first.buildingId)
    .slice(0, MAX_BUILDING_ATTEMPTS - 1);
  if (remaining.length === 0) {
    step("second_support_building", "blocked",
      { reason: "下拉中除首个成功建筑外没有其他核验支撑建筑可尝试", support_building_count: supportOptions.length });
  } else {
    await page.getByRole("button", { name: "放置起降点" }).click();
    await page.locator(".studio-spatial-placement select").nth(0).selectOption("rooftop");
    await page.waitForFunction(() =>
      document.querySelector(".studio-spatial-placement select")?.value === "rooftop", null,
      { timeout: 10000 });
    const idsBefore = await page.evaluate(() => [...document.querySelectorAll("[data-facility-id]")]
      .map(node => node.dataset.facilityId));
    let second = null;
    const secondRejects = [];
    for (const [index, support] of remaining.entries()) {
      const stem = `06-second-${index + 1}-${support.buildingId.replace(/[^\w.-]/g, "_")}`;
      const outcome = await placeRooftopVertiport(
        { ...support, expectedFacilityCount: 5, knownFacilityIds: idsBefore }, stem);
      if (outcome.ok) { second = { ...outcome, support }; break; }
      secondRejects.push({ attempt: index + 1, ...outcome.blocker });
      step("second_support_candidate_rejected", "blocked", { attempt: index + 1, ...outcome.blocker });
    }
    if (second === null) {
      step("second_support_building", "blocked", {
        reason: `已尝试 ${secondRejects.length} 个其他核验支撑建筑，均无法容纳默认 14×10 m 起降点`
          + `（未确认、未缩放、未伪造；另有 ${supportOptions.length - 1 - secondRejects.length} 个更低建筑未尝试）`,
        rejections: secondRejects,
      });
    } else {
      const secondFacility = second.facility;
      assert.equal(secondFacility.placement, "rooftop");
      assert.equal(secondFacility.buildingId, second.support.buildingId);
      assert.ok(Math.abs(secondFacility.supportHeightM - second.support.topY) <= 1e-6);
      assert.equal(secondFacility.widthM, PAD.widthM);
      assert.equal(secondFacility.depthM, PAD.depthM);
      if (!probeReady) {
        step("second_support_building", "blocked",
          { reason: "已保存第二个屋顶设施，但三维实例证据通道不可用",
            buildingId: secondFacility.buildingId, supportHeightM: secondFacility.supportHeightM });
      } else {
        await waitForProbeFacility(second.facilityId);
        const secondProbe = (await readProbe())?.[second.facilityId] ?? null;
        assert.ok(secondProbe, `3D probe observed no instance for ${second.facilityId}`);
        assert.ok(Math.abs(secondProbe.baseY - secondFacility.supportHeightM) <= HEIGHT_TOLERANCE_M,
          `second 3D instance baseY ${secondProbe.baseY} must sit on ${secondFacility.supportHeightM}`);
        // Final reload: both rooftop facilities must survive together.
        await page.goto(new URL("/city-studio.html?tab=spatial&mode=selected", baseUrl).href,
          { waitUntil: "domcontentloaded" });
        const readyFinal = await waitForSceneReady("reload_2");
        assert.ok(readyFinal.ok,
          `最终刷新后选区城市未就绪：${JSON.stringify(readyFinal.evidence)}`);
        await page.locator(".studio-spatial-panel").waitFor({ timeout: 30000 });
        await page.waitForFunction(() => document.querySelectorAll("[data-facility-id]").length === 5,
          null, { timeout: 60000 });
        const finalStored = await storedScenario();
        const finalFirst = finalStored.facilities.find(item => item.id === first.id);
        const finalSecond = finalStored.facilities.find(item => item.id === second.facilityId);
        assert.equal(finalFirst?.placement, "rooftop");
        assert.equal(finalSecond?.placement, "rooftop");
        assert.equal(finalSecond.buildingId, second.support.buildingId);
        assert.ok(Math.abs(finalSecond.supportHeightM - second.support.topY) <= 1e-6);
        step("final_reload_both_rooftops_persist", "pass", {
          rooftop_facilities: [finalFirst.id, finalSecond.id],
          buildings: [finalFirst.buildingId, finalSecond.buildingId],
          support_heights_m: [finalFirst.supportHeightM, finalSecond.supportHeightM],
        });
        await page.screenshot({ path: resolve(output, "07-final-reload.png") });
        step("second_support_building", "pass", {
          facility_id: second.facilityId, buildingId: secondFacility.buildingId,
          dropdown_top_height_m: second.support.topY, stored_supportHeightM: secondFacility.supportHeightM,
          probe_baseY: secondProbe.baseY,
        });
      }
    }
  }

  assert.deepEqual(pageErrors, []);
} catch (error) {
  fatal = { error: String(error && error.stack ? error.stack : error) };
  try {
    fatal.panel_state = await panelErrors();
    fatal.bodyExcerpt = (await page.locator("body").innerText({ timeout: 5000 })).slice(0, 2000);
  } catch { /* body unreadable */ }
  await page.screenshot({ path: resolve(output, "failure.png") }).catch(() => {});
} finally {
  const probedFacilities = await readProbe().catch(() => null);
  const finalProbeState = await probeState().catch(() => null);
  const receipt = {
    schema_version: "aero-bench.facility-rooftop-browser-acceptance/v1",
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    page_url: page.url(),
    sealed_scenario: {
      job_id: fixture.selectedScene.job_id,
      selection_sha256: fixture.selectedScene.selection_sha256,
      source_id: fixture.selectedScene.selection.source_id,
      bounds_enu_m: fixture.selectedScene.selection.bounds_enu_m,
    },
    fixture: { path: resolve(fixturePath).replace("/tmp/aero-facility-capabilities-mimo/", ""),
      sha256: fixtureSha256, reused_read_only_from: "validation/facility-capabilities-browser" },
    default_pad_required: PAD,
    three_probe: { chunk: threeChunk, final_state: finalProbeState,
      method: "init-script imports the served three chunk and wraps Object3D.prototype.add "
        + "to observe facility visuals' userData; read-only, no production module touched" },
    probe_facilities: probedFacilities,
    steps,
    page_errors: pageErrors,
    benign_console_errors: { text: "THREE.Object3D.add: object not an instance of THREE.Object3D. undefined",
      count: benignConsoleErrors.length,
      origin: "frontend/src/map.ts loadPackedScene → world.add(...objects) with an empty objects array on the selected-scene presentation path; no-op warning, reproduced on a plain load",
      occurrences: benignConsoleErrors },
    failed_requests: failedRequests,
    authoring_request_count: authoringRequests.length,
    result: fatal === null ? "pass" : "fail",
    ...(fatal === null ? {} : { failure: fatal }),
    untested: [
      "无人机起降飞行与调度（本次只验证屋顶落位、文档持久化与三维实例高度证据）",
      "屋顶设施的像素级渲染正确性（截图为人工留证，未做图像断言）",
      "非默认尺寸起降点的屋顶落位（按要求未缩放、未伪造任何尺寸）",
      "地面放置模式回归与物流/订单流程（由既有 facility-capabilities E2E 覆盖）",
      "3D 探针为页面内只读证据通道（import 站点已加载的同一 three chunk 并包装 "
        + "Object3D.prototype.add 记录 userData），未修改任何磁盘生产模块",
    ],
  };
  writeFileSync(resolve(output, "rooftop-browser-receipt.json"),
    JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify({ result: receipt.result, steps: steps.length,
    pageErrors: pageErrors.length, benignConsoleErrors: benignConsoleErrors.length,
    probedFacilities: probedFacilities === null ? null : Object.keys(probedFacilities).length }));
  await browser.close();
}
