import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { IncidentFX } from './incident-fx';
import { Rain, rainCount } from './rain';
import { WindStreaks, streakCount } from './wind';
import { applyWeatherMaterials, fogDensity, observerFogOffset, setupScene } from '../scene';
import { fxHash, fxSeed } from './fx-shared';
import type { Conditions } from '../conditions';
import { AtmosphereLayer } from './index';
import { worldPosition } from '../coordinates';
import { QualityMaterials } from '../quality-materials';

vi.mock('three', async importOriginal => {
  const three = await importOriginal<typeof import('three')>();
  return { ...three, PMREMGenerator: class {
    fromScene() { return new three.WebGLRenderTarget(1, 1); }
    dispose() {}
  } };
});

vi.mock('./fx-shared', async importOriginal => ({
  ...await importOriginal<typeof import('./fx-shared')>(),
  smokeAtlas: async () => new T.Texture(),
}));
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    beginPath() {}, arc() {}, fill() {}, stroke() {}, moveTo() {}, bezierCurveTo() {}, fillRect() {},
  } as unknown as ReturnType<HTMLCanvasElement['getContext']>);
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const conditions: Conditions = {
  wind: { east: 6, north: 0, speed: 6, fromDeg: 270, gust: false },
  precipitationMmH: 4, visibilityM: 500, light: 'day',
  incidents: [{ id: 'fire-a', kind: 'fire', label: 'Building fire', position: [20, 40, 0], radiusM: 30, phase: 'active', sinceS: 5, activeS: 5, startS: 7.5 }],
};
const camera = new T.PerspectiveCamera(); camera.position.set(60, 40, -60); camera.updateMatrixWorld();

/** Instance matrices (smoke/flames) or slot seeds — identical at the same (conditions, t). */
function matrices(mesh: T.InstancedMesh): number[] {
  return [...mesh.instanceMatrix.array as Float32Array].map(value => Math.fround(value));
}
const meshes = (unit: T.Object3D) => unit.children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh<T.BufferGeometry, T.ShaderMaterial>[];
const renderer = () => ({ toneMappingExposure: 1.12, domElement: { height: 1800 }, getPixelRatio: () => 2 }) as T.WebGLRenderer;

