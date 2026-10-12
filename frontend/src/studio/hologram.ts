import * as THREE from 'three';
import type { PreviewOverlay } from './plan-overlay';

export type HologramMode = 'blueprint' | 'sweep' | 'real';
export const SWEEP_DURATION_MS = 1600;

export function sweepRadius(elapsedMs: number, durationMs: number, maxRadius: number): number {
  const progress = durationMs <= 0 ? 1 : Math.max(0, Math.min(1, elapsedMs / durationMs));
  return maxRadius * (1 - Math.pow(1 - progress, 3));
}

export function hologramMode(overlay: PreviewOverlay | undefined, playedKeys: ReadonlySet<string>): HologramMode {
  if (overlay?.tone === 'proposed') return 'blueprint';
  return overlay?.reveal && !playedKeys.has(overlay.reveal.key) ? 'sweep' : 'real';
}

export function hologramCenter(overlay: PreviewOverlay, sceneCenter: readonly [number, number]): [number, number] {
  const revealed = overlay.tone === 'configured' ? new Set(overlay.reveal?.order) : undefined;
  const markers = overlay.markers.filter(marker => !revealed || revealed.has(marker.id));
  if (!markers.length) return [...sceneCenter];
  return markers.reduce<[number, number]>((center, marker) => [center[0] + marker.position[0] / markers.length, center[1] - marker.position[1] / markers.length], [0, 0]);
}

type Surface = 'building' | 'ground' | 'road' | 'grid';
type RenderObject = THREE.Mesh | THREE.Line;
interface Patch { source: THREE.Material; material: THREE.Material; surface: Surface; users: number }
interface Binding { source: THREE.Material | THREE.Material[]; patches: Patch[] }
interface Sweep { startedAt: number; center: THREE.Vector2; maxRadius: number; radius: number }
const playedKeys = new Set<string>(), sweeps = new Map<string, Sweep>();
const SURFACES: Record<Surface, number> = { building: 0, ground: 1, road: 2, grid: 3 };
const SWEEP_FADE_MS = 280;

