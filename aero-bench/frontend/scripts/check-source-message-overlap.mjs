/**
 * Browser acceptance for the source-message / telemetry-HUD overlap fix.
 *
 * Opens the sealed W1-I5 replay trace through the real UI. The telemetry HUD
 * becomes visible through the normal product path (trace accepted, UAV
 * inspector samples rendered). The source message is then made visible through
 * real product paths only, in two groups:
 *  1. showSourceNote — `page.route` aborts the native city presentation's
 *     `assets/<sha256>` building fetches (browser-side induced fetch failure);
 *     `resolveDeclaredAssets` surfaces "Native scene asset failed: …" while the
 *     trace stays accepted and the HUD stays visible.
 *  2. renderSourceError — the second catalog trace (W3-BACKEND) is loaded while
 *     `page.route` aborts that trace request (browser-side induced fetch
 *     failure), so the load fails and the error message renders.
 *
 * For every viewport (1600×900, 1280×800) and HUD state (expanded, minimized,
 * dragged to the bottom-centre by header drag) the script records both client
 * rects, the intersection area and viewport containment, writes overlap.json
 * plus a screenshot per scenario, and exits non-zero unless every intersection
 * area is 0 and every visible toast is inside the viewport.
 *
 * Usage: node frontend/scripts/check-source-message-overlap.mjs {ORIGIN} <outdir>
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

const origin = process.argv[2];
const output = resolve(process.argv[3] ?? "/tmp/aero-t18-overlap");
if (origin === undefined) {
  console.error("usage: node frontend/scripts/check-source-message-overlap.mjs {ORIGIN} <outdir>");
  process.exit(2);
}
mkdirSync(output, { recursive: true });

const PRIMARY_TRACE =
  "validation/platform-plan-20261001/W1-I5/runs-v8/c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba/public/public-trace.json";
const SECONDARY_TRACE_MATCH = "W3-BACKEND/traffic-execution-v4";
const VIEWPORTS = [{ width: 1600, height: 900 }, { width: 1280, height: 800 }];
const HUD_TIMEOUT_MS = 1_800_000; // the sealed trace is ~275 MB plus ~700 MB of sealed shards
const TOAST_TIMEOUT_MS = 300_000;

const browser = await chromium.launch({ headless: true, args: [
  "--use-gl=angle", "--use-angle=vulkan", "--enable-gpu", "--ignore-gpu-blocklist",
] });

const scenarios = [];
const pageErrors = [];

function overlapArea(a, b) {
  const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  return ix * iy;
}

async function waitHudVisible(page) {
  const started = Date.now();
  await page.waitForFunction(() => {
    const hud = document.querySelector(".telemetry-hud");
    if (hud === null || hud.hidden) return false;
    const rect = hud.getBoundingClientRect();
    return rect.width > 50 && rect.height > 20;
  }, undefined, { timeout: HUD_TIMEOUT_MS, polling: 500 });
  console.log(`  hud visible after ${Math.round((Date.now() - started) / 1000)}s`);
}

async function waitToastVisible(page, timeout = TOAST_TIMEOUT_MS) {
  const started = Date.now();
  await page.waitForFunction(() => {
    const toast = document.querySelector(".source-message");
    return toast !== null && !toast.hidden && toast.textContent.trim().length > 0;
  }, undefined, { timeout, polling: 250 });
  console.log(`  toast visible after ${Math.round((Date.now() - started) / 1000)}s`);
}

async function selectAndLoadTrace(page, pathFragment) {
  const value = await page.evaluate((fragment) => {
    const select = document.querySelector("#workspace-trace-select");
    if (select === null) return null;
    for (const option of select.options) {
      if (option.value.includes(fragment)) return option.value;
    }
    return null;
  }, pathFragment);
  assert.notEqual(value, null, `no workspace trace option matches ${pathFragment}`);
  await page.selectOption("#workspace-trace-select", value);
  await page.locator(".trace-source-control button.action-button").first().click();
}

/** Drive the HUD into `expanded` / `minimized` / `dragged` (bottom-centre) state. */
async function setHudState(page, state, viewport) {
  if (state === "minimized") {
    await page.evaluate(() => {
      const hud = document.querySelector(".telemetry-hud");
      if (hud !== null && !hud.classList.contains("minimized")) {
        hud.querySelector(".gz-minimize")?.click();
      }
    });
  } else {
    await page.evaluate(() => {
      const hud = document.querySelector(".telemetry-hud");
      if (hud !== null && hud.classList.contains("minimized")) {
        hud.querySelector(".gz-minimize")?.click();
      }
    });
  }
  if (state === "dragged") {
    const header = page.locator(".telemetry-hud .gz-header");
    const box = await header.boundingBox();
    assert.notEqual(box, null, "HUD header has no bounding box");
    await page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 15));
    await page.mouse.down();
    await page.mouse.move(viewport.width / 2, viewport.height - 24, { steps: 12 });
    await page.mouse.up();
  }
  if (state === "minimized") {
    await page.waitForFunction(
      () => document.querySelector(".telemetry-hud")?.classList.contains("minimized") === true,
      undefined, { timeout: 10_000, polling: 100 },
    );
  }
  // Let the placement settle after the layout change (drag-end notification,
  // minimize clamp, ResizeObserver tick) before measuring.
  await page.waitForTimeout(400);
}

