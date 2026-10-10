/** Capture thumbnails from the actual interactive GLBs, not source-package illustrations. */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(join(frontendRoot, "scripts/uav-preview-models.json"), "utf8"));
const baseUrl = process.argv[2] ?? "http://127.0.0.1:5173";
const models = manifest;
const browser = await chromium.launch({ headless: true, args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-webgl"] });
const output = join(frontendRoot, "public/models/uav/thumbnails");
mkdirSync(output, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  page.setDefaultTimeout(60000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(new URL("/asset-library.html", baseUrl).href, { waitUntil: "networkidle" });
  for (const model of models) {
    await page.locator(`[data-asset-id="model:${model.id}"]`).click();
    await page.locator("#preview-loading").waitFor({ state: "hidden", timeout: 30000 });
    const mode = await page.locator("#preview-mode").innerText();
    if (mode !== "交互式 3D") throw new Error(`${model.id}: ${mode}`);
    await page.waitForTimeout(180);
    const clip = await page.locator("#preview-canvas").boundingBox();
    if (!clip) throw new Error(`${model.id}: preview canvas has no visible bounds`);
    writeFileSync(join(output, `${model.id}.png`), await page.screenshot({ clip, timeout: 60000 }));
    process.stdout.write(`${model.id}\n`);
  }
  if (errors.length) throw new Error(errors.join("\n"));
} finally {
  await browser.close();
}