describe('atmosphere determinism: same (conditions, t) → same instance matrices', () => {
  it('smoke puffs repeat exactly at the same simulation time', async () => {
    const first = new IncidentFX('med'), second = new IncidentFX('med');
    await Promise.all([first.ready, second.ready]);
    const ring = new Map<string, number>();
    first.update(conditions.incidents, 12.5, camera, ring, undefined, () => 0);
    second.update(conditions.incidents, 12.5, camera, ring, undefined, () => 0);
    const gather = (layer: IncidentFX) => [...layer.root.children[0].children]
      .filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh[];
    const a = gather(first), b = gather(second);
    expect(a.length).toBe(b.length);
    expect(a[1].count).toBeGreaterThan(0);
    for (let i = 0; i < a.length; i++) expect(matrices(a[i])).toEqual(matrices(b[i]));
    first.dispose(); second.dispose();
  });
  it('rain and wind streak counts follow rate/speed and their seeds are pure functions of the slot', () => {
    expect(rainCount(1, 'high')).toBe(1500); expect(rainCount(4, 'high')).toBe(4000);
    expect(rainCount(10, 'high')).toBe(8000); expect(rainCount(10, 'low')).toBe(Math.round(8000 / 3));
    expect(streakCount(1, 'high')).toBe(0); expect(streakCount(12, 'high')).toBeLessThanOrEqual(160);
    expect(streakCount(12, 'low')).toBeLessThanOrEqual(40);
    const rain = new Rain('med'), wind = new WindStreaks('med');
    rain.update(4, 30, camera, conditions.wind ?? undefined); wind.update(conditions.wind, 30, camera);
    const rainGeometry = (rain as unknown as { geometry: T.InstancedBufferGeometry }).geometry;
    const seedsA = [...(rainGeometry.getAttribute('dropSeed').array as Float32Array)];
    const rainSeeds = rainGeometry.getAttribute('dropSeed') as T.InstancedBufferAttribute, windSeeds = wind.mesh.geometry.getAttribute('streakSeed') as T.InstancedBufferAttribute;
    const versions = [rainSeeds.version, windSeeds.version];
    rain.update(4, 90, camera, conditions.wind ?? undefined);
    const seedsB = [...(rainGeometry.getAttribute('dropSeed').array as Float32Array)];
    expect(seedsA.slice(0, 20)).toEqual(seedsB.slice(0, 20)); // slot seeds are time-independent
    rain.setQuality('low'); wind.setQuality('low');
    rain.update(10, 120, camera, conditions.wind ?? undefined); wind.update(conditions.wind, 120, camera);
    expect([rainSeeds.version, windSeeds.version]).toEqual(versions); // no repeated GPU seed uploads
    rain.dispose(); wind.dispose();
  });
  it('fog density matches the 5 % contrast contract and the hash is stable', () => {
    expect(fogDensity(500)).toBeCloseTo(Math.sqrt(-Math.log(0.05)) / 500, 12);
    expect(fxHash(1234, 7)).toBe(fxHash(1234, 7));
    expect(fxSeed('fire-a')).toBe(fxSeed('fire-a'));
    expect(fxHash(1, 1)).not.toBe(fxHash(1, 2));
  });
  it('keeps existing puffs at the end cut, fades flames and emits no person-fall smoke', async () => {
    const layer = new IncidentFX('high'); await layer.ready;
    const fire = { ...conditions.incidents[0], startS: 0, sinceS: 20, activeS: 20 };
    layer.update([fire], 20, camera, new Map());
    const meshes = layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh[];
    const atEnd = matrices(meshes[1]);
    layer.update([{ ...fire, phase: 'ended', sinceS: 0 }], 20, camera, new Map());
    expect(matrices(meshes[1])).toEqual(atEnd);
    expect(meshes[0].visible).toBe(true);
    expect(layer.root.children[0].children.find(node => node instanceof T.PointLight)?.visible).toBe(false);
    layer.update([{ ...fire, phase: 'ended', sinceS: 2, activeS: 22 }], 22, camera, new Map());
    expect((meshes[0].material as T.ShaderMaterial).uniforms.uRamp.value).toBe(0.5);
    layer.update([{ ...fire, phase: 'ended', sinceS: 30, activeS: 50 }], 50, camera, new Map());
    expect(meshes[0].visible).toBe(false); expect(meshes[1].count).toBe(0);
    layer.update([{ ...fire, kind: 'person_fall' }], 50, camera, new Map());
    expect(meshes[0].visible).toBe(false); expect(meshes[1].visible).toBe(false);
    layer.dispose();
  });
  it('combines visibility with art fog and preserves the exact clear day/dusk presets in any call order', () => {
    const scene = new T.Scene(), render = renderer(), rig = setupScene(render, scene), art = scene.fog;
    const clear = { dusk: false, rain: 0, visibilityM: null };
    rig.setAtmosphere({ dusk: false, rain: 0.5, visibilityM: 500 });
    expect(scene.fog).toBeInstanceOf(T.Fog);
    expect(scene.fog).toMatchObject({ near: 0, far: 500 / 0.95 });
    rig.setDusk(true);
    expect(rig.sun.intensity).toBeCloseTo(1.5 * (1 - 0.45 * 0.5));
    expect(render.toneMappingExposure).toBeCloseTo(1 - 0.15 * 0.5);
    expect(rig.fill.intensity).toBe(0.35);
    rig.setAtmosphere({ dusk: true, rain: 0.5, visibilityM: 500 });
    expect(rig.sun.intensity).toBeCloseTo(1.5 * (1 - 0.45 * 0.5)); // repeated call never compounds
    rig.setAtmosphere({ ...clear, dusk: true });
    expect(scene.fog).toBe(art);
    expect(scene.fog).toMatchObject({ near: 850, far: 4200 });
    expect(rig.sun.intensity).toBe(1.5); expect(render.toneMappingExposure).toBe(1);
    expect(rig.sun.color.getHexString()).toBe('ffba79'); expect(scene.environmentIntensity).toBe(0.22);
    rig.setAtmosphere({ ...clear, visibilityM: 5000 });
    expect(scene.fog).toBe(art);
    rig.setAtmosphere(clear);
    expect(scene.fog).toBe(art); expect(art!.color.equals(rig.horizon)).toBe(true);
    expect(scene.fog).toMatchObject({ near: 500, far: 2000 });
    expect(rig.sun.intensity).toBe(3.1); expect(rig.fill.intensity).toBe(1.45); expect(render.toneMappingExposure).toBe(1.12);
    expect(rig.daySky.material.uniforms.horizon.value.equals(rig.horizon)).toBe(true);
    rig.dispose();
  });
  it('restores exact dry materials at high and low quality', () => {
    const material = new T.MeshStandardMaterial({ color: '#d3c7b3', roughness: 0.96 });
    const city = new T.Mesh(new T.BoxGeometry(), material), color = material.color.clone();
    applyWeatherMaterials(city, 1, false); applyWeatherMaterials(city, 0.5, false); applyWeatherMaterials(city, 0, false);
    expect(material.color.toArray()).toEqual(color.toArray()); expect(material.roughness).toBe(0.96);
    const low = new T.MeshLambertMaterial({ color: '#d3c7b3' }), lowCity = new T.Mesh(new T.BoxGeometry(), low), lowColor = low.color.clone();
    applyWeatherMaterials(lowCity, 1, false); expect(low.color.r).toBeLessThan(lowColor.r);
    applyWeatherMaterials(lowCity, 0, false); expect(low.color.toArray()).toEqual(lowColor.toArray());
  });
  it('preserves finite wet colours and exact dry colours when quality drops after rain starts', () => {
    const material = new T.MeshStandardMaterial({ color: '#34383d', roughness: 0.92 });
    const city = new T.Mesh(new T.PlaneGeometry(), material), dry = material.color.clone();
    const quality = new QualityMaterials();
    applyWeatherMaterials(city, 0.5, true);
    quality.apply(city, true); // real low-quality path JSON-clones the wet userData
    applyWeatherMaterials(city, 0.5, true);
    const cheap = city.material as unknown as T.MeshLambertMaterial;
    expect(cheap.color.toArray().every(Number.isFinite)).toBe(true);
    expect(cheap.color.toArray()).toEqual(dry.clone().multiplyScalar(0.96).toArray());
    // Ordinary material cloning must also carry a reversible, precise baseline.
    const clone = cheap.clone(), copied = new T.Mesh(city.geometry, clone);
    applyWeatherMaterials(copied, 0, false);
    expect(clone.color.toArray()).toEqual(dry.toArray());
    applyWeatherMaterials(city, 0, false);
    quality.apply(city, false); applyWeatherMaterials(city, 0, false);
    expect(city.material).toBe(material);
    expect(material.color.toArray()).toEqual(dry.toArray());
    expect(material.roughness).toBe(0.92);
    quality.dispose(); clone.dispose(); city.geometry.dispose(); material.dispose();
  });
  it('shades smoke with the actual display lights, including an override of journaled dusk', async () => {
    const scene = new T.Scene(), render = renderer(), rig = setupScene(render, scene);
    const layer = new AtmosphereLayer(scene, render, 'low'); await layer.ready;
    const dusk = { ...conditions, light: 'dusk' as const };
    rig.setAtmosphere({ dusk: false, rain: 0.5, visibilityM: 500 });
    layer.update(dusk, 12.5, camera);
    const smoke = meshes(layer.root.children[0].children[0])[1];
    expect((smoke.material.uniforms.uAmbient ?? { value: null }).value).toBeUndefined(); // uAmbient is gone
    const dayLight = smoke.material.uniforms.uSmokeLight.value.clone() as T.Color;
    expect(dayLight.getHex()).toBe(new T.Color('#8f8a84').lerp(new T.Color('#7d8288'), 0.15).getHex());
    expect(smoke.material.uniforms.uScatter.value).toBe(0.25);
    expect(smoke.material.uniforms.uGlowStrength.value).toBeGreaterThan(0); // flames are burning
    rig.setAtmosphere({ dusk: true, rain: 0.5, visibilityM: 500 });
    layer.update(dusk, 12.5, camera);
    const light = smoke.material.uniforms.uSmokeLight.value as T.Color;
    expect(light.r).toBeLessThan(dayLight.r); expect(light.g).toBeLessThan(dayLight.g); expect(light.b).toBeLessThan(dayLight.b);
    expect(smoke.material.uniforms.uScatter.value).toBe(0.55);
    // The display override, rather than the unchanged journaled Conditions.light, drives shading.
    rig.setAtmosphere({ dusk: false, rain: 0.5, visibilityM: 500 });
    layer.update(dusk, 12.5, camera);
    expect(smoke.material.uniforms.uSmokeLight.value.toArray()).toEqual(dayLight.toArray());
    expect(smoke.material.uniforms.uScatter.value).toBe(0.25);
    layer.dispose(); rig.dispose();
  });
  it('fades the fire underlight with the flame ramp and keeps a constant glow colour', async () => {
    const layer = new IncidentFX('med'); await layer.ready;
    const fire = { ...conditions.incidents[0], startS: 0 };
    layer.update([{ ...fire, activeS: 3, sinceS: 3 }], 3, camera, new Map()); // ramp 1
    const smoke = meshes(layer.root.children[0])[1].material as T.ShaderMaterial;
    layer.setSmokeLight(false, 0); // day, after update so this frame's ramp applies
    const burning = smoke.uniforms.uGlowStrength.value as number;
    expect(burning).toBeCloseTo(0.25);
    expect((smoke.uniforms.uGlow.value as T.Color).getHex()).toBe(new T.Color('#ff8a2b').getHex());
    const flameTop = smoke.uniforms.uFlameTop.value as number;
    expect(flameTop).toBeGreaterThan(0);
    layer.setSmokeLight(true, 0);
    expect(smoke.uniforms.uGlowStrength.value).toBeCloseTo(0.55);
    // Fire ended 4 s ago → ramp 0, no underlight.
    layer.update([{ ...fire, phase: 'ended', activeS: 12, sinceS: 4 }], 12, camera, new Map());
    layer.setSmokeLight(false, 0);
    expect(smoke.uniforms.uGlowStrength.value).toBe(0);
    expect(smoke.uniforms.uFlameTop.value).toBe(flameTop); // stays at the last flame height
    layer.dispose();
  });
  it.each(['high', 'low'] as const)('keeps a continuous %s plume across schedule wraps and late activation', async quality => {
    const layer = new IncidentFX(quality); await layer.ready;
    const fire = { ...conditions.incidents[0], startS: 90 };
    const rate = quality === 'high' ? 2.0 : 0.8, lifeS = 24;
    for (const age of [1, 20, 26, 39.9, 40, 53.4, 80, 120]) {
      layer.update([{ ...fire, activeS: age, sinceS: age }], 90 + age, camera, new Map());
      const smoke = meshes(layer.root.children[0])[1];
      // Reference emission times, independent of the circular slot implementation.
      const alive = Array.from({ length: Math.floor(age * rate) + 1 }, (_, i) => age - i / rate).filter(value => value >= 0 && value < lifeS).length;
      expect(smoke.count).toBe(alive);
      expect(matrices(smoke).every(Number.isFinite)).toBe(true);
      const cells = smoke.geometry.getAttribute('puffCell').array as Float32Array;
      expect([...cells.slice(0, smoke.count)].every(cell => Number.isInteger(cell) && cell >= 0 && cell < 4)).toBe(true);
      expect(new Set(cells.slice(0, smoke.count)).size).toBeGreaterThan(age < 2 ? 0 : 1);
    }
    layer.update([{ ...fire, phase: 'ended', activeS: 121, sinceS: 1 }], 211, camera, new Map());
    expect(meshes(layer.root.children[0])[1].count).toBeGreaterThan(0);
    layer.update([{ ...fire, phase: 'ended', activeS: 146, sinceS: 26 }], 236, camera, new Map());
    // 26 s after ending, every puff from the 24 s lifetime has expired.
    expect(meshes(layer.root.children[0])[1].count).toBe(0);
    layer.dispose();
  });
  it('keeps per-fire ramps independent and caps flame size and cluster radius', async () => {
    const layer = new IncidentFX('high'); await layer.ready;
    layer.update([{ ...conditions.incidents[0], radiusM: 500 }, { ...conditions.incidents[0], id: 'b', phase: 'ended', sinceS: 2, activeS: 22 }], 22, camera, new Map());
    const flames = meshes(layer.root.children[0])[0], endedFlames = meshes(layer.root.children[1])[0];
    expect(flames.material.uniforms.uRamp.value).toBe(1);
    expect(endedFlames.material.uniforms.uRamp.value).toBe(0.5);
    const matrix = new T.Matrix4(), position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
    for (let i = 0; i < flames.count; i++) {
      flames.getMatrixAt(i, matrix); matrix.decompose(position, quaternion, scale);
      expect(Math.hypot(position.x, position.z)).toBeLessThanOrEqual(6);
      expect(scale.y).toBeGreaterThanOrEqual(5); expect(scale.y).toBeLessThanOrEqual(12.000001);
      expect(scale.x / scale.y).toBeCloseTo(0.8);
    }
    layer.dispose();
  });
  it('skips pending run resources, shows draft phase labels, and uses the entity coordinate transform after rebasing', async () => {
    const layer = new IncidentFX('med'); await layer.ready;
    const pending = { ...conditions.incidents[0], phase: 'pending' as const, activeS: 0, startS: 90 };
    layer.update([pending], 0, camera, new Map()); expect(layer.root.children).toHaveLength(0);
    const overlay = document.createElement('div'), preview = new IncidentFX('med', { annotations: true, preview: { overlay, locale: () => 'en-US' } }); await preview.ready;
    const origin = new T.Vector3(2000, 10, -2000), roof = vi.fn(() => 25);
    preview.setPinScale(1800, 2); preview.update([pending], 0, camera, new Map(), origin, roof);
    // The unit sits at ground level; the roof height feeds flames/glow/smoke, not the decal unit.
    expect(preview.root.children[0].position.toArray()).toEqual(worldPosition(pending.position, 'enu').sub(origin).toArray());
    expect(roof).toHaveBeenCalledWith(20, 40);
    expect(overlay.textContent).toBe('Fire at 01:30');
    const pin = preview.root.children[0].children.find(node => node instanceof T.Sprite) as T.Sprite;
    expect(pin.scale.x * camera.projectionMatrix.elements[5] * 1800 / 2).toBeCloseTo(26); expect(pin.position.y).toBe(49);
    expect(pin.material.fog).toBe(false);
    expect(meshes(preview.root.children[0]).every(mesh => !mesh.visible)).toBe(true);
    preview.update(conditions.incidents, 12.5, camera, new Map());
    expect(overlay.textContent).toBe('Building fire · Active 00:05');
    const ring = preview.root.children[0].children.find(node => node instanceof T.Mesh && !(node instanceof T.InstancedMesh) && (node as T.Mesh).material instanceof T.ShaderMaterial && (node as T.Mesh<T.BufferGeometry, T.ShaderMaterial>).material.uniforms.uReveal) as T.Mesh<T.BufferGeometry, T.ShaderMaterial>;
    vi.spyOn(performance, 'now').mockReturnValue(300);
    preview.update(conditions.incidents, 12.5, camera, new Map([['fire-a', 0]]));
    expect(ring.material.uniforms.uReveal.value).toBe(0.5); expect(ring.rotation.y).toBe(0);
    preview.dispose(); layer.dispose(); expect(overlay.children).toHaveLength(0);
  });
  it('keeps sensor flames/smoke fogged but allocates no annotation meshes or wind streaks', async () => {
    const scene = new T.Scene(), render = renderer(), rig = setupScene(render, scene);
    const layer = new AtmosphereLayer(scene, render, 'low', { annotations: false }); await layer.ready;
    rig.setAtmosphere({ dusk: true, rain: 0.5, visibilityM: 500 }); layer.update(conditions, 12.5, camera);
    expect(layer.root.children).toHaveLength(2); // incident group + rain; no wind volume
    const unit = layer.root.children[0].children[0];
    expect(unit.children.some(node => node instanceof T.Sprite || node instanceof T.Line)).toBe(false);
    expect(unit.children.filter(node => node instanceof T.InstancedMesh)).toEqual(meshes(unit));
    for (const mesh of meshes(unit)) {
      expect(mesh.visible).toBe(true); expect(mesh.material.fog).toBe(true);
      expect(mesh.material.uniforms).toHaveProperty('fogDensity');
    }
    expect(scene.fog).toBeInstanceOf(T.Fog);
    expect(scene.fog).toMatchObject({ near: 0, far: 500 / 0.95 });
    layer.dispose(); rig.dispose();
  });
  it('applies draft fog and dusk to existing lights and restores the original clear scene exactly', async () => {
    const scene = new T.Scene(), render = renderer(), background = new T.Color('#edf2f6'), fog = new T.Fog('#aaaabb', 1000, 3000);
    scene.background = background; scene.fog = fog;
    const sun = new T.DirectionalLight('#fff3e1', 2.5), fill = new T.HemisphereLight('#ddddff', '#65705b', 2); scene.add(sun, fill);
    const sunColor = sun.color.clone(), fillColor = fill.color.clone();
    const layer = new AtmosphereLayer(scene, render, 'med'); await layer.ready;
    layer.update({ ...conditions, light: 'dusk', precipitationMmH: 0 }, 12.5, camera);
    expect(scene.fog).toBeInstanceOf(T.Fog); expect(scene.fog).toMatchObject({ near: 0, far: 500 / 0.95 });
    expect(sun.intensity).toBe(1.25); expect(fill.intensity).toBe(1); expect(sun.color.equals(sunColor)).toBe(false);
    layer.update({ ...conditions, precipitationMmH: 0, visibilityM: null }, 12.5, camera);
    expect(scene.background).toBe(background); expect(scene.fog).toBe(fog);
    expect(sun.intensity).toBe(2.5); expect(fill.intensity).toBe(2);
    expect(sun.color.toArray()).toEqual(sunColor.toArray()); expect(fill.color.toArray()).toEqual(fillColor.toArray());
    layer.dispose();
  });
  it.each(['rig', 'preview'] as const)('updates %s observer fog only beyond 5 m and retains denser art fog', async mode => {
    const scene = new T.Scene(), render = renderer(), camera = new T.PerspectiveCamera(), target = new T.Vector3();
    const rig = mode === 'rig' ? setupScene(render, scene) : undefined;
    if (!rig) scene.fog = new T.Fog('#aaaabb', 850, 4200);
    const art = scene.fog;
    const layer = rig ? undefined : new AtmosphereLayer(scene, render, 'med');
    await layer?.ready;
    const update = (visibilityM: number | null) => {
      if (rig) rig.setAtmosphere({ dusk: true, rain: 0.5, visibilityM, observerOffsetM: observerFogOffset(camera, target) });
      else layer!.update({ ...conditions, light: 'dusk', visibilityM }, 12.5, camera, undefined, undefined, undefined, target);
    };
    camera.position.set(0, 0, 1000); update(800);
    const weather = scene.fog;
    expect(weather).toBeInstanceOf(T.Fog); expect(weather).not.toBe(art);
    expect(weather).toMatchObject({ near: 850, far: 850 + 800 / 0.95 });
    camera.position.z += 5; update(800);
    expect(scene.fog).toBe(weather); expect(weather).toMatchObject({ near: 850 });
    camera.position.z += 1; update(800);
    expect(scene.fog).toBe(weather); expect(weather).toMatchObject({ near: 856, far: 856 + 800 / 0.95 });
    camera.position.set(0, 0, 100); update(800);
    expect(weather).toMatchObject({ near: 0, far: 800 / 0.95 });
    update(5000); expect(scene.fog).toBe(art);
    update(null); expect(scene.fog).toBe(art);
    layer?.dispose(); rig?.dispose();
  });
  it.each([[900, 2, 45, 1], [800, 1, 60, 1], [500, 2, 35, 1.5]])('keeps the pin at 26 CSS px with height %s, DPR %s, FOV %s and zoom %s', async (height, dpr, fov, zoom) => {
    const scene = new T.Scene(), render = renderer();
    render.domElement = { clientHeight: height, height: height * dpr } as HTMLCanvasElement;
    render.getPixelRatio = () => dpr;
    const camera = new T.PerspectiveCamera(fov, 1.6); camera.zoom = zoom; camera.updateProjectionMatrix();
    const layer = new AtmosphereLayer(scene, render, 'med'); await layer.ready;
    const cssSize = () => {
      layer.update(conditions, 12.5, camera);
      const pin = layer.root.children[0].children[0].children.find(node => node instanceof T.Sprite) as T.Sprite;
      // Project the same camera-facing corners as Three's non-attenuating sprite shader.
      const half = pin.scale.y * 100 / 2;
      const top = new T.Vector3(0, half, -100).project(camera), bottom = new T.Vector3(0, -half, -100).project(camera);
      return (top.y - bottom.y) * height / 2;
    };
    expect(cssSize()).toBeCloseTo(26);
    camera.setViewOffset(1440, height, 240, 0, 900, height * 0.8);
    expect(cssSize()).toBeCloseTo(26);
    layer.dispose();
  });
});
