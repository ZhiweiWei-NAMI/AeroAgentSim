import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5173";
const output = resolve(process.argv[3] ?? "validation/city-motion-demo");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { origin, checks: [], pageErrors: [], failedResponses: [] };

try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 750 } });
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400) report.failedResponses.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(`${origin}/?scene=1`, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForSelector("#city-map[data-scene-ready=true][data-rendered-uav-count='2']", { timeout: 180000 });
  if (!await page.locator("body").evaluate(element => element.classList.contains("scene-view"))) {
    throw new Error("?scene=1 did not open the full-screen city view");
  }
  const controls = page.locator("#city-map");
  await controls.getByRole("button", { name: "暂停", exact: true }).click();

  async function capture(label) {
    const bytes = await page.locator("#city-map canvas").screenshot({ path: resolve(output, `${label}.png`), timeout: 120000 });
    const dataset = await page.locator("#city-map").evaluate(element => ({ ...element.dataset }));
    return { label, sha256: createHash("sha256").update(bytes).digest("hex"),
      second: Number(dataset.previewSecond), uavs: Number(dataset.renderedUavCount),
      vehicles: Number(dataset.previewVisibleVehicles), bicycles: Number(dataset.previewVisibleBicycles),
      pedestrians: Number(dataset.previewVisiblePedestrians), types: dataset.previewUavTypes,
      followId: dataset.previewFollowId };
  }

  async function seek(second) {
    await controls.getByRole("slider", { name: "城市演示时间" }).evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, second);
    await page.waitForFunction(value => Number(document.querySelector("#city-map")?.dataset.previewSecond) === value,
      second);
  }

  for (const [label, button, startSecond] of [
    ["x500", "跟随 X500", 30], ["camera_quad", "跟随相机四旋翼", 30],
    ["street", "街道视角", 60], ["bicycle", "跟随自行车", 60], ["pedestrian", "跟随行人", 60],
  ]) {
    await controls.getByRole("button", { name: button }).click();
    await seek(startSecond);
    const first = await capture(`${label}-start`);
    await seek(startSecond + 3);
    const last = await capture(`${label}-after`);
    report.checks.push({ label, first, last, canvasChanged: first.sha256 !== last.sha256,
      timeAdvanced: last.second > first.second });
  }

  await seek(30);
  await controls.getByRole("button", { name: "1×", exact: true }).click();
  await controls.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector("#city-map")?.dataset.previewSecond) >= 31,
    undefined, { timeout: 120000 });
  await controls.getByRole("button", { name: "暂停", exact: true }).click();
  report.playbackAdvanced = Number(await controls.getAttribute("data-preview-second")) >= 31;
  const stopped = await page.locator("#city-map").getAttribute("data-preview-second");
  await page.waitForTimeout(900);
  const stillStopped = await page.locator("#city-map").getAttribute("data-preview-second");
  report.pauseHeldTime = stopped === stillStopped;
  await seek(90);
  report.seekReached90 = await page.locator("#city-map").getAttribute("data-preview-second") === "90";
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

console.log(JSON.stringify(report, null, 2));
if (report.pageErrors.length || report.failedResponses.length || !report.playbackAdvanced
    || !report.pauseHeldTime || !report.seekReached90
    || report.checks.length !== 5 || report.checks.some(check => !check.canvasChanged || !check.timeAdvanced
      || check.first.uavs !== 2 || check.last.uavs !== 2 || check.first.types !== "Holybro X500,相机四旋翼")
    || !report.checks.find(check => check.label === "bicycle")?.first.followId?.startsWith("bicycle.")
    || !report.checks.find(check => check.label === "pedestrian")?.first.followId?.startsWith("person.")
    || report.checks.find(check => check.label === "street")?.first.bicycles < 1
    || report.checks.find(check => check.label === "street")?.first.pedestrians < 1) process.exitCode = 1;
