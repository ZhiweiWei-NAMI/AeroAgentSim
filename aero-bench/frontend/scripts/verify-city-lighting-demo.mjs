import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5173";
const output = resolve(process.argv[3] ?? "validation/city-lighting-demo");
const scenePath = process.argv[4];
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { origin, scenePath: scenePath ?? "/city-presentation/default-scene-v1.json",
  screenshots: [], pageErrors: [], failedResponses: [] };

try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 750 } });
  page.setDefaultTimeout(120000);
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400) report.failedResponses.push(`${response.status()} ${response.url()}`);
  });
  const loadStarted = performance.now();
  const url = new URL(origin);
  url.searchParams.set("scene", "1");
  if (scenePath !== undefined) url.searchParams.set("city", scenePath);
  await page.goto(url.href, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, null, { timeout: 480000 });
  const map = page.locator("#city-map");
  const sceneError = await map.getAttribute("data-scene-error-message");
  if (sceneError !== null) throw new Error(`City scene failed: ${sceneError}`);
  report.sceneLoadSeconds = Number(((performance.now() - loadStarted) / 1000).toFixed(1));
  report.sceneLoadPhaseSeconds = JSON.parse(await map.getAttribute("data-scene-load-phase-seconds") ?? "{}");
  report.renderBackend = await map.getAttribute("data-render-backend");
  const dimensions = await map.locator("canvas").boundingBox();
  report.fullScreen = await page.locator("body").evaluate(element => element.classList.contains("scene-view"))
    && dimensions !== null && dimensions.width >= 1100 && dimensions.height >= 700;
  report.roadVisual = await map.getAttribute("data-road-visual");
  report.roadLaneCount = Number(await map.getAttribute("data-road-lane-count"));
  report.buildingStyle = await map.getAttribute("data-building-style");
  report.buildingAssetCount = Number(await map.getAttribute("data-building-asset-count"));
  report.buildingReplacements = Number(await map.getAttribute("data-building-replacements"));
  report.buildingVisualParts = Number(await map.getAttribute("data-building-visual-parts"));
  report.buildingUsedAssets = (await map.getAttribute("data-building-used-assets"))?.split(",") ?? [];
  report.streetLampCount = Number(await map.getAttribute("data-street-lamp-count"));
  report.windowMaterialCount = Number(await map.getAttribute("data-building-window-material-count"));
  report.storefrontLightCount = Number(await map.getAttribute("data-storefront-light-count"));
  report.trafficSignalCount = Number(await map.getAttribute("data-traffic-signal-count"));
  report.vehicleLampMaterialCount = Number(await map.getAttribute("data-vehicle-lamp-material-count"));
  report.uavChoiceCount = await map.getByRole("combobox", { name: "跟随无人机" }).locator("option").count() - 1;
  await map.getByRole("button", { name: "暂停", exact: true }).click();
  if (await map.getAttribute("data-city-mood") === "dusk") {
    await map.getByRole("button", { name: "切换日景" }).click();
  }
  async function seek(second) {
    await map.locator('input[aria-label="城市演示时间"]').evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, second);
    await page.waitForFunction(value => Number(document.querySelector("#city-map")?.dataset.previewSecond) === value,
      second);
  }

  async function capture(label) {
    const bytes = await map.locator("canvas").screenshot({ path: resolve(output, `${label}.png`), timeout: 120000 });
    const dataset = await map.evaluate(element => ({ ...element.dataset }));
    const entry = { label, sha256: createHash("sha256").update(bytes).digest("hex"),
      mood: dataset.cityMood, second: Number(dataset.previewSecond), advertisingCount: Number(dataset.advertisingCount),
      vehicles: Number(dataset.previewVisibleVehicles), bicycles: Number(dataset.previewVisibleBicycles),
      followId: dataset.previewFollowId };
    report.screenshots.push(entry);
    return entry;
  }

  await map.getByRole("combobox", { name: "跟随无人机" }).selectOption("uav.01");
  await seek(30);
  const firstUav = await capture("uav-x500-day");
  const firstCamera = await map.evaluate(element => [Number(element.dataset.previewCameraX), Number(element.dataset.previewCameraZ)]);
  await seek(40);
  const firstUavLater = await capture("uav-x500-day-t40");
  const laterCamera = await map.evaluate(element => [Number(element.dataset.previewCameraX), Number(element.dataset.previewCameraZ)]);
  report.followCameraMoved = Math.hypot(laterCamera[0] - firstCamera[0], laterCamera[1] - firstCamera[1]) > 5
    && firstUavLater.followId === "uav.01";
  await map.getByRole("combobox", { name: "跟随无人机" }).selectOption("uav.02");
  const secondUav = await capture("uav-camera-day");
  await map.getByRole("button", { name: "街道视角" }).click();
  const streetDay = await capture("street-day");
  await map.getByRole("button", { name: "切换夜景" }).click();
  const streetDusk = await capture("street-dusk");
  await map.getByRole("button", { name: "路灯与信号灯" }).click();
  const streetLights = await capture("streetlights-dusk");
  await map.getByRole("button", { name: "信号灯近景" }).click();
  const signalCloseup = await capture("signal-dusk");
  report.focusSignalId = await map.getAttribute("data-focus-signal-id");
  await map.getByRole("button", { name: "车灯近景" }).click();
  const vehicleLights = await capture("vehicle-lights-dusk");
  report.focusVehicleId = await map.getAttribute("data-focus-vehicle-id");
  report.headlightBeamCount = Number(await map.getAttribute("data-headlight-beam-count"));
  await map.getByRole("button", { name: "灯光街区" }).click();
  const advertising = await capture("advertising-dusk");
  await map.getByRole("button", { name: "城市总览" }).click();
  const overview = await capture("overview-dusk");
  report.overviewNearbyBuildings = Number(await map.getAttribute("data-overview-nearby-buildings"));
  report.dayNightChanged = streetDusk.sha256 !== streetDay.sha256;
  report.uavSelectionChanged = firstUav.sha256 !== secondUav.sha256
    && firstUav.followId === "uav.01" && secondUav.followId === "uav.02";
  report.advertisingCameraChanged = advertising.sha256 !== streetDusk.sha256;
  report.streetlightCameraChanged = streetLights.sha256 !== streetDusk.sha256;
  report.signalCameraChanged = signalCloseup.sha256 !== streetLights.sha256;
  report.vehicleLightCameraChanged = vehicleLights.sha256 !== signalCloseup.sha256;
  report.overviewCameraChanged = overview.sha256 !== advertising.sha256;
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

