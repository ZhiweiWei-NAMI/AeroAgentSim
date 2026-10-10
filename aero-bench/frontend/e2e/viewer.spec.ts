import { expect, test } from "@playwright/test";

// Measured in software Chromium at 1440x900 on a loaded host: #city-map reaches
// data-scene-ready="true" at 30.3 s with the page otherwise idle. The suite's
// 60 s default test timeout is nowhere near enough headroom for that load while
// other pages or host processes compete for CPU, so this test sets its own budget.
const SCENE_READY_TIMEOUT_MS = 240_000;

test("serves the offline OSM2World runtime, style and configured OSM extract", async ({ request }) => {
  const runtime = await request.get("/osm2world/osm2world-core-web.mjs");
  expect(runtime.status(), "osm2world-core-web.mjs").toBe(200);
  expect(runtime.headers()["content-type"]).toMatch(/javascript|ecmascript/);

  const style = await request.get("/osm2world/style/standard.properties");
  expect(style.status(), "standard.properties").toBe(200);
  expect(await style.text()).toContain("material_BUILDING_DEFAULT_texture0_color_file=");

  const osm = await request.get("/osm2world/shanghai-hongqiao.osm.json");
  expect(osm.status(), "shanghai-hongqiao.osm.json").toBe(200);
  const body = await osm.json();
  expect(body.version).toBe(0.6);
  expect(body.bounds).toMatchObject({ minlat: 31.2228, minlon: 121.4636 });
  expect(body.elements.length).toBeGreaterThan(10000);
  expect(body.elements.some((element: { type: string; tags?: { building?: string } }) => element.type === "way" && element.tags?.building !== undefined)).toBe(true);
});

test("live console mounts a WebGL city canvas before any trace is loaded", async ({ page }) => {
  test.setTimeout(SCENE_READY_TIMEOUT_MS);
  await page.goto("/");
  await expect(page.locator("#city-map canvas")).toBeVisible();
  await expect(page.locator(".map-city-badge")).toContainText("上海中心城区");
  await expect(page.locator("#city-map")).toHaveAttribute("data-scene-ready", "true", { timeout: SCENE_READY_TIMEOUT_MS });
});
