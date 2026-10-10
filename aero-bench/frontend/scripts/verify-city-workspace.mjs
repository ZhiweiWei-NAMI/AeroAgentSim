/** End-to-end checks for the editable city workspace, using only its public UI and assets. */
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const origin = new URL(process.argv[2] ?? "https://127.0.0.1:5208").origin;
const output = resolve(process.argv[3] ?? resolve(scriptDirectory, "../../validation/city-studio-20260926"));
await mkdir(output, { recursive: true });

const report = {
  origin, output, startedAt: new Date().toISOString(), status: "running",
  checks: [], observations: {}, screenshots: [], pageErrors: [],
};
let browser;
let page;

async function check(name, action) {
  const started = performance.now();
  try {
    const result = await action();
    report.checks.push({ name, status: "passed", durationMs: Math.round(performance.now() - started) });
    return result;
  } catch (error) {
    report.checks.push({ name, status: "failed", durationMs: Math.round(performance.now() - started),
      error: error instanceof Error ? error.message : String(error) });
    throw error;
  }
}

async function tab(name) {
  const link = page.locator(`#studio-tabs a[data-tab="${name}"]`);
  await link.click();
  assert.equal(await link.getAttribute("aria-current"), "page", `${name} tab did not activate`);
}

async function waitForDataset(key, expected, timeout = 60000) {
  await page.waitForFunction(([attribute, value]) =>
    document.querySelector("#studio-map")?.getAttribute(attribute) === value,
  [key, String(expected)], { timeout });
}

async function mapView() {
  const svg = page.locator(".studio-spatial-map");
  await svg.scrollIntoViewIfNeeded();
  return svg.evaluate(node => {
    const box = node.getBoundingClientRect();
    const view = node.viewBox.baseVal;
    const scale = Math.min(box.width / view.width, box.height / view.height);
    const left = box.left + (box.width - view.width * scale) / 2;
    const top = box.top + (box.height - view.height * scale) / 2;
    const candidates = [];
    const background = node.firstElementChild;
    const openAt = (x, z) => {
      const hit = document.elementFromPoint(left + (x - view.x) * scale,
        top + (z - view.y) * scale);
      return hit === background || hit === node;
    };
    for (let row = 1; row <= 17; row++) {
      for (let column = 1; column <= 17; column++) {
        const x = view.x + view.width * column / 18;
        const z = view.y + view.height * row / 18;
        const hit = document.elementFromPoint(left + (x - view.x) * scale,
          top + (z - view.y) * scale);
        if (hit !== null && node.contains(hit)) {
          const clearanceScore = [[0, 0], [-15, 0], [15, 0], [0, -15], [0, 15],
            [-15, -15], [-15, 15], [15, -15], [15, 15]]
            .filter(([dx, dz]) => openAt(x + dx, z + dz)).length;
          candidates.push({ x, z, clearanceScore });
        }
      }
    }
    return { minX: view.x, maxX: view.x + view.width, minZ: view.y,
      maxZ: view.y + view.height, width: view.width, height: view.height,
      candidates: candidates.sort((a, b) => b.clearanceScore - a.clearanceScore) };
  });
}

async function clickMapPoint(point) {
  const svg = page.locator(".studio-spatial-map");
  await svg.scrollIntoViewIfNeeded();
  const screen = await svg.evaluate((node, local) => {
    const box = node.getBoundingClientRect();
    const view = node.viewBox.baseVal;
    const scale = Math.min(box.width / view.width, box.height / view.height);
    const left = box.left + (box.width - view.width * scale) / 2;
    const top = box.top + (box.height - view.height * scale) / 2;
    return { x: left + (local.x - view.x) * scale, y: top + (local.z - view.y) * scale };
  }, point);
  await page.mouse.click(screen.x, screen.y);
}

async function placeValidFacility() {
  await page.getByRole("button", { name: "放置起降点", exact: true }).click();
  const view = await mapView();
  assert(view.candidates.length > 0, "The loaded map has no clickable points");
  const cards = page.locator("[data-facility-id]");
  const before = await cards.count();
  const failures = [];
  for (const point of view.candidates) {
    await clickMapPoint(point);
    if (await cards.count() === before + 1) {
      const id = await cards.last().getAttribute("data-facility-id");
      assert(id, "Placed facility has no ID");
      return { id, point: { x: point.x, z: point.z }, attempts: failures.length + 1,
        rejectedCandidates: failures.slice(0, 8), view };
    }
    const message = (await page.locator(".studio-spatial-map-section [role=alert]").textContent())?.trim();
    failures.push({ x: point.x, z: point.z, reason: message ?? "No facility created" });
  }
  throw new Error(`No valid facility location after ${failures.length} UI attempts: ${JSON.stringify(failures.slice(0, 8))}`);
}

