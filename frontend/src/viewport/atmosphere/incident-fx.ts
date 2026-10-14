import * as T from 'three';
import type { Conditions } from '../conditions';
import { fxHash, fxSeed, smokeAtlas, proceduralSmokeAtlas } from './fx-shared';
import type { Quality } from '../pipeline';

/**
 * Fire-incident visuals: pin, affected-area decal, flames and smoke, all closed-form
 * in (age, seed) from (t − activation) — never accumulated per frame. Smoke is not an
 * entity and nothing simulates combustion or spread.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const FLAME_MIN = 5, FLAME_MAX = 9;
const SMOKE_LIFETIME_S = 26, SMOKE_HIGH = 48, SMOKE_LOW = 16, SMOKE_RATE = 1.2;
const AREA = new T.Color('#ff6a3d'), ENDED = new T.Color('#8b95a1');
const SMOKE_BASE = new T.Color('#3b3632'), SMOKE_MID = new T.Color('#8f8a84'), SMOKE_HIGH_TONE = new T.Color('#b9b6b1');

type Row = Conditions['incidents'][number] & { seed: number };

interface Visual {
  unit: T.Group; ring: T.Mesh<T.PlaneGeometry, T.ShaderMaterial>; flames: T.InstancedMesh<T.PlaneGeometry, T.ShaderMaterial>; smoke: T.InstancedMesh<T.InstancedBufferGeometry, T.Material>;
  tint: T.InstancedBufferAttribute; pin: T.Sprite; leader: T.Line<T.BufferGeometry, T.LineBasicMaterial>; light?: T.PointLight; active: boolean;
}

const flameMaterial = () => new T.ShaderMaterial({
  transparent: true, depthWrite: false, blending: T.AdditiveBlending,
  uniforms: { uTime: { value: 0 }, uRamp: { value: 1 } },
  vertexShader: `varying vec2 flameUv;void main(){flameUv=uv;
    gl_Position=projectionMatrix*modelViewMatrix*instanceMatrix*vec4(position,1.0);}`,
  // Two octaves of scrolling value noise times a teardrop mask; colour core → tip by height.
  fragmentShader: `uniform float uTime;uniform float uRamp;varying vec2 flameUv;
    float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
    float a=fract(sin(dot(i,vec2(127.1,311.7)))*43758.5453),b=fract(sin(dot(i+vec2(1,0),vec2(127.1,311.7)))*43758.5453),
    c=fract(sin(dot(i+vec2(0,1),vec2(127.1,311.7)))*43758.5453),d=fract(sin(dot(i+vec2(1,1),vec2(127.1,311.7)))*43758.5453);
    return mix(mix(a,b,f.x),mix(c,d,f.x),f.y);}
    void main(){vec2 uv=flameUv;float t=uTime;
    float n=noise(uv*3.0+vec2(t*0.9,-t*1.6))*0.6+noise(uv*7.0-vec2(t*1.4,t*2.2))*0.4;
    float teardrop=1.0-smoothstep(0.0,0.55*pow(1.0-uv.y,0.7),abs(uv.x-0.5)*2.0);
    float mask=smoothstep(1.0,0.25,uv.y)*teardrop*(0.55+0.45*n);
    if(mask<=0.01)discard;
    vec3 color=mix(mix(vec3(1.0,0.824,0.478),vec3(1.0,0.541,0.169),clamp(uv.y*1.4,0.0,1.0)),vec3(0.886,0.275,0.106),smoothstep(0.45,0.95,uv.y));
    gl_FragColor=vec4(color,min(0.9,mask*1.2)*uRamp);}`,
});

const smokeMaterial = (map: T.Texture) => new T.ShaderMaterial({
  transparent: true, depthWrite: false,
  uniforms: { uMap: { value: map } },
  vertexShader: `attribute vec4 puffTint;varying vec2 puffUv;varying vec4 puffColor;
    void main(){puffUv=uv*0.5;puffColor=puffTint;
    gl_Position=projectionMatrix*modelViewMatrix*instanceMatrix*vec4(position,1.0);}`,
  fragmentShader: `uniform sampler2D uMap;varying vec2 puffUv;varying vec4 puffColor;
    void main(){vec4 puff=texture2D(uMap,puffUv);float alpha=dot(puff.rgb,vec3(0.2126,0.7152,0.0722))*puff.a*puffColor.a;
    if(alpha<=0.002)discard;gl_FragColor=vec4(puffColor.rgb,alpha);}`,
});

/** Ground decal: dashed 12-segment outer ring plus a radial inner fill, depth-tested with polygon offset. */
const ringMaterial = () => new T.ShaderMaterial({
  transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2,
  uniforms: { uColor: { value: AREA.clone() }, uOpacity: { value: 0.85 }, uRadius: { value: 30 }, uFill: { value: 1 } },
  vertexShader: 'varying vec2 ringUv;void main(){ringUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
  fragmentShader: `uniform vec3 uColor;uniform float uOpacity;uniform float uRadius;uniform float uFill;varying vec2 ringUv;
    void main(){float d=length(ringUv-0.5)*2.0;if(d>1.0)discard;
    float angle=atan(ringUv.y-0.5,ringUv.x-0.5);
    float width=max(1.5/uRadius,fwidth(d)*1.5);
    float alpha=(1.0-d)*0.12*uFill;
    if(d>1.0-width&&fract((angle/6.28318530718+0.5)*12.0)<=0.55)alpha=uOpacity;
    if(alpha<=0.002)discard;gl_FragColor=vec4(uColor,alpha);}`,
});

