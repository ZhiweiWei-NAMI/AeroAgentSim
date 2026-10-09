import { afterEach, describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { disposeObject } from './assets';
import { buildLiteCity, isLiteCityScene, proceduralCar, proceduralModel, proceduralUav, PROCEDURAL_ASSET_SCHEME } from './procedural-traffic';
import { loadTrafficCity } from '../scene/traffic-city';

afterEach(() => { vi.unstubAllGlobals(); });

const bundle = {
  format: 'traffic-city-lite/v1' as const, frame: 'enu' as const,
  buildings: [
    { id: 'building.way.1.component.0', footprint: [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]] as const, height_m: 24 },
    { id: 'building.way.2.component.0', footprint: [[20, 0], [30, 0], [30, 8], [20, 8], [20, 0]] as const, height_m: 6 },
  ] as const,
  roads: [
    { id: 'incident-lane', points_enu_m: [[0, 0, 0], [40, 0, 0]] as const, width_m: 6.4 },
    { id: 'route-a', points_enu_m: [[0, 20, 0], [20, 20, 0], [20, 40, 0]] as const, width_m: 3.2 },
  ] as const,
};

describe('procedural lite traffic city', () => {
  it('accepts the traffic-city-lite/v1 contract and rejects other shapes', () => {
    expect(isLiteCityScene(bundle)).toBe(true);
    expect(isLiteCityScene({ format: 'traffic-city-lite/v1', frame: 'enu', buildings: [] })).toBe(true);
    expect(isLiteCityScene({ format: 'traffic-city-lite/v1', frame: 'render-world', buildings: bundle.buildings })).toBe(false);
    expect(isLiteCityScene({ ...bundle, buildings: [{ ...bundle.buildings[0], height_m: 0 }] })).toBe(false);
    expect(isLiteCityScene({ ...bundle, buildings: [{ ...bundle.buildings[0], footprint: [[0, 0], [1, 1]] }] })).toBe(false);
    expect(isLiteCityScene({ ...bundle, roads: [{ ...bundle.roads[0], width_m: -1 }] })).toBe(false);
    // Original GLB scenes carry building urls and must never take the lite path.
    expect(isLiteCityScene({ buildings: [{ url: '/assets/buildings/a.glb', x: 0, y: 0, z: 0, height: 12 }], roads: {} })).toBe(false);
  });
  it('renders recorded footprints and heights without GLB loads', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const group = new T.Group();
    buildLiteCity(bundle, group);
    const buildings = group.children.find(child => child.name === 'lite-buildings') as T.Mesh;
    expect(buildings).toBeInstanceOf(T.Mesh);
    const position = buildings.geometry.getAttribute('position');
    // Extrusion spans exactly the recorded heights: 24 m and 6 m above ground.
    let maxBuilding1 = -Infinity, maxBuilding2 = -Infinity;
    for (let i = 0; i < position.count; i++) {
      const x = position.getX(i), y = position.getY(i);
      if (x <= 10) maxBuilding1 = Math.max(maxBuilding1, y); else maxBuilding2 = Math.max(maxBuilding2, y);
    }
    expect(maxBuilding1).toBeCloseTo(24, 6);
    expect(maxBuilding2).toBeCloseTo(6, 6);
    // Footprint corners stay at recorded ENU positions in render x/z (z = -north).
    let minZ = Infinity, maxZ = -Infinity;
    for (let i = 0; i < position.count; i++) { minZ = Math.min(minZ, position.getZ(i)); maxZ = Math.max(maxZ, position.getZ(i)); }
    expect(minZ).toBeCloseTo(-8, 6);
    expect(maxZ).toBeCloseTo(0, 6);
    // One merged draw call with per-building material ranges.
    expect(buildings.geometry.getAttribute('displayFacadeStyle')).toBeDefined();
    const buildingMaterial = buildings.material as T.MeshStandardMaterial;
    expect(buildingMaterial.userData.displayEstimate).toContain('facade windows');
    const roads = group.children.find(child => child.name === 'lite-roads') as T.Mesh;
    expect(roads).toBeInstanceOf(T.Mesh);
    const roadPositions = roads.geometry.getAttribute('position'), index = roads.geometry.index!;
    const triangle = [0, 1, 2].map(i => new T.Vector3().fromBufferAttribute(roadPositions, index.getX(i)));
    expect(triangle[1].sub(triangle[0]).cross(triangle[2].sub(triangle[0])).y).toBeGreaterThan(0);
    expect((roads.material as T.MeshStandardMaterial).userData.displaySurface).toBe('asphalt');
    expect(group.userData).toMatchObject({ displayStyle: 'procedural-lite-city', buildingCount: 2, roadCount: 2 });
    expect(fetchSpy).not.toHaveBeenCalled();
    disposeObject(group);
  });
  it('rejects a lite bundle that fails the contract instead of inventing geometry', () => {
    expect(() => buildLiteCity({ ...bundle, format: 'other/v1' } as never, new T.Group())).toThrow('traffic-city-lite/v1');
  });
});

