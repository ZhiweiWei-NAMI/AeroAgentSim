/** Measure render submission stalls on the real city, with fixed sampling and camera settings. */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const origin = process.argv[2] ?? "https://127.0.0.1:5208";
const output = resolve(process.argv[3] ?? "../validation/city-playback");
const scenePath = process.argv[4] ?? "/city-presentation/default-scene-v1.json";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chromium", headless: true,
  args: ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
    "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ ignoreHTTPSErrors: true,
  viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));
await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
  if (source.match(new RegExp(pattern.source, "g"))?.length !== 1) {
    throw new Error("City playback benchmark hook no longer matches the built viewer");
  }
  await route.fulfill({ response, body: source.replace(pattern,
    match => match.replace("this.map=", "window.__aeroPlaybackMap=this.map=")) });
});

function stats(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return { median: sorted[Math.floor(sorted.length / 2)],
    p95: sorted[Math.floor(sorted.length * 0.95)], max: sorted.at(-1) };
}

try {
  const started = performance.now();
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.locator("#city-map[data-scene-ready=true]").waitFor({ timeout: 180000 });
  const loadWallMs = performance.now() - started;
  const result = await page.evaluate(async () => {
    const map = window.__aeroPlaybackMap;
    if (!map?.trafficPreview) throw new Error("City playback is not ready");
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
    const gl = map.renderer.getContext(), debug = gl.getExtension("WEBGL_debug_renderer_info");
    const gpuRenderer = String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
    if (/SwiftShader|llvmpipe|software/i.test(gpuRenderer)) throw new Error(`Software GPU: ${gpuRenderer}`);
    const initialPrograms = map.renderer.info.programs.length;
    const sample = seconds => {
      map.previewSeconds = seconds;
      const before = map.renderer.info.programs.length, start = performance.now();
      map.renderPreviewFrame();
      return { seconds, submissionMs: performance.now() - start,
        updateMs: Number(map.root.dataset.previewUpdateMs), renderMs: Number(map.root.dataset.previewRenderMs),
        calls: map.renderer.info.render.calls, triangles: map.renderer.info.render.triangles,
        programs: map.renderer.info.programs.length, newPrograms: map.renderer.info.programs.length - before };
    };
    // Scan the full timeline from the selected UAV, then measure continuous motion.
    const scan = [];
    for (let index = 0; index < 240; index++) {
      scan.push(sample(index / 2));
      if (index % 10 === 0) await new Promise(requestAnimationFrame);
    }
    const picking = [], rect = map.renderer.domElement.getBoundingClientRect();
    for (let index = 0; index < 16; index++) {
      const start = performance.now();
      map.intersect(new PointerEvent("pointermove", {
        clientX: rect.left + rect.width * (0.2 + (index % 4) / 5),
        clientY: rect.top + rect.height * (0.2 + Math.floor(index / 4) / 5),
      }));
      picking.push(performance.now() - start);
    }
    const steady = [];
    let previousRaf = null;
    for (let index = 0; index < 120; index++) {
      const raf = await new Promise(requestAnimationFrame);
      steady.push({ ...sample(30 + index / 30),
        rafIntervalMs: previousRaf === null ? null : raf - previousRaf });
      previousRaf = raf;
    }
    sample(34);
    return { gpuRenderer, canvas: [map.renderer.domElement.width, map.renderer.domElement.height],
      pixelRatio: map.renderer.getPixelRatio(), source: map.trafficPreview.data.source_kind,
      initialPrograms, finalPrograms: map.renderer.info.programs.length, scan, steady, picking };
  });
  await page.locator("#city-map canvas").screenshot({ path: resolve(output, "uav-t34.png") });
  const summary = { loadWallMs, gpuRenderer: result.gpuRenderer, canvas: result.canvas,
    scanSubmissionMs: stats(result.scan.map(frame => frame.submissionMs)),
    steadySubmissionMs: stats(result.steady.map(frame => frame.submissionMs)),
    steadyRafIntervalMs: stats(result.steady.slice(1).map(frame => frame.rafIntervalMs)),
    pickingMs: stats(result.picking), initialPrograms: result.initialPrograms,
    finalPrograms: result.finalPrograms, shaderCompileFrames: result.scan.filter(frame => frame.newPrograms > 0),
    pageErrors };
  await writeFile(resolve(output, "report.json"), JSON.stringify({
    note: "submissionMs measures CPU work and WebGL submission, not completed GPU frame time. RAF intervals include presentation scheduling.",
    summary, ...result }, null, 2) + "\n");
  console.log(JSON.stringify(summary, null, 2));
  if (pageErrors.length) process.exitCode = 1;
} finally {
  await browser.close();
}
