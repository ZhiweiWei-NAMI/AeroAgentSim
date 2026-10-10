import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:4176");
const output = resolve(process.argv[3] ?? "validation/scene-load");
const iterations = Number(process.argv[4] ?? 1);
if (!Number.isInteger(iterations) || iterations < 1 || iterations > 50) throw Error("iterations must be in [1, 50]");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const measurements = [];
try {
  for (let iteration = 0; iteration < iterations; iteration++) {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const page = await context.newPage();
    await page.addInitScript(() => {
      globalThis.__loadMeasure = { tasks: [], shellMs: null, sceneMs: null };
      new PerformanceObserver(list => {
        globalThis.__loadMeasure.tasks.push(...list.getEntries().map(entry => ({ start: entry.startTime, duration: entry.duration })));
      }).observe({ type: "longtask", buffered: true });
      const observer = new MutationObserver(() => {
        const measure = globalThis.__loadMeasure;
        if (measure.shellMs === null && document.querySelector("#city-map")) measure.shellMs = performance.now();
        if (measure.sceneMs === null && document.querySelector("#city-map[data-scene-ready=true][data-sky-ready=true]")) measure.sceneMs = performance.now();
      });
      observer.observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-scene-ready", "data-sky-ready"] });
    });
    for (const cache of ["cold", "warm"]) {
      await page.goto(new URL("?scene=1", origin).href, { waitUntil: "domcontentloaded", timeout: 180000 });
      await page.waitForSelector("#city-map[data-scene-ready=true][data-sky-ready=true]", { timeout: 240000 });
      measurements.push(await page.evaluate(({ iteration, cache }) => ({
        iteration, cache, ...globalThis.__loadMeasure,
        navigation: performance.getEntriesByType("navigation")[0]?.toJSON(),
        resources: performance.getEntriesByType("resource").map(entry => ({ name: entry.name, transferSize: entry.transferSize, encodedBodySize: entry.encodedBodySize, duration: entry.duration })),
        renderer: { ...document.querySelector("#city-map").dataset },
      }), { iteration, cache }));
    }
    await context.close();
  }
} finally {
  await browser.close();
  await writeFile(resolve(output, "load-report.json"), JSON.stringify({ origin: origin.href, iterations, measurements }, null, 2) + "\n");
}
console.log(JSON.stringify(measurements.map(({ iteration, cache, shellMs, sceneMs, tasks }) => ({ iteration, cache, shellMs, sceneMs, longTaskCount: tasks.length, maxLongTaskMs: Math.max(0, ...tasks.map(task => task.duration)) })), null, 2));