console.log(JSON.stringify(report, null, 2));
if (!report.fullScreen || report.roadVisual !== "sumo-bigcity" || report.roadLaneCount < 1000
    || report.buildingStyle !== "modern-glass" || report.buildingAssetCount !== 17
    || report.buildingReplacements < 300 || report.buildingVisualParts < report.buildingReplacements
    || report.buildingUsedAssets.length < 8
    || report.buildingUsedAssets.some(title => !/^Modern Building \d{2}$/.test(title))
    || report.streetLampCount < 100 || report.windowMaterialCount < 17 || report.storefrontLightCount < 100
    || report.trafficSignalCount < 80
    || report.vehicleLampMaterialCount < 10 || report.headlightBeamCount < 1 || !report.focusVehicleId
    || report.uavChoiceCount !== 2 || !report.uavSelectionChanged || !report.followCameraMoved
    || !report.dayNightChanged || !report.advertisingCameraChanged || !report.streetlightCameraChanged
    || !report.signalCameraChanged || !report.focusSignalId || !report.vehicleLightCameraChanged
    || !report.overviewCameraChanged || report.overviewNearbyBuildings < 70
    || report.pageErrors.length || report.failedResponses.length || report.screenshots.length !== 10
    || report.screenshots.some(item => item.advertisingCount < 8 || item.vehicles < 1 || item.bicycles < 1)
    || report.screenshots.map(item => item.mood).join(",") !== "day,day,day,day,dusk,dusk,dusk,dusk,dusk,dusk") process.exitCode = 1;