function cornerPolygon(view, corner) {
  const insetX = view.width * 0.08, insetZ = view.height * 0.08;
  const sizeX = Math.min(40, view.width * 0.04), sizeZ = Math.min(40, view.height * 0.04);
  const x = corner.right ? view.maxX - insetX - sizeX : view.minX + insetX;
  const z = corner.bottom ? view.maxZ - insetZ - sizeZ : view.minZ + insetZ;
  return [{ x, z }, { x: x + sizeX, z }, { x: x + sizeX, z: z + sizeZ }, { x, z: z + sizeZ }];
}

function distantCorners(view, facilityPoint) {
  return [
    { right: false, bottom: false }, { right: true, bottom: false },
    { right: false, bottom: true }, { right: true, bottom: true },
  ].sort((a, b) => {
    const distance = corner => {
      const polygon = cornerPolygon(view, corner);
      return Math.hypot(polygon[0].x - facilityPoint.x, polygon[0].z - facilityPoint.z);
    };
    return distance(b) - distance(a);
  });
}

function localToLongitudeLatitude(point, originPoint) {
  const radians = Math.PI / 180;
  const scale = 40075016.686 * Math.cos(originPoint.latitude_deg * radians);
  const sine = Math.sin(originPoint.latitude_deg * radians);
  const originMercatorY = Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI) + 0.5;
  const mercatorY = originMercatorY - point.z / scale;
  return [originPoint.longitude_deg + point.x * 360 / scale,
    Math.atan(Math.sinh((mercatorY - 0.5) * 2 * Math.PI)) / radians];
}

async function sceneOrigin() {
  return page.evaluate(async () => {
    const scenePath = new URL(window.location.href).searchParams.get("city");
    if (!scenePath) throw new Error("Studio did not declare its city scene");
    const sceneResponse = await fetch(scenePath);
    if (!sceneResponse.ok) throw new Error(`Scene manifest HTTP ${sceneResponse.status}`);
    const scene = await sceneResponse.json();
    const manifestUrl = new URL(`assets/${scene.mesh_pack.manifest.sha256}`,
      new URL(scene.mesh_pack.base_url, window.location.href));
    const manifestResponse = await fetch(manifestUrl);
    if (!manifestResponse.ok) throw new Error(`Mesh manifest HTTP ${manifestResponse.status}`);
    const manifest = await manifestResponse.json();
    return manifest.projection.origin;
  });
}