describe('procedural vehicle stand-ins', () => {
  it('matches the recorded camera primitives for car and uav without asset fetches', () => {
    const car = proceduralCar();
    expect(car.children).toHaveLength(1);
    const body = car.children[0] as T.Mesh;
    const size = new T.Vector3();
    new T.Box3().setFromObject(body).getSize(size);
    expect(size.x).toBeCloseTo(2.2); expect(size.y).toBeCloseTo(1.4); expect(size.z).toBeCloseTo(4);
    disposeObject(car);
    const uav = proceduralUav();
    const meshes: T.Mesh[] = [];
    uav.traverse(child => { if (child instanceof T.Mesh) meshes.push(child); });
    expect(meshes).toHaveLength(3); // body + two crossing rotor bars
    const bar = meshes[1] as T.Mesh;
    const barSize = new T.Vector3();
    new T.Box3().setFromObject(bar).getSize(barSize);
    expect(barSize.x).toBeCloseTo(4); expect(barSize.y).toBeCloseTo(0.16); expect(barSize.z).toBeCloseTo(0.2);
    disposeObject(uav);
  });
  it('resolves reserved procedural: assets locally and leaves real URLs to the loader', () => {
    expect(proceduralModel(`${PROCEDURAL_ASSET_SCHEME}car`)).toBeDefined();
    expect(proceduralModel(`${PROCEDURAL_ASSET_SCHEME}uav`)).toBeDefined();
    expect(proceduralModel(`${PROCEDURAL_ASSET_SCHEME}truck`)).toBeUndefined();
    expect(proceduralModel('/assets/buildings/a.glb')).toBeUndefined();
  });
});

describe('loadTrafficCity lite fallback', () => {
  it('renders the lite bundle without GLB loads and preserves the GLB path', async () => {
    const fetchSpy = vi.fn(async () => ({ ok: true, json: async () => bundle }));
    vi.stubGlobal('fetch', fetchSpy);
    const assets = { model: vi.fn() } as unknown as Parameters<typeof loadTrafficCity>[1];
    const group = await loadTrafficCity('https://console.example/v1/studio/demo-assets/scene.json', assets, new AbortController().signal);
    expect(assets.model).not.toHaveBeenCalled();
    expect(group.userData).toMatchObject({ displayStyle: 'procedural-lite-city' });
    expect(group.children.length).toBe(2);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    disposeObject(group);
  });
  it('keeps the original GLB scene loader exactly as before', async () => {
    const glbScene = { buildings: [{ url: '/assets/buildings/b.glb', x: 1, y: 2, z: 3, height: 12 }], roads: {} };
    const model = new T.Group();
    const fetchSpy = vi.fn(async () => ({ ok: true, json: async () => glbScene }));
    vi.stubGlobal('fetch', fetchSpy);
    const assets = { model: vi.fn(async () => model) } as unknown as Parameters<typeof loadTrafficCity>[1];
    const group = await loadTrafficCity('https://console.example/v1/studio/demo-assets/scene.json', assets, new AbortController().signal);
    expect(assets.model).toHaveBeenCalledOnce();
    expect(group.children[0]).toBe(model);
    disposeObject(group);
  });
});