/** Two-tone flame (or person) glyph on a disc with a 2 px white ring, drawn once to a canvas texture. */
function pinTexture(kind: 'fire' | 'person_fall', ended = false): T.Texture {
  const size = 44, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  context.beginPath(); context.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
  context.fillStyle = ended ? '#8b95a1' : kind === 'fire' ? '#e2461b' : '#d55e00'; context.fill();
  context.lineWidth = 2; context.strokeStyle = '#ffffff'; context.stroke();
  context.fillStyle = '#ffd27a';
  if (kind === 'fire') {
    context.beginPath(); context.moveTo(size / 2, 10);
    context.bezierCurveTo(size / 2 + 8, 18, size / 2 + 6, 26, size / 2, 32);
    context.bezierCurveTo(size / 2 - 6, 26, size / 2 - 8, 18, size / 2, 10); context.fill();
    context.fillStyle = '#fff1dc'; context.beginPath(); context.moveTo(size / 2, 20);
    context.bezierCurveTo(size / 2 + 4, 24, size / 2 + 3, 28, size / 2, 31);
    context.bezierCurveTo(size / 2 - 3, 28, size / 2 - 4, 24, size / 2, 20); context.fill();
  } else {
    context.beginPath(); context.arc(size / 2, 16, 5, 0, Math.PI * 2); context.fill();
    context.fillRect(size / 2 - 5, 22, 10, 12);
  }
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  return texture;
}

let pinTextures: Partial<Record<'fire' | 'person_fall' | 'fire-ended' | 'person_fall-ended', T.Texture>> | undefined;
let pinUsers = 0;

const QUARTER_TURN = new T.Euler();

