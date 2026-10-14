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
    expect(scale.x + 1e-6).toBeGreaterThanOrEqual(5); expect(scale.x).toBeLessThanOrEqual(34.001);
  }

  // From NW → drifts to the SE: +east (world +x) and −north (world +z).
  expect(maxEast).toBeGreaterThan(10);
  expect(maxSouth).toBeGreaterThan(10);
  layer.dispose();
});

it('keeps the ring on the ground while flames, glow, smoke and the pin use the roof', async () => {
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
  expect(glow.position.y).toBeCloseTo(25.1);
  const rings = unit.children.filter(node => (node as T.Mesh).isMesh === true && !(node instanceof T.InstancedMesh))
    .map(node => (node as T.Mesh).material as T.ShaderMaterial);
  expect(rings.some(material => material.depthFunc === T.GreaterDepth)).toBe(true);
  expect(rings.some(material => material.uniforms.uOpacity?.value >= 0.85)).toBe(true);
  const smoke = smokeOf(layer); smoke.getMatrixAt(0, matrix); matrix.decompose(position, quaternion, scale);
  expect(position.y).toBeGreaterThan(25 + 5); // emission starts above the flames, not in their base
  const pin = unit.children.find(node => node instanceof T.Sprite) as T.Sprite;
  expect(pin.position.y).toBe(39);
  layer.dispose();
});

it('keeps the additive core small and selects its pass independently of shared quad attributes', async () => {
  const layer = new IncidentFX('high'); await layer.ready;
  layer.update([fire(20)], 20, camera, new Map());
  const [body, , core] = layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh<T.BufferGeometry, T.ShaderMaterial>[];
  expect(body.material.uniforms.uCore.value).toBe(0);
  expect(core.material.uniforms.uCore.value).toBe(1);
  expect(body.material.premultipliedAlpha).toBe(true); expect(core.material.premultipliedAlpha).toBe(true);
  const a = new T.Matrix4(), b = new T.Matrix4(), bodySize = new T.Vector3(), coreSize = new T.Vector3(), base = new T.Vector3(), glowBase = new T.Vector3(), quaternion = new T.Quaternion();
  for (let i = 0; i < body.count; i++) {
    body.getMatrixAt(i, a); core.getMatrixAt(i, b);
    a.decompose(base, quaternion, bodySize); b.decompose(glowBase, quaternion, coreSize);
    expect(coreSize.x / bodySize.x).toBeCloseTo(0.35);
    expect(coreSize.y / bodySize.y).toBeCloseTo(0.55);
    expect(glowBase.y - coreSize.y / 2).toBeCloseTo(base.y - bodySize.y / 2);
  }
  layer.dispose();
});