async function measure(page) {
  return page.evaluate(() => {
    const rectOf = (element) => {
      const r = element.getBoundingClientRect();
      return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    const toast = document.querySelector(".source-message");
    const hud = document.querySelector(".telemetry-hud");
    const toastRect = toast === null ? null : rectOf(toast);
    const hudRect = hud === null || hud.hidden ? null : rectOf(hud);
    const toastInsideViewport = toastRect !== null
      && toastRect.left >= 0 && toastRect.top >= 0
      && toastRect.right <= window.innerWidth + 0.5 && toastRect.bottom <= window.innerHeight + 0.5;
    return {
      toastVisible: toast !== null && !toast.hidden && toast.textContent.trim().length > 0,
      toastClass: toast?.className ?? null,
      toastText: (toast?.textContent ?? "").trim().slice(0, 200),
      appliedStyle: toast === null ? null : { right: toast.style.right || "(css default)", bottom: toast.style.bottom || "(css default)" },
      hudVisible: hud !== null && !hud.hidden,
      hudClass: hud?.className ?? null,
      hudRect,
      toastRect,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      toastInsideViewport,
    };
  });
}

async function recordScenario(page, label, sourcePath, inducedFailure, screenshot, expectToastVisible = true) {
  const m = await measure(page);
  const intersectionArea = m.toastRect === null || m.hudRect === null || !m.hudVisible || !m.toastVisible
    ? 0 : overlapArea(m.toastRect, m.hudRect);
  scenarios.push({
    label,
    sourceMessagePath: sourcePath,
    inducedFailure,
    expectToastVisible,
    viewport: m.viewport,
    toastVisible: m.toastVisible,
    toastClass: m.toastClass,
    toastText: m.toastText,
    appliedStyle: m.appliedStyle,
    hudVisible: m.hudVisible,
    hudClass: m.hudClass,
    toastRect: m.toastRect,
    hudRect: m.hudRect,
    intersectionArea,
    toastInsideViewport: m.toastInsideViewport,
    screenshot,
  });
  await page.screenshot({ path: resolve(output, screenshot), fullPage: false });
  console.log(`  [${label}] intersection=${intersectionArea} toastInView=${m.toastInsideViewport} `
    + `hud=${m.hudVisible} toast=${m.toastVisible} hudRect=${JSON.stringify(m.hudRect)} `
    + `toastRect=${JSON.stringify(m.toastRect)}`);
  return scenarios[scenarios.length - 1];
}

try {
  for (const viewport of VIEWPORTS) {
    const tag = `${viewport.width}x${viewport.height}`;
    console.log(`viewport ${tag}`);
    const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(`${tag}: ${error.message.slice(0, 300)}`));

    // The native presentation fetches building GLBs from replay/assets/<sha256>;
    // aborting those keeps the trace itself accepted (sealed replay shards are
    // replay/artifacts/<sha256> and stay untouched), so the HUD renders and
    // resolveDeclaredAssets surfaces the note-style source message.
    await page.route(/\/assets\/[0-9a-f]{64}(?:$|\?)/, (route) => route.abort("failed"));
    await page.goto(new URL(`/?view=replay&trace=${PRIMARY_TRACE}`, origin).href, { waitUntil: "domcontentloaded" });

    await waitHudVisible(page);
    await waitToastVisible(page);
    console.log(`  toast text: ${(await measure(page)).toastText}`);

    // Group 1: note-style toast while the HUD is visible, across HUD states.
    for (const state of ["expanded", "minimized", "dragged"]) {
      await setHudState(page, state, viewport);
      await recordScenario(
        page, `note-${state}-${tag}`, "showSourceNote (native scene asset failure)",
        "browser-side induced fetch failure: page.route aborts assets/<sha256> building requests",
        `t18-${tag}-note-${state}.png`,
      );
    }

    // Group 2: error-style toast from an aborted second catalog trace load.
    await page.route(`**/${SECONDARY_TRACE_MATCH}/**`, (route) => route.abort("failed"));
    await selectAndLoadTrace(page, SECONDARY_TRACE_MATCH);
    await waitToastVisible(page, 120_000);
    const errorSeen = await page.evaluate(() =>
      document.querySelector(".source-message")?.classList.contains("error") === true);
    assert.equal(errorSeen, true, "expected renderSourceError for the aborted trace load");
    await recordScenario(
      page, `error-aborted-load-${tag}`, "renderSourceError (aborted second catalog trace load)",
      "browser-side induced fetch failure: page.route aborts the selected trace request",
      `t18-${tag}-error-aborted-load.png`,
    );

    // Clear the route and reload successfully: the toast must disappear.
    await page.unroute(`**/${SECONDARY_TRACE_MATCH}/**`);
    await selectAndLoadTrace(page, SECONDARY_TRACE_MATCH);
    await page.waitForFunction(() => {
      const toast = document.querySelector(".source-message");
      return toast === null || toast.hidden || toast.textContent.trim().length === 0;
    }, undefined, { timeout: TOAST_TIMEOUT_MS, polling: 500 });
    await recordScenario(
      page, `error-cleared-reload-${tag}`, "toast cleared by a successful reload",
      "none (route removed, trace loads successfully)",
      `t18-${tag}-error-cleared-reload.png`, false,
    );
    await context.close();
  }
} finally {
  await browser.close();
}

