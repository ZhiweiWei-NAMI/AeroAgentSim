import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const output = process.argv[2] ?? "/tmp/aero-osm-composition";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.route(/\/src\/map\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace("this.root = container;", "globalThis.__compositionMap = this; this.root = container;");
    await route.fulfill({ response, body });
  });
  await page.goto("http://127.0.0.1:4175/?scene=1");
  await page.waitForSelector("#city-map[data-scene-ready=true][data-sky-ready=true]", { timeout: 180000 });
  await page.evaluate(() => {
    const map = globalThis.__compositionMap;
    globalThis.__compositionOrigin = { position: map.camera.position.clone(), target: map.controls.target.clone() };
  });
  for (const [name, angle, tilt] of [["baseline", 0, 0], ["north", -0.65, 0.025], ["park", -1.25, 0.025], ["west", 0.8, 0.025]]) {
    await page.evaluate(({ angle, tilt }) => {
      const map = globalThis.__compositionMap;
      const { position, target } = globalThis.__compositionOrigin;
      const offset = position.clone().sub(target);
      const x = offset.x * Math.cos(angle) + offset.z * Math.sin(angle);
      const z = -offset.x * Math.sin(angle) + offset.z * Math.cos(angle);
      map.camera.position.copy(target).add(offset.set(x, offset.y, z));
      map.controls.target.copy(target);
      map.controls.target.y -= offset.length() * tilt;
      map.scene.fog.color.set("#95a8ac");
      map.scene.fog.near = 1400;
      map.scene.fog.far = 6500;
      map.controls.update();
      map.renderer.render(map.scene, map.camera);
    }, { angle, tilt });
    await page.screenshot({ path: `${output}/${name}.png`, timeout: 120000 });
  }
} finally {
  await browser.close();
}
