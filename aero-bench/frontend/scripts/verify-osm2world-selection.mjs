import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:4175";
const output = resolve(process.argv[3] ?? "validation/osm2world-selection");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const report = { errors: [], checks: {} };
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.route(/\/src\/map\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace("this.root = container;", "globalThis.__selectionMap = this; this.root = container;");
    await route.fulfill({ response, body });
  });
  page.on("pageerror", error => report.errors.push(error.message));
  await page.goto(`${origin}/?scene=1`);
  await page.waitForSelector("#city-map[data-scene-ready=true][data-sky-ready=true]", { timeout: 180000 });
  report.checks = await page.evaluate(() => {
    const map = globalThis.__selectionMap;
    const checks = {};
    let target = null;
    const rect = map.root.getBoundingClientRect();
    for (let y = 0.6; y <= 0.9 && target === null; y += 0.1) {
      for (let x = 0.3; x <= 0.7 && target === null; x += 0.1) {
        const hit = map.intersect(new PointerEvent("pointerdown", { clientX: rect.x + rect.width * x, clientY: rect.y + rect.height * y }));
        if (hit?.kind === "building") target = hit;
      }
    }
    if (target === null) throw Error("no actual building hit by raycast");
    checks.target = target;
    const view = { ...map.staticView, selected: target };
    map.applyStaticView(view);
    checks.selectedOutline = map.selectionOutline.visible;
    map.applyStaticView({ ...view, hiddenEntities: new Set([`building:${target.id}`]) });
    checks.hiddenOutline = !map.selectionOutline.visible;
    map.applyStaticView({ ...view, layers: { ...view.layers, buildings: false } });
    checks.layerOff = !map.selectionOutline.visible && map.world.children.filter(mesh => mesh.userData.layer === "buildings").every(mesh => !mesh.visible);
    map.applyStaticView(view);
    checks.restoredOutline = map.selectionOutline.visible;
    map.applyStaticView({ ...view, isolate: target });
    checks.isolated = map.world.children.every(mesh => !mesh.visible || mesh.geometry.groups.every(group => {
      const face = group.start / 3;
      const range = mesh.userData.ranges.find(range => face < range.end);
      return range?.target?.kind === target.kind && range.target.id === target.id;
    }));
    checks.focused = map.focus(target, { scenario: null });
    map.renderer.render(map.scene, map.camera);
    return checks;
  });
  await page.screenshot({ path: resolve(output, "isolated-building.png") });
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify(report, null, 2));
if (report.errors.length || Object.entries(report.checks).some(([key, value]) => key !== "target" && value !== true)) process.exitCode = 1;
