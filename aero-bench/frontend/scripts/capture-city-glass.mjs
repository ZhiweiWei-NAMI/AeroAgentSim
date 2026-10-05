/** Fixed-camera visual acceptance capture; hooks only the downloaded test bundle. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const origin = process.argv[2] ?? 'https://127.0.0.1:5208';
const output = resolve(process.argv[3] ?? '../validation/city-glass');
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
  const setup = await page.evaluate(() => {
    const map = window.__aeroVisualMap;
    map.previewPlaying = false; cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
    map.previewFollowId = null; map.setCameraMode('free'); map.previewSeconds = 60;
    map.camera.position.set(-354.0759223335008,5,-88.51567977112627);
    map.controls.target.set(-309.8815022981839,1.7,-115.91738113695351);
    map.controls.update(); map.configureCityPresentation({mood:'day'});
    const materials = new Map();
    const geometryBytes = {panes:0, frames:0};
    map.buildingPresentation.traverse(node => {
      if(node.isMesh && node.material?.isMeshPhysicalMaterial) materials.set(node.material, node.material.envMapIntensity);
      const kind = node.name === 'Independent reflecting window panes' ? 'panes'
        : node.name === 'Metal window frames and reveals' ? 'frames' : null;
      if(kind) geometryBytes[kind] += Object.values(node.geometry.attributes).reduce((sum, a)=>sum+a.array.byteLength, 0)
        + (node.geometry.index?.array.byteLength ?? 0);
    });
    window.__glassMaterials = materials;
    const read = () => {
      map.renderPreviewFrame();
      const gl = map.renderer.getContext();
      const pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
      gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      return pixels;
    };
    const on = read();
    for(const material of materials.keys()) material.envMapIntensity = 0;
    const off = read();
    for(const [material, intensity] of materials) material.envMapIntensity = intensity;
    read();
    let changed = 0, delta = 0;
    for(let i=0; i<on.length; i+=4) {
      const d = Math.abs(on[i]-off[i])+Math.abs(on[i+1]-off[i+1])+Math.abs(on[i+2]-off[i+2]);
      delta += d; if(d > 6) changed++;
    }
    const local = [...materials.keys()].filter(m=>m.envMap?.isRenderTargetTexture).length;
    if(!local || !changed) throw new Error('No visible local glass reflection response');
    window.__glassStart = map.camera.position.clone();
    return {windowCount:map.buildingPresentation.userData.detailedWindowCount, geometryBytes, localMaterials:local,
      changedPixels:changed, totalPixels:on.length/4, meanRgbDifference:delta/(on.length/4*3),
      eye:map.camera.position.toArray(), focus:map.previewReflectionFocus.toArray()};
  });
  const frames = [];
  for(let frame=0; frame<31; frame++) {
    const stats = await page.evaluate(frame => {
      const map = window.__aeroVisualMap;
      map.camera.position.copy(window.__glassStart);
      map.camera.position.x += frame * .15;
      map.camera.position.y += frame * .025;
      map.controls.update(); map.renderPreviewFrame();
      return {frame, eye:map.camera.position.toArray(), calls:map.renderer.info.render.calls,
        triangles:map.renderer.info.render.triangles};
    }, frame);
    if(frame%10===0) await page.locator('#city-map canvas').screenshot({path:resolve(output, `glass-move-${frame}.png`)});
    frames.push(stats);
  }
  await page.evaluate(() => {
    for(const material of window.__glassMaterials.keys()) material.envMapIntensity = 0;
    window.__aeroVisualMap.renderPreviewFrame();
  });
  await page.locator('#city-map canvas').screenshot({path:resolve(output, 'glass-env-disabled.png')});
  const probeReuse = await page.evaluate(() => {
    const map = window.__aeroVisualMap, probe = map.localReflections;
    const cube = probe.target, filtered = probe.filtered;
    const memory = [];
    for(let i=0; i<8; i++) {
      map.camera.position.copy(window.__glassStart); map.camera.position.y = 15 + (i%3)*15;
      map.recenterCityReflections(); map.renderPreviewFrame();
      if(probe.target !== cube || probe.filtered !== filtered) throw new Error('Reflection targets reallocated on camera relocation');
      memory.push({...map.renderer.info.memory});
    }
    return {sameCubeAndFilteredTarget:true, memory};
  });
  await writeFile(resolve(output, 'reflection-response.json'), JSON.stringify({...setup, probeReuse}, null, 2));
  await writeFile(resolve(output, 'report.json'), JSON.stringify({ loadMs, frames, errors }, null, 2));
  console.log(JSON.stringify({ loadMs, frames, errors }));
  if (errors.length) process.exitCode = 1;
} finally { await browser.close(); }
