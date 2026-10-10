import { chromium } from "playwright";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:4175";
const output = resolve(process.argv[3] ?? "validation/osm2world-scene-asset");
await mkdir(output, { recursive: true });
const bytes = await readFile("releases/urban-infrastructure-inspection-v1/world/scene.osm.json");
const sha256 = createHash("sha256").update(bytes).digest("hex");
const browser = await chromium.launch({ headless: true });
const report = { sha256, sizeBytes: bytes.length, errors: [], modes: [] };
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.route("**/asset-probe", route => route.fulfill({ contentType: "text/html", body: '<html><body style="margin:0"><div id="map" style="width:100vw;height:100vh"></div></body></html>' }));
  await page.route(`**/assets/${sha256}`, route => route.fulfill({ contentType: "application/json", body: bytes }));
  page.on("pageerror", error => report.errors.push(error.message));
  page.on("console", message => { if (/^ERROR\[/.test(message.text())) report.errors.push(message.text()); });
  for (const mode of ["replay", "live"]) {
    await page.goto(`${origin}/asset-probe`);
    const result = await page.evaluate(async ({ sha256, sizeBytes, mode }) => {
      const { AssetResolver } = await import("/src/asset-resolver.ts");
      const { loadSceneAsset } = await import("/src/osm2world/assets.ts");
      const { PublicTraceMap } = await import("/src/map.ts");
      const { publicTrace } = await import("/src/testing/trace-v3-fixture.ts");
      const { ControlClient } = await import("/src/run-control.ts");
      const scenario = publicTrace().scenario;
      const asset = { ...scenario.assets[0], asset_id: "asset.osm", media_type: "application/json", sha256, size_bytes: sizeBytes, replay_path: `assets/${sha256}` };
      scenario.assets = [asset];
      scenario.layers = [{ layer_id: "layer.osm", kind: "osm_scene", asset_id: asset.asset_id, visibility: "public", default_visible: true }];
      scenario.entities = [];
      let authorized = false;
      const client = new ControlClient({ baseUrl: location.origin, fetch: async (url, init) => {
        authorized = init.headers.Authorization === `Bearer ${"a".repeat(64)}` && String(url).endsWith(`/assets/${sha256}`);
        return fetch(`/assets/${sha256}`);
      } });
      const resolver = new AssetResolver({ baseHref: location.origin + "/", ...(mode === "live" ? { fetch: () => client.publicAsset({ runId: "b".repeat(64), operatorToken: "a".repeat(64), csrfToken: "c".repeat(64) }, sha256) } : {}) });
      const osm = await loadSceneAsset(scenario, resolver);
      resolver.dispose();
      const map = new PublicTraceMap(document.getElementById("map"), { onPick: () => {}, onHover: () => {}, onContextMenu: () => {} });
      const layers = Object.fromEntries(["buildings", "roads", "terrain", "imagery", "weather", "regions", "uav", "ugv", "pedestrian", "static_assets", "network_links", "trajectories"].map(key => [key, true]));
      map.render({ scenario, osm, sceneState: null, trajectories: [], networkFrame: null }, { layers, hiddenEntities: new Set(), hiddenTrajectories: new Set(), isolate: null, selected: null, hovered: null });
      return { mode, authorized: mode === "replay" || authorized, elements: osm.elements.length };
    }, { sha256, sizeBytes: bytes.length, mode });
    await page.waitForSelector("#map[data-scene-ready=true][data-textures-ready=true][data-sky-ready=true]", { timeout: 180000 });
    result.renderer = await page.locator("#map").evaluate(element => ({ ...element.dataset }));
    await page.screenshot({ path: resolve(output, `${mode}.png`) });
    report.modes.push(result);
  }
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify(report, null, 2));
if (report.errors.length || report.modes.some(mode => !mode.authorized)) process.exitCode = 1;