async function seek(seconds, expectedPhase) {
  const slider = page.locator("#studio-time");
  assert.equal(await slider.isDisabled(), false, "The visible authoring time control is disabled");
  await slider.evaluate((input, value) => {
    input.value = String(value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, seconds);
  await waitForDataset("data-workspace-flight-phase", expectedPhase);
  const sample = await page.locator("#studio-map").evaluate(node => ({
    time: Number(node.dataset.workspaceTimeSeconds),
    altitudeM: Number(node.dataset.workspaceUavAltitudeM),
    altitudeText: node.dataset.workspaceUavAltitudeM,
    phase: node.dataset.workspaceFlightPhase,
    uavs: Number(node.dataset.workspaceUavCount),
  }));
  assert(Math.abs(sample.time - seconds) < 0.11, `Preview sampled ${sample.time}s instead of ${seconds}s`);
  assert(sample.altitudeText && Number.isFinite(sample.altitudeM), `No UAV altitude at ${seconds}s`);
  assert.equal(sample.uavs, 3, `Authoring UAV count changed at ${seconds}s`);
  return sample;
}

async function screenshot(name) {
  await page.screenshot({ path: resolve(output, name), animations: "disabled" });
  report.screenshots.push(name);
}

try {
  browser = await chromium.launch({ channel: "chromium", headless: true,
    args: ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
      "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"],
  });
  page = await browser.newPage({ ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, acceptDownloads: true });
  page.on("pageerror", error => report.pageErrors.push(error.message));

  await check("load city workspace and hardware scene", async () => {
    await page.goto(`${origin}/city-studio.html?tab=runtime`, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.locator('#studio-map[data-scene-ready="true"]').waitFor({ timeout: 300000 });
    await waitForDataset("data-preview-source", "authoring-draft", 300000);
    assert.equal(await page.locator("#studio-map").getAttribute("data-render-backend"), "hardware");
    assert.equal(await page.locator("#studio-tabs a").count(), 4);
    assert.equal(report.pageErrors.length, 0, `Page errors: ${report.pageErrors.join("; ")}`);
  });

  await check("activate four editor tabs", async () => {
    for (const name of ["runtime", "spatial", "algorithm", "events"]) await tab(name);
    await tab("spatial");
    await page.locator(".studio-spatial-map").waitFor({ timeout: 60000 });
  });

  const placement = await check("place a valid facility on the loaded 2D map and see it in 3D", async () => {
    const result = await placeValidFacility();
    const capacity = page.locator(`[data-facility-id="${result.id}"]`).getByLabel("容量", { exact: true });
    await capacity.fill("3");
    await capacity.dispatchEvent("change");
    await waitForDataset("data-workspace-facility-count", 1);
    assert.equal(await page.locator("#studio-map").getAttribute("data-preview-source"), "authoring-draft");
    await screenshot("facility-on-2d-and-3d.png");
    return result;
  });
  report.observations.facility = { id: placement.id, point: placement.point, attempts: placement.attempts,
    rejectedCandidates: placement.rejectedCandidates };

  await check("reject an overlapping facility", async () => {
    await page.getByRole("button", { name: "放置充电站", exact: true }).click();
    await clickMapPoint(placement.point);
    assert.equal(await page.locator("[data-facility-id]").count(), 1);
    const error = await page.locator(".studio-spatial-map-section [role=alert]").textContent();
    assert(error?.trim(), "Overlap did not show a placement error");
    assert.equal(await page.locator('.studio-spatial-map rect[stroke="#ef4444"]').count(), 1);
    report.observations.conflictMessage = error.trim();
    await page.getByRole("button", { name: "仅选点", exact: true }).click();
  });

  await check("draw a manual no-fly area and import a GeoJSON area", async () => {
    const corners = distantCorners(placement.view, placement.point);
    const manual = cornerPolygon(placement.view, corners[0]);
    await page.getByRole("button", { name: "绘制禁飞区", exact: true }).click();
    for (const point of manual) await clickMapPoint(point);
    await page.getByRole("button", { name: "完成多边形", exact: true }).click();
    await page.locator("[data-airspace-id]").first().waitFor();
    assert.equal(await page.locator("[data-airspace-id]").count(), 1);

    const imported = cornerPolygon(placement.view, corners[1]);
    const originPoint = await sceneOrigin();
    const ring = imported.map(point => localToLongitudeLatitude(point, originPoint));
    ring.push(ring[0]);
    const geojson = { type: "Feature", properties: { name: "E2E GeoJSON 禁飞区" }, geometry: {
      type: "Polygon", coordinates: [ring],
    } };
    await page.getByLabel("选择 GeoJSON 文件").setInputFiles({
      name: "e2e-no-fly.geojson", mimeType: "application/geo+json",
      buffer: Buffer.from(JSON.stringify(geojson)),
    });
    await page.locator("[data-airspace-id]").nth(1).waitFor({ timeout: 30000 });
    assert.equal(await page.locator("[data-airspace-id]").count(), 2);
    report.observations.airspace = { manual, imported, origin: originPoint };
  });

  await check("bind fleet types and counts to its verified landing site", async () => {
    await tab("runtime");
    const asset = page.locator('[name="fleet.0.assetId"]');
    const assetIds = await asset.locator("option").evaluateAll(options => options.map(option => option.value));
    assert(assetIds.length >= 2, "The declared fleet asset choices are missing");
    await asset.selectOption(assetIds[1]);
    await page.locator('[name="fleet.0.assetId"]').selectOption(assetIds[0]);
    await page.locator('[name="fleet.0.count"]').fill("2");
    await page.locator('[name="fleet.0.count"]').dispatchEvent("change");
    await page.locator('[name="fleet.0.homeFacilityId"]').selectOption(placement.id);
    await page.locator('[name="fleet.1.homeFacilityId"]').selectOption(placement.id);
    await page.locator('[name="traffic.vehicles"]').fill("12");
    await page.locator('[name="traffic.vehicles"]').dispatchEvent("change");
    await page.locator('[name="traffic.bicycles"]').fill("4");
    await page.locator('[name="traffic.bicycles"]').dispatchEvent("change");
    await page.locator('[name="traffic.pedestrians"]').fill("6");
    await page.locator('[name="traffic.pedestrians"]').dispatchEvent("change");
    await page.waitForFunction(startedAt => {
      if (Date.now() - startedAt < 500) return false;
      if (document.querySelector("#studio-map")?.getAttribute("data-workspace-uav-count") === "3") return true;
      const state = document.querySelector("#studio-preview-state")?.getAttribute("data-state");
      return state === "ready" || state === "warning" || state === "error"
        || document.querySelector("#studio-save-status")?.getAttribute("data-state") === "error";
    }, Date.now(), { timeout: 90000 });
    const rendered = Number(await page.locator("#studio-map").getAttribute("data-workspace-uav-count"));
    if (rendered !== 3) {
      const issues = await page.locator("#studio-issues").textContent();
      throw new Error(`Only ${rendered} of 3 authoring UAVs passed preview validation at `
        + `X ${placement.point.x.toFixed(1)}, Z ${placement.point.z.toFixed(1)}: ${issues?.trim() ?? "no issue text"}`);
    }
    assert.equal(await page.locator('#studio-follow option[value^="uav.01."]').count(), 2);
    assert.equal(await page.locator('#studio-follow option[value="uav.02"]').count(), 1);
    assert.equal(await page.locator("#studio-map").getAttribute("data-workspace-facility-count"), "1");
  });

  await check("sample takeoff, hover, and landing through the preview time control", async () => {
    const play = page.locator("#studio-play");
    assert.equal(await play.isDisabled(), false, "The visible authoring playback control is disabled");
    if (await page.locator("#studio-map").getAttribute("data-preview-playing") === "true") {
      await play.click();
    }
    await waitForDataset("data-preview-playing", "false");
    const ground = await seek(1, "ground");
    const takeoff = await seek(3.5, "takeoff");
    const hover = await seek(7, "hover");
    await page.locator("#studio-follow").selectOption("uav.01.01");
    await waitForDataset("data-workspace-follow-id", "uav.01.01");
    await screenshot("follow-x500-hover.png");
    await page.locator("#studio-follow").selectOption("uav.02");
    await waitForDataset("data-workspace-follow-id", "uav.02");
    await screenshot("follow-camera-quad-hover.png");
    const landing = await seek(10.5, "landing");
    const settled = await seek(13, "ground");
    assert(takeoff.altitudeM > ground.altitudeM + 2, "Takeoff did not raise the authoring craft");
    assert(hover.altitudeM > takeoff.altitudeM + 2, "Hover altitude did not reach its planned height");
    assert(landing.altitudeM < hover.altitudeM - 2, "Landing did not descend");
    assert(Math.abs(settled.altitudeM - ground.altitudeM) < 0.2, "Craft did not return to its pad");
    report.observations.flightSamples = { ground, takeoff, hover, landing, settled };
  });

  await check("apply rain, snow, and fog draft settings to the scene", async () => {
    await page.locator('[name="environment.preset"]').selectOption("heavyRain");
    await waitForDataset("data-workspace-precipitation", "rain");
    await screenshot("weather-rain.png");
    await page.locator('[name="environment.preset"]').selectOption("snow");
    await waitForDataset("data-workspace-precipitation", "snow");
    await page.locator('[name="environment.preset"]').selectOption("fog");
    await waitForDataset("data-workspace-precipitation", "none");
    await waitForDataset("data-workspace-visibility-m", "350");
  });

  await check("switch centralized and distributed algorithm drafts", async () => {
    await tab("algorithm");
    const mode = page.getByLabel("决策方式", { exact: true });
    await mode.selectOption("distributed");
    assert.match(await page.locator("#studio-panel").textContent(), /各机载代理/);
    await mode.selectOption("centralized");
    assert.match(await page.locator("#studio-panel").textContent(), /中心调度器/);
    await mode.selectOption("distributed");
  });

  await check("edit an event, action rule, and label rule", async () => {
    await tab("events");
    await page.getByRole("button", { name: "添加事件", exact: true }).click();
    const event = page.locator('[role="group"][aria-label^="事件 "]');
    await event.getByLabel("发生时间（秒）").fill("5");
    await event.getByLabel("目标 ID").fill("uav.01");
    await event.getByLabel("事件载荷（JSON 对象）").fill('{"orderId":"e2e-order"}');
    await page.getByRole("button", { name: "添加动作规则", exact: true }).click();
    const action = page.locator('[role="group"][aria-label^="动作规则 "]');
    await action.getByLabel("动作", { exact: true }).selectOption("takeoff");
    await action.getByLabel("执行角色").selectOption("vehicle");
    await page.getByRole("button", { name: "添加标签规则", exact: true }).click();
    const label = page.locator('[role="group"][aria-label^="标签规则 "]');
    await label.getByLabel("Provider 状态字段").fill("status");
    await label.getByLabel("比较值").fill("hover");
    await label.getByLabel("显示标签").fill("悬停");
  });

  await check("save, export, import, and compare the authoring JSON", async () => {
    const draftName = "E2E 城市工作区";
    await page.locator("#studio-name").fill(draftName);
    await page.locator("#studio-name").dispatchEvent("change");
    await page.locator("#studio-save").click();
    await page.locator('#studio-save-status[data-state="saved"]').waitFor();
    const [firstDownload] = await Promise.all([
      page.waitForEvent("download"), page.locator("#studio-export").click(),
    ]);
    const firstPath = resolve(output, "city-workspace-export.json");
    await firstDownload.saveAs(firstPath);
    const exported = JSON.parse(await readFile(firstPath, "utf8"));
    assert.equal(exported.name, draftName);
    assert.equal(exported.purpose, "scenario-authoring");
    assert.equal(exported.facilities.length, 1);
    assert.equal(exported.fleet[0].count, 2);
    assert(exported.fleet.every(entry => entry.homeFacilityId === placement.id));
    assert.deepEqual(exported.traffic, { vehicles: 12, pedestrians: 6, bicycles: 4 });
    assert.equal(exported.airspace.length, 2);
    assert.deepEqual(exported.airspace.map(region => region.source.kind), ["manual", "geojson"]);
    assert.equal(exported.environment.visibilityM, 350);
    assert.equal(exported.algorithms.mode, "distributed");
    assert.equal(exported.events[0].payload.orderId, "e2e-order");
    assert.equal(exported.actionRules[0].action, "takeoff");
    assert.equal(exported.labelRules[0].label, "悬停");

    await page.locator("#studio-name").fill("Temporary name");
    await page.locator("#studio-name").dispatchEvent("change");
    await page.locator("#studio-import").setInputFiles(firstPath);
    await page.waitForFunction(() => document.querySelector("#studio-save-status")?.textContent
      ?.includes("已导入并保存"), null, { timeout: 60000 });
    assert.equal(await page.locator("#studio-name").inputValue(), draftName);
    const [secondDownload] = await Promise.all([
      page.waitForEvent("download"), page.locator("#studio-export").click(),
    ]);
    const secondPath = resolve(output, "city-workspace-roundtrip.json");
    await secondDownload.saveAs(secondPath);
    const roundtrip = JSON.parse(await readFile(secondPath, "utf8"));
    assert.deepEqual(roundtrip, exported);
    report.observations.exportedCounts = { facilities: exported.facilities.length,
      fleet: exported.fleet.length, airspace: exported.airspace.length,
      events: exported.events.length, actionRules: exported.actionRules.length,
      labelRules: exported.labelRules.length };
  });

  await check("finish without page errors", async () => {
    assert.equal(report.pageErrors.length, 0, `Page errors: ${report.pageErrors.join("; ")}`);
    assert(report.screenshots.length >= 2, "Fewer than two screenshots were captured");
  });
  report.status = "passed";
} catch (error) {
  report.status = "failed";
  report.failure = error instanceof Error ? { message: error.message, stack: error.stack } : { message: String(error) };
  process.exitCode = 1;
  if (page !== undefined) {
    try { await screenshot("failure.png"); } catch { /* The page may have closed. */ }
  }
} finally {
  if (report.pageErrors.length) { report.status = "failed"; process.exitCode = 1; }
  report.finishedAt = new Date().toISOString();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ status: report.status, checks: report.checks,
    screenshots: report.screenshots, pageErrors: report.pageErrors, failure: report.failure }, null, 2));
  await browser?.close();
}
