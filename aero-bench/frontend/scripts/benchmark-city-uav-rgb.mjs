/** Sample only requested visual frames from the current public city preview. */
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const origin = process.argv[2] ?? "https://127.0.0.1:5208";
const output = resolve(process.argv[3] ?? "../validation/city-gpu-rgb");
const width = 640, height = 480, horizontalFovDegrees = 90, downwardPitchDegrees = 35;
const frameCount = 20, intervalSeconds = 0.5, aircraftId = "uav.02";
await mkdir(output, { recursive: true });

const browser = await chromium.launch({
  channel: "chromium", headless: true,
  args: ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
    "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"],
});
const context = await browser.newContext({ ignoreHTTPSErrors: true,
  viewport: { width: 1000, height: 700 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
  const matches = source.match(new RegExp(pattern.source, "g"));
  if (matches?.length !== 1) throw new Error("City app capture hook no longer matches the built viewer");
  const body = source.replace(pattern, match => match.replace("this.map=", "window.__aeroCaptureMap=this.map="));
  await route.fulfill({ response, body });
});

const begun = performance.now();
let report;
try {
  await page.goto(`${origin}/?scene=1`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.locator("#city-map[data-scene-ready=true]").waitFor({ timeout: 180000 });
  const loaded = performance.now();
  const result = await page.evaluate(({ width, height, horizontalFovDegrees,
      downwardPitchDegrees, frameCount, intervalSeconds, aircraftId }) => {
    const map = window.__aeroCaptureMap;
    if (!map?.trafficPreview) throw new Error("City capture map is not ready");
    const renderer = map.renderer, gl = renderer.getContext();
    const gpuInfo = gl.getExtension("WEBGL_debug_renderer_info");
    const gpuRenderer = String(gl.getParameter(gpuInfo?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
    if (/SwiftShader|llvmpipe|software/i.test(gpuRenderer)) {
      throw new Error(`GPU capture selected software WebGL: ${gpuRenderer}`);
    }
    if (map.trafficPreview.flightData.source_kind !== "planned-visual-flight") {
      throw new Error("The displayed UAV source kind changed; verify camera extrinsics again");
    }
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation);
    map.previewAnimation = 0;
    map.previewFollowId = null;
    renderer.setPixelRatio(1);
    renderer.setSize(width, height, false);
    const camera = map.camera;
    camera.aspect = width / height;
    camera.fov = 2 * Math.atan(Math.tan(horizontalFovDegrees * Math.PI / 360)
      * height / width) * 180 / Math.PI;
    camera.updateProjectionMatrix();
    const rgba = new Uint8Array(width * height * 4);
    const frames = [];
    const frameData = map.trafficPreview.flightData.frames;
    const step = map.trafficPreview.flightData.step_seconds;
    const angle = (before, after, fraction) => {
      const delta = ((after - before + Math.PI) % (Math.PI * 2) + Math.PI * 2)
        % (Math.PI * 2) - Math.PI;
      return before + delta * fraction;
    };
    const begun = performance.now();
    for (let index = 0; index < frameCount; index++) {
      const seconds = index * intervalSeconds;
      const frameIndex = Math.floor(seconds / step);
      const fraction = (seconds - frameIndex * step) / step;
      const before = frameData[frameIndex]?.find(sample => sample[0] === aircraftId);
      const after = frameData[frameIndex + 1]?.find(sample => sample[0] === aircraftId);
      if (!before?.[7] || !after?.[7]) throw new Error(`UAV is not flying at ${seconds}s`);
      const start = performance.now();
      map.trafficPreview.update(seconds, true, true, true, true, false);
      const x = before[1] + (after[1] - before[1]) * fraction;
      const z = before[2] + (after[2] - before[2]) * fraction;
      const y = before[3] + (after[3] - before[3]) * fraction;
      camera.position.set(x, y - 0.35, z);
      camera.rotation.set(-downwardPitchDegrees * Math.PI / 180,
        -angle(before[4], after[4], fraction), 0, "YXZ");
      camera.updateMatrixWorld(true);
      map.controls.target.set(x, 0, z);
      map.focusSunShadow();
      map.trafficPreview.setSignalVisibility(camera, true);
      map.trafficPreview.setVehicleLighting(camera, map.cityMood);
      const prepared = performance.now();
      renderer.render(map.scene, camera);
      const rendered = performance.now();
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
      if (gl.getError() !== gl.NO_ERROR) throw new Error(`GPU RGB readback failed at ${seconds}s`);
      const read = performance.now();
      const rgb = new Uint8Array(width * height * 3);
      for (let row = 0; row < height; row++) {
        const source = (height - 1 - row) * width * 4;
        const target = row * width * 3;
        for (let column = 0; column < width; column++) {
          const src = source + column * 4, dst = target + column * 3;
          rgb[dst] = rgba[src]; rgb[dst + 1] = rgba[src + 1]; rgb[dst + 2] = rgba[src + 2];
        }
      }
      const finished = performance.now();
      frames.push({ seconds, position: [x, y, z], yawRadians: angle(before[4], after[4], fraction),
        prepareMs: prepared - start, renderMs: rendered - prepared,
        readbackMs: read - rendered, rgbCopyMs: finished - read,
        totalMs: finished - start, drawCalls: renderer.info.render.calls,
        rgbByteLength: rgb.byteLength });
      (window.__aeroRgbFrames ??= []).push(rgb);
    }
    return { gpuRenderer, sourceKind: map.trafficPreview.flightData.source_kind,
      captureMs: performance.now() - begun, frames };
  }, { width, height, horizontalFovDegrees, downwardPitchDegrees,
    frameCount, intervalSeconds, aircraftId });
  const sampled = performance.now();
  const hashes = [];
  for (let index = 0; index < frameCount; index++) {
    const base64 = await page.evaluate(index => {
      const bytes = window.__aeroRgbFrames[index];
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 16384) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
      }
      return btoa(binary);
    }, index);
    const rgb = Buffer.from(base64, "base64");
    if (rgb.byteLength !== width * height * 3) throw new Error(`RGB byte count changed at frame ${index}`);
    const ppm = Buffer.concat([Buffer.from(`P6\n${width} ${height}\n255\n`), rgb]);
    await writeFile(resolve(output, `uav02-${String(index).padStart(2, "0")}.ppm`), ppm);
    hashes.push(createHash("sha256").update(rgb).digest("hex"));
  }
  report = {
    source: "current city-presentation demo", sourceKind: result.sourceKind,
    aircraftId, width, height, horizontalFovDegrees, downwardPitchDegrees,
    sampleTimesSeconds: result.frames.map(frame => frame.seconds), gpuRenderer: result.gpuRenderer,
    sceneLoadWallMs: loaded - begun, captureWallMs: sampled - loaded,
    browserCaptureMs: result.captureMs, fileTransferWriteMs: performance.now() - sampled,
    totalWallMs: performance.now() - begun, frames: result.frames,
    rgbSha256: hashes, pageErrors: errors,
  };
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ output, gpuRenderer: report.gpuRenderer,
    sceneLoadWallMs: report.sceneLoadWallMs, captureWallMs: report.captureWallMs,
    browserCaptureMs: report.browserCaptureMs, fileTransferWriteMs: report.fileTransferWriteMs,
    totalWallMs: report.totalWallMs,
    frameMs: report.frames.map(frame => Number(frame.totalMs.toFixed(2))),
    uniqueFrames: new Set(hashes).size, pageErrors: errors }));
  if (errors.length || new Set(hashes).size !== frameCount) process.exitCode = 1;
} finally {
  await browser.close();
}
