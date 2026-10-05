import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5173";
const output = resolve(process.argv[3] ?? "validation/city-streets-citizens");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { origin, captures: [], pageErrors: [], failedResponses: [] };
const variants = ["casual27_m", "sportive06_f", "business03_m", "casual02_f",
  "casual01_m", "business04_f", "sportive09_m", "casual11_m"];

function hashId(id) {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index++) hash = Math.imul(hash ^ id.charCodeAt(index), 16777619);
  return hash >>> 0;
}

try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 750 } });
  page.setDefaultTimeout(180000);
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400) report.failedResponses.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(`${origin}/?scene=1`, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, null, { timeout: 300000 });
  report.sceneStatus = await page.locator("#city-map").evaluate(element => ({
    ready: element.dataset.sceneReady,
    error: element.dataset.sceneErrorMessage,
    road: element.dataset.roadVisual,
  }));
  if (report.sceneStatus.error || report.sceneStatus.road !== "sumo-bigcity") {
    throw new Error(`City scene failed: ${JSON.stringify(report.sceneStatus)}`);
  }
  const map = page.locator("#city-map");
  await map.getByRole("button", { name: "暂停", exact: true }).click();
  async function seek(second) {
    await map.getByRole("slider", { name: "城市演示时间" }).evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, second);
  }
  async function capture(label) {
    const bytes = await map.locator("canvas").screenshot({ path: resolve(output, `${label}.png`), timeout: 180000 });
    const data = await map.evaluate(element => ({
      followId: element.dataset.previewFollowId,
      time: Number(element.querySelector('input[aria-label="城市演示时间"]')?.value),
      bicycles: Number(element.dataset.previewVisibleBicycles),
      pedestrians: Number(element.dataset.previewVisiblePedestrians),
      laneCount: Number(element.dataset.roadLaneCount),
    }));
    const variant = data.followId?.startsWith("person.") ? variants[hashId(data.followId) % variants.length] : undefined;
    report.captures.push({ label, sha256: createHash("sha256").update(bytes).digest("hex"), ...data, variant });
  }

  await map.getByRole("button", { name: "街道视角" }).click();
  await seek(60);
  await capture("road-day");
  await map.getByRole("button", { name: "跟随自行车" }).click();
  await capture("bicycle-60");
  await seek(63);
  await capture("bicycle-63");
  const seen = new Set();
  for (let attempt = 0; attempt < 24 && seen.size < variants.length; attempt++) {
    await map.getByRole("button", { name: "跟随行人" }).click();
    await seek(60.4);
    const id = await map.getAttribute("data-preview-follow-id");
    if (id === null || !id.startsWith("person.")) throw new Error("Pedestrian focus did not select a person");
    const variant = variants[hashId(id) % variants.length];
    if (seen.has(variant)) continue;
    seen.add(variant);
    await capture(`person-${variant}`);
  }
  report.variantsSeen = [...seen];
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

console.log(JSON.stringify(report, null, 2));
if (report.pageErrors.length || report.failedResponses.length || report.captures.length !== 11
    || report.variantsSeen?.length !== 8 || report.captures.some(item => item.laneCount < 1000)
    || report.captures[1]?.followId?.startsWith("bicycle.") !== true
    || report.captures[2]?.followId !== report.captures[1]?.followId
    || report.captures[1]?.sha256 === report.captures[2]?.sha256) process.exitCode = 1;
