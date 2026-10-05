import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5173";
const output = resolve(process.argv[3] ?? "validation/city-pedestrian-lighting");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { origin, shots: [], pageErrors: [], failedResponses: [] };

try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 750 } });
  page.setDefaultTimeout(180000);
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("response", response => {
    if (response.status() >= 400) report.failedResponses.push(`${response.status()} ${response.url()}`);
  });
  await page.goto(`${origin}/?scene=1`, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForSelector("#city-map[data-scene-ready=true][data-preview-visible-pedestrians]", { timeout: 180000 });
  const map = page.locator("#city-map");
  await map.getByRole("button", { name: "暂停", exact: true }).click();
  await map.getByRole("button", { name: "跟随行人" }).click();

  async function seek(second) {
    await map.getByRole("slider", { name: "城市演示时间" }).evaluate((element, value) => {
      element.value = String(value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }, second);
  }

  async function capture(label) {
    const bytes = await map.locator("canvas").screenshot({ path: resolve(output, `${label}.png`), timeout: 180000 });
    const values = await map.evaluate(element => ({
      mood: element.dataset.cityMood,
      followId: element.dataset.previewFollowId,
      pedestrians: Number(element.dataset.previewVisiblePedestrians),
      time: Number(element.querySelector('input[aria-label="城市演示时间"]')?.value),
    }));
    report.shots.push({ label, sha256: createHash("sha256").update(bytes).digest("hex"), ...values });
  }

  await seek(60); await capture("pedestrian-day-60");
  await seek(60.4); await capture("pedestrian-day-60_4");
  await seek(61.3); await capture("pedestrian-day-61_3");
  await map.getByRole("button", { name: "切换夜景" }).click();
  await capture("pedestrian-dusk-61_3");
  await map.getByRole("button", { name: "路灯与信号灯" }).click();
  await capture("streetlights-dusk");
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

console.log(JSON.stringify(report, null, 2));
if (report.pageErrors.length || report.failedResponses.length || report.shots.length !== 5
    || report.shots.slice(0, 4).some(shot => !shot.followId?.startsWith("person.") || shot.pedestrians < 1)
    || report.shots[0].sha256 === report.shots[1].sha256
    || report.shots[1].sha256 === report.shots[2].sha256
    || report.shots[2].sha256 === report.shots[3].sha256
    || report.shots.map(shot => shot.mood).join(",") !== "day,day,day,dusk,dusk") process.exitCode = 1;
