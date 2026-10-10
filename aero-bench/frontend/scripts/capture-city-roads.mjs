/** Fixed-camera visual acceptance capture; hooks only the downloaded test bundle. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const origin = process.argv[2] ?? 'https://127.0.0.1:5208';
const output = resolve(process.argv[3] ?? '../validation/city-reference');
const scenePath = process.argv[4] ?? '/city-presentation/default-scene-v1.json';
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
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const data = document.querySelector('#city-map')?.dataset;
    return (data?.sceneReady === 'true' && data?.skyReady === 'true') || data?.sceneError === 'true';
  }, undefined, { timeout: 180000 });
  const sceneError = await page.locator('#city-map').getAttribute('data-scene-error-message');
  if (sceneError) throw new Error(`City scene failed: ${sceneError}`);
  const loadMs = performance.now() - start;
  const cameras = await page.evaluate(async scenePath => {
    const scene = await (await fetch(scenePath)).json();
    const roads = await (await fetch(scene.assets.road)).json();
    const groups = new Map();
    for(const lane of roads.lanes) {
      if(lane.kind !== 'motor' || lane.id.startsWith(':') || lane.width<2.5) continue;
      const edge = lane.id.slice(0,lane.id.lastIndexOf('_'));
      const lanes = groups.get(edge) ?? []; lanes.push(lane); groups.set(edge,lanes);
    }
    const candidates = [...groups.entries()].filter(([,lanes])=>lanes.length>=2)
      .map(([edge,lanes])=> {
        const p=lanes[Math.floor(lanes.length/2)].shape;
        const start=p[0],end=p.at(-1),length=Math.hypot(end[0]-start[0],end[1]-start[1]);
        const x=(start[0]+end[0])/2,z=(start[1]+end[1])/2;
        return {edge,lanes:lanes.length,widths:lanes.map(l=>l.width),x,z,length,
          dx:(end[0]-start[0])/length,dz:(end[1]-start[1])/length};
      }).filter(c=>c.length>60 && Math.hypot(c.x,c.z)<550)
      .sort((a,b)=>b.lanes-a.lanes || Math.hypot(a.x,a.z)-Math.hypot(b.x,b.z));
    const result = [{name:'original-problem-street', eye:[-354.0759223335008,5,-88.51567977112627],
      target:[-309.8815022981839,1.7,-115.91738113695351]}];
    for(const c of candidates) {
      if(result.length>=3) break;
      if(result.some(r=>r.edge && Math.hypot(r.target[0]-c.x,r.target[2]-c.z)<100)) continue;
      result.push({name:`multi-lane-${result.length}`,edge:c.edge,laneCount:c.lanes,widths:c.widths,
        eye:[c.x-c.dx*55-c.dz*12,38,c.z-c.dz*55+c.dx*12],target:[c.x,0,c.z]});
    }
    if(result.length<2) throw new Error('No actual multi-lane street available for visual acceptance');
    const crosswalks = roads.crossings.map(crossing => {
      const shape = crossing.shape;
      const length = shape.slice(1).reduce((sum, p, i) =>
        sum + Math.hypot(p[0] - shape[i][0], p[1] - shape[i][1]), 0);
      return { id: crossing.id, length, x: (shape[0][0] + shape.at(-1)[0]) / 2,
        z: (shape[0][1] + shape.at(-1)[1]) / 2 };
    });
    const original = crosswalks.filter(c => c.id.startsWith(':1889249276_c'))
      .sort((a, b) => b.length - a.length)[0];
    const longest = crosswalks.filter(c => Math.hypot(c.x,c.z) < 550)
      .sort((a, b) => b.length - a.length)[0];
    const connected = crosswalks.find(c => c.id.startsWith(':476792263_c1'));
    for (const [name, crossing] of [['crossing-original-short', original], ['crossing-multi-lane', longest],
      ['crossing-connected-ports', connected]]) {
      if (!crossing) continue;
      result.push({ name, crossingId: crossing.id, crossingLength: crossing.length,
        eye: [crossing.x - 25, 48, crossing.z + 30], target: [crossing.x, 0, crossing.z] });
    }
    return result;
  }, scenePath);
  const frames = [];
  for (const mood of ['day']) for (const view of cameras) {
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
        streetLamps: { count: map.roadPresentation?.userData.streetLampCount,
          clearance: map.roadPresentation?.userData.streetLampClearance },
        directionGuides:map.roadPresentation?.userData.directionGuideCount,
        concretePolygons:map.roadPresentation?.userData.concreteRoadbedPolygonCount,
        streetLayout: (() => {
          const layout = map.roadPresentation?.userData.streetLayout;
          return layout ? { markingCount: layout.markings.length, arrowCount: layout.arrows.length,
            pedestrianConnections: layout.pedestrian_connections?.profile,
            walkingAreaCount: layout.walking_area_count, sidewalkHeight: layout.sidewalk_height_m,
            roadHeight: layout.road_height_m, surfaceStats: layout.sidewalk_stats,
            markingProfile: { counts: layout.marking_profile?.counts,
              surveyed: layout.marking_profile?.surveyed_markings,
              clipped: layout.marking_profile?.final_roadbed_clip?.clipped_markings,
              omitted: layout.marking_profile?.final_roadbed_clip?.omitted_markings } } : null;
        })(),
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
