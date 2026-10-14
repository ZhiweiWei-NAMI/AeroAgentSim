import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as T from 'three';
import { IncidentFX } from './incident-fx';
import type { Conditions } from '../conditions';

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

const camera = new T.PerspectiveCamera(); camera.position.set(60, 40, -60); camera.updateMatrixWorld();
const fire = (activeS = 30): Conditions['incidents'][number] =>
  ({ id: 'fire-a', kind: 'fire', label: 'Fire', position: [20, 40, 0], radiusM: 30, phase: 'active', sinceS: activeS, activeS, startS: 0 });

/** The smoke InstancedMesh is the second InstancedMesh in the unit ([flames, smoke, core]). */
const smokeOf = (layer: IncidentFX) => layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh)[1] as T.InstancedMesh;

it.each(['high', 'low'] as const)('keeps a live plume after 30 s of fire at %s quality', async quality => {
  const onReady = vi.fn();
  const layer = new IncidentFX(quality, { annotations: true, onReady });
  await layer.ready;
  expect(onReady).toHaveBeenCalled();
  layer.update([fire(30)], 30, camera, new Map());
  const smoke = smokeOf(layer);
  expect(smoke.count).toBeGreaterThanOrEqual(quality === 'high' ? 20 : 10);
  expect((smoke.geometry as T.InstancedBufferGeometry).instanceCount).toBe(smoke.count);
  expect(smoke.material).toBe(layer['smoke']); // atlas material assigned, not the hidden placeholder
  layer.dispose();
});

it('the plume leans with the authored wind: from NW means it drifts to the SE (+east, −north)', async () => {
  const layer = new IncidentFX('high');
  await layer.ready;
  layer.setWind(6, 0); // wind blowing towards +east = from the west… from NW → east+north components
  layer.setWind(6 * Math.SQRT1_2, -6 * Math.SQRT1_2); // from NW blows towards +east, −north
  layer.update([fire(30)], 30, camera, new Map());
  const smoke = smokeOf(layer), matrix = new T.Matrix4(), position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
  let maxEast = 0, maxSouth = 0;
  for (let i = 0; i < smoke.count; i++) {
    smoke.getMatrixAt(i, matrix); matrix.decompose(position, quaternion, scale);
    maxEast = Math.max(maxEast, position.x);
    maxSouth = Math.max(maxSouth, position.z); // ENU south maps to +z
    expect(scale.x + 1e-6).toBeGreaterThanOrEqual(8); expect(scale.x).toBeLessThanOrEqual(40.001);
  }

  // From NW → drifts to the SE: +east (world +x) and −north (world +z).
  expect(maxEast).toBeGreaterThan(10);
  expect(maxSouth).toBeGreaterThan(10);
  layer.dispose();
});

it('rings, fills and glow sit at ground level; only flames and smoke ride the roof height', async () => {
  const layer = new IncidentFX('high', { annotations: true });
  await layer.ready;
  const roof = vi.fn(() => 25);
  layer.update([fire(30)], 30, camera, new Map(), undefined, roof);
  const unit = layer.root.children[0];
  expect(unit.position.toArray()).toEqual([20, 0, -40]); // ENU (20,40,0) → world (20, 0, −40); ground decal at ground
  const matrix = new T.Matrix4(), position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
  const flamesMesh = unit.children.filter(node => node instanceof T.InstancedMesh)[0] as T.InstancedMesh;
  if (flamesMesh.count === 0) throw new Error('flames not written');
  flamesMesh.getMatrixAt(0, matrix); matrix.decompose(position, quaternion, scale);
  if (!Number.isFinite(position.y)) throw new Error(`flame matrix at 0 is degenerate: ${[...matrix.elements]}`);
  expect(position.y).toBeGreaterThan(20); // flame base above the roof
  const glow = unit.children.find(node => node instanceof T.Mesh && !(node instanceof T.InstancedMesh)) as T.Mesh;
  expect(glow).toBeTruthy();
  expect((glow.material as T.Material).depthTest).toBe(true);
  const rings = unit.children.filter(node => (node as T.Mesh).isMesh === true && !(node instanceof T.InstancedMesh))
    .map(node => (node as T.Mesh).material as T.ShaderMaterial);
  expect(rings.some(material => material.depthFunc === T.GreaterDepth)).toBe(true);
  expect(rings.some(material => material.uniforms.uOpacity?.value >= 0.85)).toBe(true);
  layer.dispose();
});