const vertexPosition = `
vec4 hologramWorld = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  hologramWorld = batchingMatrix * hologramWorld;
#endif
#ifdef USE_INSTANCING
  hologramWorld = instanceMatrix * hologramWorld;
#endif
vHologramPosition = (modelMatrix * hologramWorld).xyz;
`;
const fragmentUniforms = `
varying vec3 vHologramPosition;
uniform vec2 uSweepCenter;
uniform float uSweepRadius;
uniform float uTime;
uniform float uMode;
uniform float uSweepGlow;
uniform float uSweepWidth;
uniform float uSceneRadius;
uniform vec2 uHeightRange;
uniform float uGridSpacing;
uniform float uHologramSurface;
uniform vec3 uHologramNavy;
uniform vec3 uHologramCyan;
uniform vec3 uHologramViolet;
float hologramAmount() {
  if (uMode < 0.5) return 0.0;
  if (uMode < 1.5) return 1.0;
  float distanceToCenter = length(vHologramPosition.xz - uSweepCenter);
  return smoothstep(uSweepRadius - uSweepWidth, uSweepRadius, distanceToCenter);
}
float hologramGrid(vec2 point, float spacing) {
  vec2 cell = point / spacing, footprint = fwidth(cell);
  vec2 line = abs(fract(cell - 0.5) - 0.5) / max(footprint, vec2(0.0001));
  return (1.0 - min(min(line.x, line.y), 1.0)) * (1.0 - smoothstep(0.5, 1.5, max(footprint.x, footprint.y)));
}
`;
const fragmentOutput = `
if (uMode > 0.5) {
  float hologram = hologramAmount();
  float radialDistance = length(vHologramPosition.xz - uSweepCenter);
  float front = (1.0 - smoothstep(0.0, uSweepWidth, abs(radialDistance - uSweepRadius))) * uSweepGlow;
  float height = clamp((vHologramPosition.y - uHeightRange.x) / uHeightRange.y, 0.0, 1.0);
  vec3 blueprint = uHologramNavy;
  if (uHologramSurface < 0.5) {
    vec3 face = cross(dFdx(vHologramPosition), dFdy(vHologramPosition));
    vec3 faceNormal = face / max(length(face), 0.00001);
    vec3 eye = normalize(cameraPosition - vHologramPosition);
    float rim = pow(1.0 - abs(dot(faceNormal, eye)), 2.5);
    float scanY = vHologramPosition.y / 3.0 - uTime * 0.18;
    float scanAA = max(fwidth(scanY), 0.015);
    float scan = (1.0 - smoothstep(0.035, 0.035 + scanAA, abs(fract(scanY) - 0.5))) * (1.0 - smoothstep(0.4, 1.2, scanAA));
    vec3 rimColor = mix(uHologramViolet, uHologramCyan, height);
    blueprint += rimColor * (0.035 + height * 0.065 + rim * 0.65 + scan * 0.13);
    blueprint += uHologramCyan * front * (0.7 + rim);
  } else if (uHologramSurface < 1.5) {
    float minor = hologramGrid(vHologramPosition.xz, uGridSpacing);
    float major = hologramGrid(vHologramPosition.xz, uGridSpacing * 5.0);
    float falloff = 1.0 - smoothstep(uSceneRadius * 0.08, uSceneRadius, radialDistance);
    blueprint = uHologramNavy * 0.65 + (uHologramViolet * minor * 0.08 + uHologramCyan * major * 0.22) * (0.15 + falloff * 0.85);
  } else {
    blueprint += uHologramCyan * 0.4;
  }
  float fog = 1.0 - exp(-pow(length(cameraPosition - vHologramPosition) / (uSceneRadius * 1.8), 2.0));
  blueprint = mix(blueprint, uHologramNavy, min(fog, 0.92));
  gl_FragColor.rgb = mix(gl_FragColor.rgb, blueprint, hologram) + uHologramCyan * front * 0.85;
  if (uHologramSurface > 2.5) gl_FragColor.a *= 1.0 - hologram;
  else gl_FragColor.a = mix(gl_FragColor.a, 1.0, hologram);
}
`;

/** Owns only preview material clones. Texture ownership stays with the source material/cache. */
export class PreviewHologram {
  private readonly background: THREE.Scene['background'];
  private readonly stage = new THREE.Color('#060b16');
  private readonly roots = new Map<THREE.Object3D, { surface: Surface; nodes: Set<RenderObject> }>();
  private readonly bindings = new Map<RenderObject, Binding>();
  private readonly patches = new Map<THREE.Material, Map<Surface, Patch>>();
  private readonly media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  private reduced = this.media?.matches ?? false;
  private mode: HologramMode = 'real';
  private overlay?: PreviewOverlay;
  private sweep?: Sweep;
  private revealKey?: string;
  private maxRadius = 1;
  private lastTime?: number;
  private readonly uniforms = {
    uSweepCenter: { value: new THREE.Vector2() }, uSweepRadius: { value: -1 },
    uTime: { value: 0 }, uMode: { value: 0 }, uSweepGlow: { value: 0 }, uSweepWidth: { value: 1 },
    uSceneRadius: { value: 1 }, uHeightRange: { value: new THREE.Vector2(0, 1) }, uGridSpacing: { value: 10 },
    uHologramNavy: { value: new THREE.Color('#060b16') }, uHologramCyan: { value: new THREE.Color('#38c6f4') },
    uHologramViolet: { value: new THREE.Color('#8a7dff') },
  };
  private motionChange = () => {
    this.reduced = this.media?.matches ?? false;
    if (this.reduced && this.mode === 'sweep') this.finish();
    this.invalidate();
  };

  constructor(private readonly scene: THREE.Scene, private readonly invalidate: () => void) {
    this.background = scene.background;
    this.media?.addEventListener('change', this.motionChange);
  }