export class IncidentFX {
  readonly root = new T.Group();
  readonly ready: Promise<void>;
  private quality: Quality;
  private visuals = new Map<string, Visual>();
  private flames = flameMaterial();
  private smoke?: T.ShaderMaterial;
  private plane = new T.PlaneGeometry(1, 1);
  private ringGeometry = new T.PlaneGeometry(2, 2).rotateX(-Math.PI / 2);
  private leaderGeometry = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3(0, 1, 0)]);
  private matrix = new T.Matrix4();
  private quaternion = new T.Quaternion();
  private scale = new T.Vector3();
  private position = new T.Vector3();
  private color = new T.Color();
  private vertical = new T.Vector3(0, 1, 0);
  private disposed = false;
  constructor(quality: Quality) {
    this.quality = quality;
    pinUsers++;
    this.ready = smokeAtlas().then(texture => {
      if (this.disposed) return;
      this.smoke = smokeMaterial(texture ?? proceduralSmokeAtlas());
      for (const visual of this.visuals.values()) { visual.smoke.material.dispose(); visual.smoke.material = this.smoke; }
    });
  }
  setQuality(quality: Quality) {
    this.quality = quality;
    // Low quality drops the PointLight (spec §3.7).
    for (const visual of this.visuals.values()) if (visual.light) visual.light.visible = false;
  }
  private create(row: Row): Visual {
    pinTextures ??= { fire: pinTexture('fire'), person_fall: pinTexture('person_fall'), 'fire-ended': pinTexture('fire', true), 'person_fall-ended': pinTexture('person_fall', true) };
    const unit = new T.Group(); unit.name = `fx-incident-${row.id}`;
    const ring = new T.Mesh(this.ringGeometry, ringMaterial());
    ring.renderOrder = 3;
    const flameCount = Math.round(FLAME_MIN + fxHash(row.seed, 1) * (FLAME_MAX - FLAME_MIN));
    const flames = new T.InstancedMesh(this.plane, this.flames.clone(), flameCount);
    flames.count = flameCount; flames.frustumCulled = false; flames.renderOrder = 4;
    // Per-fire puff storage: instance attributes ride a private geometry clone.
    const capacity = SMOKE_HIGH;
    const geometry = new T.InstancedBufferGeometry();
    geometry.index = this.plane.index; geometry.attributes.position = this.plane.attributes.position; geometry.attributes.uv = this.plane.attributes.uv;
    const tint = new T.InstancedBufferAttribute(new Float32Array(capacity * 4), 4);
    tint.setUsage(T.DynamicDrawUsage);
    geometry.setAttribute('puffTint', tint);
    geometry.instanceCount = 0;
    const smoke = new T.InstancedMesh(geometry, this.smoke ?? new T.MeshBasicMaterial({ visible: false }), capacity);
    smoke.count = 0; smoke.frustumCulled = false; smoke.renderOrder = 2;
    const pin = new T.Sprite(new T.SpriteMaterial({ map: pinTextures![row.kind], sizeAttenuation: false, depthTest: false, transparent: true }));
    pin.scale.set(0.022, 0.022, 1); pin.renderOrder = 12; pin.center.set(0.5, 1);
    const leader = new T.Line(this.leaderGeometry, new T.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.6, depthTest: false }));
    leader.renderOrder = 11; leader.frustumCulled = false;
    unit.add(ring, flames, smoke, pin, leader);
    const visual: Visual = { unit, ring, flames, smoke, tint, pin, leader, active: false };
    if (row.kind === 'fire') {
      visual.light = new T.PointLight('#ff8a2b', 2.5, row.radiusM * 3);
      visual.light.visible = false; unit.add(visual.light);
    }
    this.root.add(unit);
    this.visuals.set(row.id, visual);
    return visual;
  }
  update(rows: Conditions['incidents'], simSeconds: number, camera: T.Camera, ringStart: Map<string, number>, origin?: T.Vector3, roofHeight?: (east: number, north: number) => number) {
    if (this.disposed) return;
    if (!rows.length && !this.visuals.size) return;
    const shown = new Set<string>();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (const row of rows) {
      shown.add(row.id);
      const seed = fxSeed(row.id);
      const visual = this.visuals.get(row.id) ?? this.create({ ...row, seed });
      const pinKind = row.phase === 'ended' ? `${row.kind}-ended` as const : row.kind;
      visual.pin.material.map = pinTextures![pinKind]!;
      if (row.phase === 'pending') { visual.unit.visible = false; continue; }
      visual.unit.visible = true;
      const ended = row.phase === 'ended';
      // Render axes: east, up, south, relative to the render origin. Roofs by projection, not draping.
      const east = row.position[0] - (origin?.x ?? 0), south = -row.position[1] - (origin?.z ?? 0);
      const groundY = Math.max(roofHeight?.(row.position[0], row.position[1]) ?? row.position[2], 0) - (origin?.y ?? 0);
      visual.unit.position.set(east, groundY, south);
      // Ring draw-in: 0.6 s wall-clock, once per activation (spec §5). Grey tint after ending.
      const start = ringStart.get(row.id), drawIn = !reduced && start !== undefined ? Math.min(1, (performance.now() - start) / 600) : 1;
      const ringMat = visual.ring.material as T.ShaderMaterial;
      ringMat.uniforms.uColor.value.set(ended ? ENDED : row.kind === 'fire' ? AREA : '#d55e00');
      ringMat.uniforms.uOpacity.value = (ended ? 0.5 : 0.85) * drawIn;
      ringMat.uniforms.uRadius.value = row.radiusM;
      ringMat.uniforms.uFill.value = ended ? 0 : drawIn;
      visual.ring.scale.setScalar(row.radiusM);
      visual.ring.rotation.y = -drawIn * Math.PI / 6; // reveals the last of the 12 dashes clockwise
      // Flames: ramp 3 s in sim time after activation, fade 4 s after ending.
      const burning = row.kind === 'fire' && (!ended || row.sinceS < 4);
      visual.active = row.kind === 'fire' && row.phase === 'active';
      visual.flames.visible = burning;
      if (burning) {
        const ramp = ended ? Math.max(0, 1 - row.sinceS / 4) : Math.min(1, row.sinceS / 3);
        const material = visual.flames.material as T.ShaderMaterial;
        material.uniforms.uTime.value = simSeconds;
        material.uniforms.uRamp.value = ramp;
        const height = Math.min(9, Math.max(3, row.radiusM * 0.25)), cluster = row.radiusM * 0.25;
        for (let i = 0; i < visual.flames.count; i++) {
          const angle = fxHash(seed, i * 4 + 2) * Math.PI * 2, radius = fxHash(seed, i * 4 + 3) * cluster;
          // Camera-facing billboards, each rotated about its own vertical axis.
          this.quaternion.copy(camera.quaternion);
          QUARTER_TURN.setFromQuaternion(this.quaternion, 'YXZ');
          this.quaternion.setFromAxisAngle(this.vertical, QUARTER_TURN.y + angle + Math.sin(simSeconds * 0.6 + i) * 0.15);
          this.scale.set(height * 0.55, height * (0.8 + 0.4 * fxHash(seed, i * 4 + 4)), 1);
          this.position.set(Math.cos(angle) * radius, this.scale.y / 2, Math.sin(angle) * radius);
          this.matrix.compose(this.position, this.quaternion, this.scale);
          visual.flames.setMatrixAt(i, this.matrix);
        }
        visual.flames.instanceMatrix.needsUpdate = true;
      }
      if (row.kind === 'fire') this.writeSmoke(visual, seed, simSeconds, camera, row);
      else { visual.smoke.visible = false; visual.smoke.count = 0; }
      // Pin: max(roof height, 0) + 14 m above the ground point, leader line down to it.
      const pinHeight = 14;
      visual.pin.position.set(0, pinHeight, 0);
      visual.leader.position.set(0, 0, 0);
      const points = this.leaderGeometry.attributes.position as T.BufferAttribute;
      points.setXYZ(0, 0, pinHeight - 0.55, 0); points.setXYZ(1, 0, 0, 0); points.needsUpdate = true;
    }
    for (const [id, visual] of this.visuals) if (!shown.has(id)) { this.disposeVisual(visual); this.visuals.delete(id); }
    // One warm PointLight per scene for the nearest active fire.
    let nearest: Visual | undefined, distance = Infinity;
    if (this.quality === 'high') for (const visual of this.visuals.values()) {
      if (!visual.light || !visual.active || visual.unit.visible !== true) continue;
      const d = visual.unit.position.distanceToSquared(camera.position);
      if (d < distance) { distance = d; nearest = visual; }
    }
    for (const visual of this.visuals.values()) if (visual.light) visual.light.visible = visual === nearest;
  }
  /** Puffs are closed-form in (age, seed): position, size, spin and alpha need no history. */
  private writeSmoke(visual: Visual, seed: number, simSeconds: number, camera: T.Camera, row: Conditions['incidents'][number]) {
    if (!this.smoke) { visual.smoke.visible = false; return; }
    const capacity = visual.smoke.instanceMatrix.count;
    const windEast = this.wind.x, windNorth = this.wind.y;
    const limit = this.quality === 'low' ? SMOKE_LOW : SMOKE_HIGH;
    const period = limit / SMOKE_RATE; // deterministic emission schedule across one lifetime
    const ended = row.phase === 'ended';
    const startS = row.startS ?? (ended ? undefined : simSeconds - row.sinceS);
    const emissionS = ended ? simSeconds - row.sinceS : simSeconds;
    let count = 0;
    for (let slot = 0; slot < limit && count < capacity; slot++) {
      // Emission is a repeating closed-form schedule (period = limit / rate); a puff
      // exists while 0 ≤ age < lifetime, so the plume is the same at any seek.
      const born = slot / SMOKE_RATE;
      const cycle = Math.floor((emissionS - born) / period);
      const emittedAt = born + cycle * period;
      if (startS !== undefined && emittedAt < startS) continue;
      // After ending emission stops: the remaining puffs live out their life (spec §3.1).
      const age = simSeconds - emittedAt;
      if (age < 0 || age > SMOKE_LIFETIME_S) continue;
      const life = age / SMOKE_LIFETIME_S;
      const seedA = fxHash(seed, slot * 8 + 1), seedB = fxHash(seed, slot * 8 + 2), seedC = fxHash(seed, slot * 8 + 3);
      // Rise 2.2 m/s slowing with height; drift with the CURRENT wind × 0.9 plus seed jitter.
      const risen = 2.2 * age * (1 - 0.45 * Math.min(1, age / SMOKE_LIFETIME_S));
      const drift = age * 0.9;
      const jitterX = (seedA - 0.5) * 6 + Math.sin(simSeconds * 0.2 + seedB * 6) * 1.5;
      const jitterZ = (seedB - 0.5) * 6 + Math.cos(simSeconds * 0.17 + seedC * 6) * 1.5;
      const size = 6 + 28 * life;
      this.quaternion.copy(camera.quaternion);
      QUARTER_TURN.setFromQuaternion(this.quaternion, 'YXZ');
      QUARTER_TURN.z = simSeconds * (0.1 + seedC * 0.2) + seedA * Math.PI * 2;
      this.quaternion.setFromEuler(QUARTER_TURN);
      this.position.set(jitterX + windEast * drift, risen, jitterZ - windNorth * drift);
      this.scale.set(size, size, 1);
      this.matrix.compose(this.position, this.quaternion, this.scale);
      visual.smoke.setMatrixAt(count, this.matrix);
      const tint = life < 0.5 ? this.color.copy(this.base).lerp(this.mid, life * 2) : this.color.copy(this.mid).lerp(this.high, (life - 0.5) * 2);
      visual.tint.setXYZW(count, tint.r, tint.g, tint.b, life < 0.15 ? (life / 0.15) * 0.42 : (1 - (life - 0.15) / 0.85) * 0.42);
      count++;
    }
    visual.smoke.count = count;
    (visual.smoke.geometry as T.InstancedBufferGeometry).instanceCount = count;
    visual.smoke.instanceMatrix.needsUpdate = true;
    visual.tint.needsUpdate = true;
    visual.smoke.visible = count > 0;
  }
  setWind(east: number, north: number) { this.wind.set(east, north); }
  private wind = new T.Vector2();
  private base = SMOKE_BASE;
  private mid = SMOKE_MID;
  private high = SMOKE_HIGH_TONE;
  private disposeVisual(visual: Visual) {
    visual.unit.removeFromParent(); visual.ring.material.dispose(); visual.flames.material.dispose();
    visual.flames.dispose(); visual.smoke.dispose();
    visual.smoke.geometry.dispose(); if (visual.smoke.material !== this.smoke) visual.smoke.material.dispose();
    visual.pin.material.dispose(); visual.leader.material.dispose();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const visual of this.visuals.values()) {
      this.disposeVisual(visual);
    }
    this.visuals.clear();
    this.flames.dispose(); this.smoke?.dispose();
    this.plane.dispose(); this.ringGeometry.dispose(); this.leaderGeometry.dispose();
    if (--pinUsers === 0) {
      for (const texture of Object.values(pinTextures ?? {})) texture?.dispose();
      pinTextures = undefined;
    }
  }
}
