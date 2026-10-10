import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:4176";
const output = resolve(process.argv[3] ?? "validation/osm2world");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const report = { origin, errors: [], failedRequests: [], converterDiagnostics: [], screenshots: [], checks: {} };
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on("pageerror", error => report.errors.push(error.message));
  page.on("response", response => { if (response.status() >= 400) report.failedRequests.push({ status: response.status(), url: response.url() }); });
  page.on("console", message => {
    if (message.type() !== "error" && message.type() !== "warning") return;
    if (message.text().includes("THREE.")) report.errors.push(message.text());
    else report.converterDiagnostics.push(message.text());
  });
  await page.goto(`${origin}/?scene=1`);
  await page.waitForSelector("#city-map[data-scene-ready=true][data-sky-ready=true]", { timeout: 180000 });
  const capture = async name => {
    const bytes = await page.screenshot({ path: resolve(output, name), timeout: 120000 });
    report.screenshots.push(name);
    const pixels = await page.evaluate(async encoded => {
      const image = await createImageBitmap(await (await fetch(`data:image/png;base64,${encoded}`)).blob());
      const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext("2d"); context.drawImage(image, 0, 0);
      const data = context.getImageData(0, 0, image.width, image.height).data;
      const colors = new Set(); let total = 0, squares = 0, count = 0;
      for (let y = Math.floor(image.height * 0.5); y < image.height * 0.9; y += 4) {
        for (let x = 20; x < image.width - 20; x += 4) {
          const i = (y * image.width + x) * 4;
          const luma = (data[i] + data[i + 1] + data[i + 2]) / 3;
          colors.add(`${data[i] >> 4}:${data[i + 1] >> 4}:${data[i + 2] >> 4}`);
          total += luma; squares += luma * luma; count++;
        }
      }
      image.close();
      return { colors: colors.size, luminanceStddev: Math.sqrt(squares / count - (total / count) ** 2) };
    }, bytes.toString("base64"));
    report.checks[name] = pixels;
    if (pixels.colors < 12 || pixels.luminanceStddev < 8) report.errors.push(`${name}: blank or insufficiently varied scene pixels`);
    return createHash("sha256").update(bytes).digest("hex");
  };
  report.checks.renderer = await page.locator("#city-map").evaluate(element => ({ ...element.dataset }));
  const before = await capture("desktop.png");
  await page.mouse.move(760, 600); await page.mouse.down();
  await page.mouse.move(940, 610, { steps: 8 }); await page.mouse.up();
  const after = await capture("desktop-orbit.png");
  report.checks.orbitChangesImage = before !== after;
  await page.setViewportSize({ width: 390, height: 844 });
  await capture("mobile.png");
  await page.getByRole("button", { name: "Toggle city view" }).click();
  await capture("mobile-console.png");
  report.checks.mobileConsoleOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  report.checks.sceneToggle = !(await page.locator("body").getAttribute("class"))?.includes("scene-view");
  report.checks.visualAcceptance = "NOT MET: reference comparison requires review; no similarity percentage is inferred from rendering success";
} finally {
  await browser.close();
  await writeFile(resolve(output, "browser-report.json"), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify(report.checks, null, 2));
if (report.errors.length || report.failedRequests.length || report.converterDiagnostics.some(message => /^(ERROR|WARNING)\[/.test(message)) || !report.checks.orbitChangesImage || report.checks.mobileConsoleOverflow) process.exitCode = 1;
