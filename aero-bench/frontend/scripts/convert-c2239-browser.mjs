import { readFileSync, mkdirSync, writeFileSync, existsSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "playwright";

const frontend = resolve(import.meta.dirname, "..");
const inventory = JSON.parse(readFileSync(resolve(frontend, "public/models/incoming/furniture/source-inventory.json"), "utf8"));
const origin = process.argv[2] ?? "http://127.0.0.1:5174";
const output = resolve(frontend, "public/models/incoming/furniture/glb");
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", error => errors.push(String(error)));
await page.goto(`${origin}/asset-library.html`, { waitUntil: "domcontentloaded" });
const results = [];
try {
  for (const asset of inventory.assets) {
    const id = asset.id;
    const mtl = asset.mtllib_in_obj;
    const target = resolve(output, `${id}.glb`);
    if (existsSync(target)) {
      const reloaded = await page.evaluate(async id => {
        const module = await import("/scripts/convert-c2239-browser.ts");
        return module.inspectFurnitureGlb(id);
      }, id);
      results.push({ id, bytes: statSync(target).size, reloaded, resumed: true });
    } else {
      const result = await page.evaluate(async ({ id, mtl }) => {
        const module = await import("/scripts/convert-c2239-browser.ts");
        return module.convertFurniture(id, mtl);
      }, { id, mtl });
      const bytes = Buffer.from(result.base64, "base64");
      writeFileSync(target, bytes);
      results.push({ id, bytes: bytes.length, source: result.source, reloaded: result.reloaded });
    }
    console.log(`${results.length}/${inventory.assets.length} ${id} ${results.at(-1).bytes} bytes`);
  }
  if (errors.length) throw new Error(`Browser errors: ${errors.join("; ")}`);
  writeFileSync(resolve(frontend, "public/models/incoming/furniture/conversion-report.json"),
    JSON.stringify({ schema_version: "aero-bench.c2239-conversion/v1", results }, null, 2) + "\n");
} finally {
  await browser.close();
}