  update(overlay: PreviewOverlay, bounds: THREE.Box3) {
    this.overlay = overlay;
    const wasReal = this.mode === 'real';
    const mode = hologramMode(overlay, playedKeys), key = mode === 'sweep' ? overlay.reveal!.key : undefined;
    if (key !== this.revealKey) this.sweep = key === undefined ? undefined : sweeps.get(key);
    this.revealKey = key; this.mode = mode;
    this.setBounds(bounds);
    if (this.reduced && mode === 'sweep') this.finish();
    else {
      this.uniforms.uMode.value = mode === 'blueprint' ? 1 : mode === 'sweep' ? 2 : 0;
      this.uniforms.uSweepRadius.value = mode === 'blueprint' ? -1 : this.sweep?.radius ?? -1;
      this.uniforms.uSweepGlow.value = 0;
      this.stage.copy(this.uniforms.uHologramNavy.value);
      this.scene.background = mode === 'real' ? this.background : this.stage;
      if (wasReal !== (mode === 'real')) for (const [root, record] of this.roots) this.sync(root, record.surface);
    }
    this.invalidate();
  }

  setBounds(bounds: THREE.Box3) {
    if (!this.overlay) return;
    const center = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3());
    const origin = this.sweep?.center ?? new THREE.Vector2(...hologramCenter(this.overlay, [center.x, center.z]));
    this.uniforms.uSweepCenter.value.copy(origin);
    const halfWidth = Math.max(size.x, 200) * 1.15 / 2, halfDepth = Math.max(size.z, 200) * 1.15 / 2;
    this.maxRadius = Math.hypot(Math.abs(center.x - origin.x) + halfWidth, Math.abs(center.z - origin.y) + halfDepth);
    this.uniforms.uSweepWidth.value = Math.max(1.5, this.maxRadius * 0.004);
    this.maxRadius += this.uniforms.uSweepWidth.value * 2;
    if (this.sweep) this.sweep.maxRadius = Math.max(this.sweep.maxRadius, this.maxRadius);
    this.uniforms.uSceneRadius.value = this.maxRadius;
    this.uniforms.uHeightRange.value.set(bounds.min.y, Math.max(size.y, 1));
    this.uniforms.uGridSpacing.value = Math.max(5, Math.pow(10, Math.floor(Math.log10(this.maxRadius / 25))));
  }

  sync(root: THREE.Object3D, surface: Surface) {
    const previous = this.roots.get(root)?.nodes ?? new Set<RenderObject>(), nodes = new Set<RenderObject>();
    if (this.mode !== 'real') root.traverse(node => {
      if (!(node instanceof THREE.Mesh || node instanceof THREE.Line)) return;
      nodes.add(node);
      if (this.bindings.has(node)) return;
      let kind = surface;
      if (surface === 'ground' && node instanceof THREE.Line) kind = 'grid';
      // Traffic polygons are direct city children; streamed GLBs are nested and final batches are named.
      if (surface === 'building' && node.parent?.name === 'traffic-city' && !['city-buildings', 'lite-buildings'].includes(node.name)) kind = 'road';
      const source = node.material;
      const patches = (Array.isArray(source) ? source : [source]).map(material => this.patch(material, kind));
      patches.forEach(patch => patch.users++);
      this.bindings.set(node, { source, patches });
      node.material = Array.isArray(source) ? patches.map(patch => patch.material) : patches[0].material;
    });
    for (const node of previous) if (!nodes.has(node)) this.restore(node);
    this.roots.set(root, { surface, nodes });
  }

  release(root: THREE.Object3D) {
    for (const node of this.roots.get(root)?.nodes ?? []) this.restore(node);
    this.roots.delete(root);
  }

  /** Called by ScenePreview's demand loop; the caller schedules at most 30 Hz for drift. */
  advance(now: number): boolean {
    const elapsed = this.lastTime === undefined ? 0 : Math.min(now - this.lastTime, 100); this.lastTime = now;
    if (this.mode === 'real' || document.hidden) return false;
    if (!this.reduced) this.uniforms.uTime.value += elapsed / 1000;
    if (this.mode === 'blueprint') return !this.reduced;
    if (this.reduced) { this.finish(); return false; }
    const key = this.revealKey!;
    // Start on the first rendered frame, so a discarded React effect cannot consume the reveal.
    this.sweep ??= sweeps.get(key) ?? { startedAt: now, center: this.uniforms.uSweepCenter.value.clone(), maxRadius: this.maxRadius, radius: -1 };
    sweeps.set(key, this.sweep);
    const age = now - this.sweep.startedAt;
    this.sweep.radius = Math.max(this.sweep.radius, sweepRadius(age, SWEEP_DURATION_MS, this.sweep.maxRadius));
    this.uniforms.uSweepRadius.value = this.sweep.radius;
    this.uniforms.uSweepGlow.value = Math.min(age / 100, 1) * Math.max(0, 1 - Math.max(0, age - SWEEP_DURATION_MS) / SWEEP_FADE_MS);
    if (this.background instanceof THREE.Color) this.stage.copy(this.uniforms.uHologramNavy.value).lerp(this.background, Math.pow(Math.min(age / SWEEP_DURATION_MS, 1), 2));
    if (age >= SWEEP_DURATION_MS + SWEEP_FADE_MS) { this.finish(); return false; }
    return true;
  }

  dispose() {
    this.media?.removeEventListener('change', this.motionChange);
    for (const node of this.bindings.keys()) this.restore(node);
    for (const cache of this.patches.values()) for (const patch of cache.values()) patch.material.dispose();
    this.patches.clear(); this.roots.clear();
    this.scene.background = this.background;
  }

  private finish() {
    if (this.revealKey !== undefined) { playedKeys.add(this.revealKey); sweeps.delete(this.revealKey); }
    this.mode = 'real'; this.sweep = undefined;
    this.uniforms.uMode.value = 0; this.uniforms.uSweepGlow.value = 0;
    this.scene.background = this.background;
    for (const [root, record] of this.roots) this.sync(root, record.surface);
  }

  private patch(source: THREE.Material, surface: Surface): Patch {
    const cache = this.patches.get(source) ?? new Map<Surface, Patch>();
    const cached = cache.get(surface); if (cached) return cached;
    if (!(source instanceof THREE.MeshStandardMaterial || source instanceof THREE.MeshBasicMaterial || source instanceof THREE.MeshLambertMaterial || source instanceof THREE.MeshPhongMaterial || source instanceof THREE.LineBasicMaterial)) throw Error(`Preview hologram cannot patch ${source.type}`);
    const material = source.clone(), originalKey = source.customProgramCacheKey();
    // Shared textures must never be disposed through disposeObject, even for an owned clone.
    material.userData.sharedAsset = true;
    if (surface === 'grid') { material.transparent = true; material.depthWrite = false; }
    material.customProgramCacheKey = () => `${originalKey}|preview-hologram-v1`;
    material.onBeforeCompile = (shader, renderer) => {
      source.onBeforeCompile.call(material, shader, renderer);
      Object.assign(shader.uniforms, this.uniforms, { uHologramSurface: { value: SURFACES[surface] } });
      shader.vertexShader = `varying vec3 vHologramPosition;\n${shader.vertexShader}`;
      shader.vertexShader = shader.vertexShader.replace('#include <project_vertex>', `#include <project_vertex>\n${vertexPosition}`);
      shader.fragmentShader = fragmentUniforms + shader.fragmentShader;
      shader.fragmentShader = shader.fragmentShader.replace('#include <alphatest_fragment>', 'diffuseColor.a = mix(diffuseColor.a, 1.0, hologramAmount());\n#include <alphatest_fragment>');
      shader.fragmentShader = shader.fragmentShader.replace('#include <opaque_fragment>', `#include <opaque_fragment>\n${fragmentOutput}`);
    };
    const patch = { source, material, surface, users: 0 }; cache.set(surface, patch); this.patches.set(source, cache);
    return patch;
  }

  private restore(node: RenderObject) {
    const binding = this.bindings.get(node); if (!binding) return;
    node.material = binding.source; this.bindings.delete(node);
    for (const patch of binding.patches) if (--patch.users === 0) {
      patch.material.dispose();
      const cache = this.patches.get(patch.source)!; cache.delete(patch.surface);
      if (!cache.size) this.patches.delete(patch.source);
    }
  }
}
