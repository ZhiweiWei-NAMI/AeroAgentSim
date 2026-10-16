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

/** The smoke InstancedMesh is the second InstancedMesh in the unit ([flames + cores, smoke]). */
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

it('covers at least 25 m of height from the low-tier plume after 30 s of fire in a 6 m/s wind', async () => {
  const layer = new IncidentFX('low');
  await layer.ready;
  layer.setWind(6, 0);
  layer.update([fire(30)], 30, camera, new Map());
  const smoke = smokeOf(layer);
  expect(smoke.count).toBe(18); // 18 puffs, 1.5/s over a 12 s life: the plume never gaps
  const matrix = new T.Matrix4(), position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
  let top = -Infinity, bottom = Infinity;
  for (let i = 0; i < smoke.count; i++) {
    smoke.getMatrixAt(i, matrix); matrix.decompose(position, quaternion, scale);
    top = Math.max(top, position.y + scale.y / 2);
    bottom = Math.min(bottom, position.y - scale.y / 2);
  }
  expect(top - bottom).toBeGreaterThanOrEqual(25);
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
    expect(scale.x + 1e-6).toBeGreaterThanOrEqual(5); expect(scale.x).toBeLessThanOrEqual(44.001);
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
  const decal = glow.material as T.ShaderMaterial;
  expect(decal.depthTest).toBe(false); // visible/occluded depths are compared in the shared shader
  expect(decal.uniforms.uRoof.value).toBeCloseTo(25.1);
  expect(decal.uniforms.uGround.value).toBeCloseTo(0.15);
  expect(decal.fragmentShader).toContain('texture2D(uDepth');
  const rings = unit.children.filter(node => (node as T.Mesh).isMesh === true && !(node instanceof T.InstancedMesh))
    .map(node => (node as T.Mesh).material as T.ShaderMaterial);
  expect(rings).toHaveLength(1);
  expect(rings[0].uniforms.uOccluded.value).toBe(0.22);
  expect(rings.some(material => material.uniforms.uOpacity?.value >= 0.85)).toBe(true);
  const smoke = smokeOf(layer); smoke.getMatrixAt(0, matrix); matrix.decompose(position, quaternion, scale);
  expect(position.y).toBeGreaterThan(25 + 5); // emission starts above the flames, not in their base
  const pin = unit.children.find(node => node instanceof T.Sprite) as T.Sprite;
  expect(pin.position.y).toBe(39);
  layer.dispose();
});

it('keeps the additive core small and ordered after the bodies in one draw', async () => {
  const layer = new IncidentFX('high'); await layer.ready;
  layer.update([fire(20)], 20, camera, new Map());
  const [body] = layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh<T.BufferGeometry, T.ShaderMaterial>[];
  const count = body.material.uniforms.uFlameCount.value;
  expect(body.count).toBe(count * 2);
  expect(body.material.premultipliedAlpha).toBe(true);
  expect(body.material.fragmentShader).toContain('if(flameCore>0.5)gl_FragColor.a=0.0;');
  const a = new T.Matrix4(), b = new T.Matrix4(), bodySize = new T.Vector3(), coreSize = new T.Vector3(), base = new T.Vector3(), glowBase = new T.Vector3(), quaternion = new T.Quaternion();
  for (let i = 0; i < body.material.uniforms.uFlameCount.value; i++) {
    body.getMatrixAt(i, a); body.getMatrixAt(i + count, b);
    a.decompose(base, quaternion, bodySize); b.decompose(glowBase, quaternion, coreSize);
    expect(coreSize.x / bodySize.x).toBeCloseTo(0.35);
    expect(coreSize.y / bodySize.y).toBeCloseTo(0.55);
    expect(glowBase.y - coreSize.y / 2).toBeCloseTo(base.y - bodySize.y / 2);
  }
  layer.dispose();
});

