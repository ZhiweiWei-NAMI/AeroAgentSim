import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5173";
const output = resolve(process.argv[3] ?? "validation/city-traffic-rules");
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
  const map = page.locator("#city-map");
  await map.getByRole("button", { name: "暂停", exact: true }).click();

  async function seek(second) {
    await map.getByRole("slider", { name: "城市演示时间" }).evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, second);
    await page.waitForFunction(value => Number(document.querySelector("#city-map")?.dataset.previewSecond) === value,
      second);
  }

  async function capture(name) {
    const bytes = await map.locator("canvas").screenshot({ path: resolve(output, `${name}.png`), timeout: 120000 });
    const state = await map.evaluate(element => ({ ...element.dataset }));
    return {
      second: Number(state.previewSecond),
      followId: state.previewFollowId,
      bicycles: Number(state.previewVisibleBicycles),
      pedestrians: Number(state.previewVisiblePedestrians),
      sha256: createHash("sha256").update(bytes).digest("hex"),
    };
  }

  for (const [name, button] of [["street", "街道视角"], ["bicycle", "跟随自行车"]]) {
    await map.getByRole("button", { name: button }).click();
    await seek(60);
    const first = await capture(`${name}-60s`);
    await seek(63);
    const last = await capture(`${name}-63s`);
    report.checks.push({ name, first, last, canvasChanged: first.sha256 !== last.sha256 });
  }

  const sceneResponse = await page.request.get(`${origin}/city-presentation/default-scene-v1.json`);
  if (!sceneResponse.ok()) throw new Error(`City scene asset returned ${sceneResponse.status()}`);
  const scene = await sceneResponse.json();
  const source = await page.request.get(new URL(scene.assets.road, origin).href);
  if (!source.ok()) throw new Error(`Road asset returned ${source.status()}`);
  const road = await source.json();
  report.crossingCount = road.crossings.length;
  report.omittedUnmarkedCrossingCount = road.omitted_unmarked_crossings.length;
  report.crossingPolicy = road.crossing_policy;
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

console.log(JSON.stringify(report, null, 2));
if (report.pageErrors.length || report.failedResponses.length || report.checks.length !== 2
    || report.checks.some(check => !check.canvasChanged || check.first.second !== 60
      || check.last.second !== 63 || check.first.bicycles < 1)
    || !report.checks[1].first.followId?.startsWith("bicycle.")
    || report.crossingCount < 1 || report.omittedUnmarkedCrossingCount < 1
    || report.crossingPolicy !== "osm-zebra-or-sumo-controlled-multiway") process.exitCode = 1;