const failedScenarios = scenarios.filter((s) =>
  s.intersectionArea !== 0
  || (s.expectToastVisible && !(s.toastVisible && s.toastInsideViewport))
  || (!s.expectToastVisible && s.toastVisible));
const report = {
  origin,
  primaryTrace: PRIMARY_TRACE,
  secondaryTrace: SECONDARY_TRACE_MATCH,
  inducedFailure: "browser-side induced fetch failure via page.route abort (no product code path mocked)",
  assertions: {
    scenarioCount: scenarios.length,
    allZeroIntersection: scenarios.every((s) => s.intersectionArea === 0),
    allToastsInsideViewport: scenarios.filter((s) => s.toastVisible).every((s) => s.toastInsideViewport),
    allExpectedToastsVisible: scenarios.filter((s) => s.expectToastVisible).every((s) => s.toastVisible),
    clearedScenarioCleared: scenarios.filter((s) => !s.expectToastVisible).every((s) => !s.toastVisible),
    pageErrors,
  },
  scenarios,
};
writeFileSync(resolve(output, "overlap.json"), `${JSON.stringify(report, null, 2)}\n`);
console.log(`overlap.json -> ${resolve(output, "overlap.json")} (${scenarios.length} scenarios, `
  + `${failedScenarios.length} failed, ${pageErrors.length} page errors)`);
if (failedScenarios.length > 0 || pageErrors.length > 0) {
  for (const s of failedScenarios) {
    console.error(`FAIL ${s.label}: intersectionArea=${s.intersectionArea} toastVisible=${s.toastVisible} `
      + `toastInsideViewport=${s.toastInsideViewport}`);
  }
  for (const error of pageErrors) console.error(`PAGEERROR ${error}`);
  process.exit(1);
}
console.log("PASS: every source-message placement clears the HUD and stays inside the viewport");
