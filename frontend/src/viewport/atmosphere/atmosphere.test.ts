import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { IncidentFX } from './incident-fx';
import { Rain, rainCount } from './rain';
import { WindStreaks, streakCount } from './wind';
import { applySkyWeather, applyWeatherMaterials, fogDensity, type SkyTarget } from './sky-weather';
import { fxHash, fxSeed } from './fx-shared';
import type { Conditions } from '../conditions';

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
  incidents: [{ id: 'fire-a', kind: 'fire', label: 'Building fire', position: [20, 40, 0], radiusM: 30, phase: 'active', sinceS: 5 }],
};
const camera = new T.PerspectiveCamera(); camera.position.set(60, 40, -60); camera.updateMatrixWorld();

/** Instance matrices (smoke/flames) or slot seeds — identical at the same (conditions, t). */
function matrices(mesh: T.InstancedMesh): number[] {
  return [...mesh.instanceMatrix.array as Float32Array].map(value => Math.fround(value));
}

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
    rain.update(4, 90, camera, conditions.wind ?? undefined);
    const seedsB = [...(rainGeometry.getAttribute('dropSeed').array as Float32Array)];
    expect(seedsA.slice(0, 20)).toEqual(seedsB.slice(0, 20)); // slot seeds are time-independent
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
    const fire = { ...conditions.incidents[0], startS: 0, sinceS: 20 };
    layer.update([fire], 20, camera, new Map());
    const meshes = layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh[];
    const atEnd = matrices(meshes[1]);
    layer.update([{ ...fire, phase: 'ended', sinceS: 0 }], 20, camera, new Map());
    expect(matrices(meshes[1])).toEqual(atEnd);
    expect(meshes[0].visible).toBe(true);
    expect(layer.root.children[0].children.find(node => node instanceof T.PointLight)?.visible).toBe(false);
    layer.update([{ ...fire, phase: 'ended', sinceS: 30 }], 50, camera, new Map());
    expect(meshes[0].visible).toBe(false); expect(meshes[1].count).toBe(0);
    layer.update([{ ...fire, kind: 'person_fall' }], 50, camera, new Map());
    expect(meshes[0].visible).toBe(false); expect(meshes[1].visible).toBe(false);
    layer.dispose();
  });
  it('combines visibility with art fog and restores exact dry materials', () => {
    const scene = new T.Scene(), horizon = new T.Color('#72b1e8');
    const art = new T.Fog(horizon, 500, 2000); scene.fog = art;
    const target: SkyTarget = { scene, horizon, sun: new T.DirectionalLight('#fff1dc', 3.1), fill: new T.HemisphereLight('#dceeff', '#b8b4a3', 1.45), renderer: { toneMappingExposure: 1.12 } as T.WebGLRenderer };
    applySkyWeather(target, conditions, false);
    expect(scene.fog).toBeInstanceOf(T.FogExp2);
    expect((scene.fog as unknown as T.FogExp2).density).toBeCloseTo(fogDensity(500));
    applySkyWeather(target, { ...conditions, visibilityM: 5000 }, false);
    expect(scene.fog).toBe(art);
    applySkyWeather(target, { ...conditions, precipitationMmH: 0, visibilityM: null }, false);
    expect(scene.fog).toBe(art); expect(art.color.equals(horizon)).toBe(true);
    const material = new T.MeshStandardMaterial({ color: '#d3c7b3', roughness: 0.96 });
    const city = new T.Mesh(new T.BoxGeometry(), material), color = material.color.clone();
    applyWeatherMaterials(city, 1, false); applyWeatherMaterials(city, 0.5, false); applyWeatherMaterials(city, 0, false);
    expect(material.color.toArray()).toEqual(color.toArray()); expect(material.roughness).toBe(0.96);
    const low = new T.MeshLambertMaterial({ color: '#d3c7b3' }), lowCity = new T.Mesh(new T.BoxGeometry(), low), lowColor = low.color.clone();
    applyWeatherMaterials(lowCity, 1, false); expect(low.color.r).toBeLessThan(lowColor.r);
    applyWeatherMaterials(lowCity, 0, false); expect(low.color.toArray()).toEqual(lowColor.toArray());
  });
});
