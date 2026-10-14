import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { IncidentFX } from './incident-fx';
import { Rain, rainCount } from './rain';
import { WindStreaks, streakCount } from './wind';
import { applyWeatherMaterials, fogDensity, setupScene } from '../scene';
import { fxHash, fxSeed } from './fx-shared';
import type { Conditions } from '../conditions';
import { AtmosphereLayer } from './index';
import { worldPosition } from '../coordinates';

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
    expect(scene.fog).toBeInstanceOf(T.FogExp2);
    expect((scene.fog as unknown as T.FogExp2).density).toBeCloseTo(fogDensity(500));
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
  it.each(['high', 'low'] as const)('keeps a continuous %s plume across schedule wraps and late activation', async quality => {
    const layer = new IncidentFX(quality); await layer.ready;
    const fire = { ...conditions.incidents[0], startS: 90 };
    for (const age of [1, 20, 26, 39.9, 40, 53.4, 80, 120]) {
      layer.update([{ ...fire, activeS: age, sinceS: age }], 90 + age, camera, new Map());
      const smoke = meshes(layer.root.children[0])[1];
      // Reference emission times, independent of the circular slot implementation.
      const alive = Array.from({ length: Math.floor(age * 1.2) + 1 }, (_, i) => age - i / 1.2).filter(value => value >= 0 && value < 26).length;
      expect(smoke.count).toBe(alive);
      expect(matrices(smoke).every(Number.isFinite)).toBe(true);
      const cells = smoke.geometry.getAttribute('puffCell').array as Float32Array;
      expect([...cells.slice(0, smoke.count)].every(cell => Number.isInteger(cell) && cell >= 0 && cell < 4)).toBe(true);
      expect(new Set(cells.slice(0, smoke.count)).size).toBeGreaterThan(age < 2 ? 0 : 1);
    }
    layer.update([{ ...fire, phase: 'ended', activeS: 121, sinceS: 1 }], 211, camera, new Map());
    expect(meshes(layer.root.children[0])[1].count).toBeGreaterThan(0);
    layer.update([{ ...fire, phase: 'ended', activeS: 146, sinceS: 26 }], 236, camera, new Map());
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
      expect(Math.hypot(position.x, position.z)).toBeLessThanOrEqual(8);
      expect(scale.y).toBeGreaterThanOrEqual(3); expect(scale.y).toBeLessThanOrEqual(9.000001);
      expect(scale.x / scale.y).toBeCloseTo(0.55);
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
    expect(preview.root.children[0].position.toArray()).toEqual(worldPosition(pending.position, 'enu').sub(origin).setY(25).toArray());
    expect(roof).toHaveBeenCalledWith(20, 40);
    expect(overlay.textContent).toBe('Fire at 01:30');
    const pin = preview.root.children[0].children.find(node => node instanceof T.Sprite) as T.Sprite;
    expect(pin.scale.x).toBeCloseTo(22 / 1800 * 2); expect(pin.position.y).toBe(14);
    expect(pin.material.fog).toBe(false);
    expect(meshes(preview.root.children[0]).every(mesh => !mesh.visible)).toBe(true);
    preview.update(conditions.incidents, 12.5, camera, new Map());
    expect(overlay.textContent).toBe('Building fire · Active 00:05');
    const ring = preview.root.children[0].children.find(node => node instanceof T.Mesh && !(node instanceof T.InstancedMesh)) as T.Mesh<T.BufferGeometry, T.ShaderMaterial>;
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
    expect(unit.children.filter(node => node instanceof T.Mesh)).toEqual(meshes(unit));
    for (const mesh of meshes(unit)) {
      expect(mesh.visible).toBe(true); expect(mesh.material.fog).toBe(true);
      expect(mesh.material.uniforms).toHaveProperty('fogDensity');
    }
    expect(scene.fog).toBeInstanceOf(T.FogExp2);
    layer.dispose(); rig.dispose();
  });
  it('applies draft fog and dusk to existing lights and restores the original clear scene exactly', async () => {
    const scene = new T.Scene(), render = renderer(), background = new T.Color('#edf2f6'), fog = new T.Fog('#aaaabb', 1000, 3000);
    scene.background = background; scene.fog = fog;
    const sun = new T.DirectionalLight('#fff3e1', 2.5), fill = new T.HemisphereLight('#ddddff', '#65705b', 2); scene.add(sun, fill);
    const sunColor = sun.color.clone(), fillColor = fill.color.clone();
    const layer = new AtmosphereLayer(scene, render, 'med'); await layer.ready;
    layer.update({ ...conditions, light: 'dusk', precipitationMmH: 0 }, 12.5, camera);
    expect(scene.fog).toBeInstanceOf(T.FogExp2); expect((scene.fog as unknown as T.FogExp2).density).toBeCloseTo(fogDensity(500));
    expect(sun.intensity).toBe(1.25); expect(fill.intensity).toBe(1); expect(sun.color.equals(sunColor)).toBe(false);
    layer.update({ ...conditions, precipitationMmH: 0, visibilityM: null }, 12.5, camera);
    expect(scene.background).toBe(background); expect(scene.fog).toBe(fog);
    expect(sun.intensity).toBe(2.5); expect(fill.intensity).toBe(2);
    expect(sun.color.toArray()).toEqual(sunColor.toArray()); expect(fill.color.toArray()).toEqual(fillColor.toArray());
    layer.dispose();
  });
});