it.each([[70, 25], [220, 30], [775, 40], [420, 55]])('keeps a projected flame footprint and an attached plume root at %s m / %s°', async (distance, pitchDeg) => {
  const layer = new IncidentFX('low'); await layer.ready;
  // 34 s keeps the newest low-tier puff exactly at birth (34 = 51 / 1.5), so the root sits on the flames.
  const row = fire(34), view = new T.PerspectiveCamera(48, 1.6);
  const target = new T.Vector3(row.position[0], 20, -row.position[1]);
  const pitch = pitchDeg * Math.PI / 180;
  view.position.copy(target).add(new T.Vector3(0, distance * Math.sin(pitch), distance * Math.cos(pitch)));
  view.lookAt(target); view.updateMatrixWorld();
  layer.setPinScale(900);
  layer.update([row], 34, view, new Map());
  layer.root.updateMatrixWorld(true);
  const [body, smoke] = layer.root.children[0].children.filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh<T.BufferGeometry, T.ShaderMaterial>[];
  const matrix = new T.Matrix4(), position = new T.Vector3(), scale = new T.Vector3(), rotation = new T.Quaternion();
  for (let i = 0; i < body.material.uniforms.uFlameCount.value; i++) {
    body.getMatrixAt(i, matrix); matrix.decompose(position, rotation, scale);
    const top = new T.Vector3(0, 0.5, 0).applyMatrix4(matrix).applyMatrix4(body.matrixWorld).project(view);
    const bottom = new T.Vector3(0, -0.5, 0).applyMatrix4(matrix).applyMatrix4(body.matrixWorld).project(view);
    expect((top.y - bottom.y) * 900 / 2).toBeGreaterThan(30);
    if (distance === 70) expect(scale.y).toBeLessThanOrEqual(12.000001);
  }
  let root = Infinity;
  for (let i = 0; i < smoke.count; i++) {
    smoke.getMatrixAt(i, matrix); root = Math.min(root, matrix.elements[13]);
  }
  expect(smoke.count).toBe(18);
  smoke.onBeforeRender({} as T.WebGLRenderer, new T.Scene(), view, smoke.geometry, smoke.material, new T.Group());
  expect(root).toBeCloseTo(smoke.material.uniforms.uFlameTop.value, 5);
  layer.dispose();
});

it('drives the low tier from its own art values while the high tier keeps its curve', async () => {
  const high = new IncidentFX('high'), low = new IncidentFX('low');
  await Promise.all([high.ready, low.ready]);
  for (const layer of [high, low]) {
    layer.setWind(9 * Math.SQRT1_2, -9 * Math.SQRT1_2);
    layer.update([fire(9)], 9, camera, new Map());
  }
  const a = smokeOf(high), b = smokeOf(low);
  const highTint = a.geometry.getAttribute('puffTint'), lowTint = b.geometry.getAttribute('puffTint');
  const widthOf = (mesh: T.InstancedMesh, slot: number) => {
    const matrix = new T.Matrix4(), position = new T.Vector3(), quaternion = new T.Quaternion(), scale = new T.Vector3();
    mesh.getMatrixAt(slot, matrix); matrix.decompose(position, quaternion, scale); return scale.x;
  };
  // The low tier is slower and sparser: 1.5 puffs/s over a 12 s life keeps all 18 puffs alive.
  expect(a.count).toBe(19); expect(b.count).toBe(14);
  // At age 3 s the low puff has reached its 16 m mark; the high puff is still at 14 m.
  expect(widthOf(b, 9)).toBeCloseTo(16); expect(widthOf(a, 12)).toBeCloseTo(14);
  // Low holds a brighter 0.82 peak to 60 % of its 12 s life; the high body sits at 0.6.
  expect(lowTint.getW(9)).toBeCloseTo(0.82);
  expect(lowTint.getW(0)).toBeCloseTo(0.82 * (1 - (9 / 12 - 0.6) / (1 - 0.6)));
  expect(highTint.getW(0)).toBeCloseTo(0.6);
  high.dispose(); low.dispose();
});
