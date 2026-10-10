/** Fixed-camera visual acceptance capture; hooks only the downloaded test bundle. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const origin = process.argv[2] ?? 'https://127.0.0.1:5208';
const output = resolve(process.argv[3] ?? '../validation/city-reference');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chromium', headless: true, args: [
  '--enable-gpu', '--use-angle=vulkan', '--enable-features=Vulkan',
  '--disable-vulkan-surface', '--disable-software-rasterizer', '--ignore-gpu-blocklist',
] });
const page = await browser.newPage({ ignoreHTTPSErrors: true, viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
  if (!pattern.test(source)) throw new Error('Visual capture map hook no longer matches');
  await route.fulfill({ response, body: source.replace(pattern,
    match => match.replace('this.map=', 'window.__aeroVisualMap=this.map=')) });
});
try {
  const start = performance.now();
  await page.goto(`${origin}/?scene=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const data = document.querySelector('#city-map')?.dataset;
    return (data?.sceneReady === 'true' && data?.skyReady === 'true') || data?.sceneError === 'true';
  }, undefined, { timeout: 180000 });
  const sceneError = await page.locator('#city-map').getAttribute('data-scene-error-message');
  if (sceneError) throw new Error(`City scene failed: ${sceneError}`);
  const loadMs = performance.now() - start;
  const cameras = [
    { name: 'aerial', eye: [150, 260, 280], target: [-100, 15, -70] },
    { name: 'rooftops', eye: [-125, 100, 70], target: [50, 36, -140] },
    { name: 'neighborhood', eye: [-260, 46, 12], target: [-330, 13, -65] },
    { name: 'street' },
  ];
  const frames = [];
  for (const mood of ['day', 'dusk']) for (const view of cameras) {
    const stats = await page.evaluate(({ mood, view }) => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      map.previewFollowId = null;
      map.previewSeconds = 34;
      map.setCameraMode('free');
      if (view.name === 'street') map.focusPreviewStreet();
      else {
        map.camera.position.set(...view.eye);
        map.controls.target.set(...view.target);
        map.controls.update();
      }
      map.configureCityPresentation({ mood });
      map.renderPreviewFrame();
      const lightCounts = {};
      map.scene.traverse(node => {
        if (node.isLight) lightCounts[node.type] = (lightCounts[node.type] ?? 0) + 1;
      });
      if (lightCounts.AmbientLight !== 1) throw new Error('Imported assets changed global ambient lighting');
      const duskImage = map.duskSky.image;
      const duskPixel = Array.from(duskImage.getContext('2d').getImageData(
        Math.floor(duskImage.width / 2), Math.floor(duskImage.height / 2), 1, 1).data);
      if (duskPixel[3] !== 255) throw new Error('Night environment canvas became transparent');
      const gl = map.renderer.getContext(), debug = gl.getExtension('WEBGL_debug_renderer_info');
      return { calls: map.renderer.info.render.calls, triangles: map.renderer.info.render.triangles,
        programs: map.renderer.info.programs.length,
        lighting: { lightCounts, duskPixel, mood: map.cityMood, exposure: map.renderer.toneMappingExposure,
          sun: map.sun.intensity, hemisphere: map.hemisphere.intensity, ambient: map.ambient.intensity,
          environment: map.scene.environmentIntensity, background: map.scene.backgroundIntensity,
          skyReady: map.root.dataset.skyReady === 'true', groundMaterial: map.horizon.material.type,
          groundReceivesShadow: map.horizon.receiveShadow },
        seconds: map.previewSeconds, eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        buildingStyle: map.buildingPresentation?.userData.buildingStyle,
        buildingCount: map.buildingPresentation?.userData.buildingCount,
        completeSourceBuildingCount: map.buildingPresentation?.userData.completeSourceBuildingCount,
        deferredSourceBuildingCount: map.buildingPresentation?.userData.deferredSourceBuildingCount,
        renderer: String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER)),
        canvas: [map.renderer.domElement.width, map.renderer.domElement.height] };
    }, { mood, view });
    await page.locator('#city-map canvas').screenshot({ path: resolve(output, `${mood}-${view.name}.png`) });
    frames.push({ mood, view, ...stats });
  }
  await writeFile(resolve(output, 'report.json'), JSON.stringify({ loadMs, frames, errors }, null, 2));
  console.log(JSON.stringify({ loadMs, frames, errors }));
  if (errors.length) process.exitCode = 1;
} finally { await browser.close(); }
