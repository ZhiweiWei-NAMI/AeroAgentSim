import * as T from 'three';
import { incidentLabel, type Conditions } from '../conditions';
import { worldPosition } from '../coordinates';
import { fxHash, fxSeed, smokeAtlas, proceduralSmokeAtlas } from './fx-shared';
import type { Quality } from '../pipeline';

/**
 * Fire-incident visuals: pin, affected-area decal, flames and smoke, all closed-form
 * in (age, seed) from (t − activation) — never accumulated per frame. Smoke is not an
 * entity and nothing simulates combustion or spread.
 * Visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.
 */

const FLAME_MIN = 5, FLAME_MAX = 7;
// A soft flame mask needs this projected height to leave a glint outside the 26 px pin,
// including the half-resolution low-quality drawing buffer. Close views keep their world size.
const FLAME_MIN_CSS_PX = 40;
// Connected plume: 2.0 puffs/s at high quality (limit 56, life 24 s).
// Low quality keeps the same emission rate with a shorter life: a shorter plume, never a sparser one.
const SMOKE_RATE = 2.0, SMOKE_LIFE_HIGH_S = 24, SMOKE_LIFE_LOW_S = 9, SMOKE_HIGH = 56, SMOKE_LOW = 18;
const AREA = new T.Color('#ff6a3d'), ENDED = new T.Color('#8b95a1');
const SMOKE_BASE = new T.Color('#3b3632'), SMOKE_MID = new T.Color('#8f8a84'), SMOKE_HIGH_TONE = new T.Color('#b9b6b1');

type Row = Conditions['incidents'][number] & { seed: number };
export interface AtmosphereOptions {
  annotations: boolean;
  /** Fired when async resources (the smoke atlas) finish loading; the idle renderer invalidates. */
  onReady?: () => void;
  /** The run uses EntityLayer's labels; the draft has a small overlay of its own. */
  preview?: { overlay: HTMLElement; locale: () => string };
}

interface Visual {
  unit: T.Group; seed: number; flames: T.InstancedMesh<T.PlaneGeometry, T.ShaderMaterial>;
  flameCore: T.InstancedMesh<T.PlaneGeometry, T.ShaderMaterial>;
  smoke: T.InstancedMesh<T.InstancedBufferGeometry, T.Material>; glow?: T.Mesh<T.PlaneGeometry, T.ShaderMaterial>;
  tint: T.InstancedBufferAttribute; cells: T.InstancedBufferAttribute; light?: T.PointLight; active: boolean;
  annotation?: { ring: T.Mesh<T.PlaneGeometry, T.ShaderMaterial>; ringOccluded?: T.Mesh<T.PlaneGeometry, T.ShaderMaterial>; pin: T.Sprite; leader: T.Line<T.BufferGeometry, T.LineBasicMaterial>; label?: HTMLElement };
}

/** Flickering additive ground-glow decal at the fire's base (roof height when the point is on a roof). */
const glowMaterial = () => new T.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, blending: T.AdditiveBlending,
  polygonOffset: true, polygonOffsetFactor: -4,
  uniforms: { uTime: { value: 0 }, uRamp: { value: 1 } },
  vertexShader: `varying vec2 glowUv;void main(){glowUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
  fragmentShader: `uniform float uTime;uniform float uRamp;varying vec2 glowUv;
    float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    void main(){float d=length(glowUv-0.5)*2.0;
    // Sim-time flicker: two incommensurate sines on the hashed phase keep the disc alive.
    float flicker=0.75+0.25*sin(uTime*7.3+hash(vec2(3.0,7.0)))*sin(uTime*11.1+hash(vec2(5.0,1.0)));
    float alpha=(1.0-smoothstep(0.0,1.0,d))*0.35*flicker*uRamp;
    if(alpha<=0.004)discard;gl_FragColor=vec4(1.0,0.254,0.024,alpha);${OUTPUT_F}}`,
});

// Chunks must start on their own lines for Three's shader preprocessor.
const FOG_PARS_V = '\n#include <fog_pars_vertex>\n', FOG_V = '\n#include <fog_vertex>\n',
  FOG_PARS_F = '\n#include <fog_pars_fragment>\n', FOG_F = '\n#include <fog_fragment>\n';
// The run composer handles display conversion; direct previews/captures need these chunks.
const OUTPUT_F = '\n#include <tonemapping_fragment>\n#include <colorspace_fragment>\n';

// Emissive flames lose intensity through fog, while retaining their orange hue.
// Adding fog colour to an additive core would brighten the fog itself.
const FOG_VISIBILITY = `float visibility=1.0;
  #ifdef USE_FOG
    #ifdef FOG_EXP2
      visibility=exp(-fogDensity*fogDensity*vFogDepth*vFogDepth);
    #else
      visibility=1.0-smoothstep(fogNear,fogFar,vFogDepth);
    #endif
  #endif
`;

/**
 * One instanced quad per flame, drawn twice with `uCore`: pass 0 is the flame body in
 * NormalBlending (orange stays orange on a light, foggy sky), pass 1 is a small additive
 * core at ≤ 0.6 alpha. Premultiplied output matches the material's blend factors.
 * Colours: #ffd27a only in the lowest 20 %, body #ff8a2b, tips #e2461b into a hint of
 * dark smoke at the very top.
 */
const flameMaterial = (additiveCore = false) => new T.ShaderMaterial({
  transparent: true, depthWrite: false, premultipliedAlpha: true, blending: additiveCore ? T.AdditiveBlending : T.NormalBlending, fog: true, side: T.DoubleSide, forceSinglePass: true,
  uniforms: T.UniformsUtils.merge([T.UniformsLib.fog, { uTime: { value: 0 }, uRamp: { value: 1 }, uCore: { value: additiveCore ? 1 : 0 } }]),
  vertexShader: `varying vec2 flameUv;${FOG_PARS_V}
    void main(){flameUv=uv;
    vec4 mvPosition=modelViewMatrix*instanceMatrix*vec4(position,1.0);
    gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  // Two octaves of scrolling value noise times a teardrop mask; colour core → tip by height.
  fragmentShader: `uniform float uTime;uniform float uRamp;uniform float uCore;varying vec2 flameUv;${FOG_PARS_F}
    float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
    float a=fract(sin(dot(i,vec2(127.1,311.7)))*43758.5453),b=fract(sin(dot(i+vec2(1,0),vec2(127.1,311.7)))*43758.5453),
    c=fract(sin(dot(i+vec2(0,1),vec2(127.1,311.7)))*43758.5453),d=fract(sin(dot(i+vec2(1,1),vec2(127.1,311.7)))*43758.5453);
    return mix(mix(a,b,f.x),mix(c,d,f.x),f.y);}
    void main(){vec2 uv=flameUv;float t=uTime;
    float n=noise(uv*3.0+vec2(t*0.9,-t*1.6))*0.6+noise(uv*7.0-vec2(t*1.4,t*2.2))*0.4;
    float teardrop=1.0-smoothstep(0.0,max(0.001,0.9*pow(max(0.0,1.0-uv.y),0.7)),abs(uv.x-0.5)*2.0);
    float mask=(1.0-smoothstep(0.25,1.0,uv.y))*teardrop*(0.55+0.45*n);
    float core=teardrop*(1.0-smoothstep(0.0,1.0,uv.y))*(1.0-smoothstep(0.2,0.75,abs(uv.x-0.5)*2.0))*(0.5+0.5*n);
    ${FOG_VISIBILITY}
    float alpha=(uCore<0.5?min(0.85,mask*1.15):min(0.6,core*0.6))*uRamp*visibility;
    if(alpha<=0.01)discard;
    // #ffd27a → #ff8a2b → #e2461b → hint of #3b3632 at the very top.
    // Palette in the renderer's linear colour space.
    vec3 color=mix(mix(vec3(1.0,0.644,0.195),vec3(1.0,0.254,0.024),clamp(uv.y*5.0,0.0,1.0)),
      vec3(0.761,0.061,0.011),smoothstep(0.45,0.95,uv.y));
    color=mix(color,vec3(0.044,0.037,0.032),smoothstep(0.9,1.0,uv.y)*0.35);
    if(uCore>0.5)color=mix(color,vec3(1.0,0.644,0.195),0.5);
    gl_FragColor=vec4(color,alpha);${OUTPUT_F}
    gl_FragColor.rgb*=alpha;}`,
});

// Scattered ambient/sky light: smoke reads LIGHTER than the dark dusk ground and darker than
// the light day ground. Rain greys both toward the same wet tone (0.3·r).
const SMOKE_LIGHT_DAY = new T.Color('#8f8a84'), SMOKE_LIGHT_DUSK = new T.Color('#5d5a63'), SMOKE_LIGHT_RAIN = new T.Color('#7d8288');
const GLOW_ORANGE = new T.Color('#ff8a2b');

const smokeMaterial = () => new T.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, fog: true,
  uniforms: { ...T.UniformsUtils.clone(T.UniformsLib.fog), uMap: { value: null as unknown as T.Texture },
    uSmokeLight: { value: new T.Color('#5d5a63') }, uGlow: { value: GLOW_ORANGE.clone() },
    uGlowStrength: { value: 0.55 }, uScatter: { value: 0.55 }, uFlameTop: { value: 10 } },
  vertexShader: `attribute vec4 puffTint;attribute float puffCell;varying vec2 puffUv;varying vec4 puffColor;varying float puffHeight;${FOG_PARS_V}
    void main(){puffUv=uv*0.5+vec2(mod(puffCell,2.0),floor(puffCell/2.0))*0.5;puffColor=puffTint;
    puffHeight=instanceMatrix[3].y;
    vec4 mvPosition=modelViewMatrix*instanceMatrix*vec4(position,1.0);
    gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  // Smoke is art-directed, not shaded by the rig: the plume scatters the ambient city/sky
  // light (mix with uSmokeLight) and the fire underlights the root while the flames burn.
  fragmentShader: `uniform sampler2D uMap;uniform vec3 uSmokeLight;uniform vec3 uGlow;uniform float uGlowStrength;uniform float uScatter;uniform float uFlameTop;varying vec2 puffUv;varying vec4 puffColor;varying float puffHeight;${FOG_PARS_F}
    void main(){vec4 puff=texture2D(uMap,puffUv);
    // The sparse atlas needs its softened density at small projected sizes. Keep the
    // close-view alpha, and restore the former sqrt density below 32–8 raster pixels.
    float footprint=max(fwidth(puffUv.x),fwidth(puffUv.y));
    float coverage=smoothstep(0.5/32.0,0.5/8.0,footprint);
    float alpha=mix(puff.a,sqrt(puff.a),coverage)*puffColor.a;
    if(alpha<=0.002)discard;
    vec3 base=puffColor.rgb;
    vec3 lit=mix(base,uSmokeLight,uScatter);
    float under=uGlowStrength*exp(-max(puffHeight-uFlameTop,0.0)/14.0);
    vec3 color=lit+uGlow*under*0.8;
    gl_FragColor=vec4(color,alpha);${OUTPUT_F}
    ${FOG_VISIBILITY}
    #ifdef USE_FOG
      // Same fog response as the city (no sqrt), so smoke and buildings agree at distance.
      gl_FragColor.rgb=mix(gl_FragColor.rgb,fogColor,1.0-visibility);
    #endif
    }`,
});


/** Fragment-shader width for the ring line: ~0.8 m at close range (0.4/R in d-units), a crisp
 *  ~1.5 px line far away (0.75·fwidth) (finding 7). */
const ringWidth = 'max(0.4/uRadius,0.75*fwidth(d))';

/** Ground decal: dashed outer ring plus a radial inner fill, depth-tested with polygon offset. */
const ringMaterial = (occluded: boolean) => new T.ShaderMaterial({
  transparent: true, depthWrite: false, depthTest: true, polygonOffset: true, polygonOffsetFactor: -2, fog: true,
  depthFunc: occluded ? T.GreaterDepth : T.LessEqualDepth,
  uniforms: T.UniformsUtils.merge([T.UniformsLib.fog, { uColor: { value: AREA.clone() }, uOpacity: { value: occluded ? 0.22 : 0.85 },
    uRadius: { value: 30 }, uExtent: { value: 34.5 }, uFill: { value: 1 }, uReveal: { value: 1 } }]),
  vertexShader: `varying vec2 ringUv;${FOG_PARS_V}
    void main(){ringUv=uv;vec4 mvPosition=modelViewMatrix*vec4(position,1.0);
    gl_Position=projectionMatrix*mvPosition;${FOG_V}}`,
  fragmentShader: `uniform vec3 uColor;uniform float uOpacity;uniform float uRadius;uniform float uExtent;uniform float uFill;uniform float uReveal;varying vec2 ringUv;${FOG_PARS_F}
    void main(){float d=length(ringUv-0.5)*2.0*uExtent/uRadius;
    float angle=fract(atan(0.5-ringUv.y,ringUv.x-0.5)/6.28318530718+1.0);
    float width=${ringWidth};
    // The line is an annulus; the independent radial gradient shares this draw call.
    float alpha=max(0.0,1.0-d)*0.10*uFill;
    if(abs(d-1.0)<width&&fract(angle*16.0)<=0.6&&angle<uReveal)alpha=uOpacity;
    if(alpha<=0.002)discard;gl_FragColor=vec4(uColor,alpha);${OUTPUT_F}${FOG_F}}`,
});

/** Two-tone flame (or person) glyph on a disc with a 2 px white ring, drawn once to a 64 px canvas. */
function pinTexture(kind: 'fire' | 'person_fall', grey = false): T.Texture {
  const size = 64, canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const context = canvas.getContext('2d')!;
  // The flame glyph fills ~60 % of the disc.
  const glyph = size * 0.6;
  context.beginPath(); context.arc(size / 2, size / 2, size / 2 - 3, 0, Math.PI * 2);
  context.fillStyle = grey ? '#8b95a1' : kind === 'fire' ? '#e2461b' : '#d55e00'; context.fill();
  context.lineWidth = 3; context.strokeStyle = '#ffffff'; context.stroke();
  context.fillStyle = '#ffd27a';
  if (kind === 'fire') {
    const top = (size - glyph) / 2, bottom = top + glyph, half = glyph * 0.42;
    context.beginPath(); context.moveTo(size / 2, top);
    context.bezierCurveTo(size / 2 + half, top + glyph * 0.32, size / 2 + half * 0.8, bottom - glyph * 0.22, size / 2, bottom);
    context.bezierCurveTo(size / 2 - half * 0.8, bottom - glyph * 0.22, size / 2 - half, top + glyph * 0.32, size / 2, top); context.fill();
    context.fillStyle = '#fff1dc'; context.beginPath(); context.moveTo(size / 2, top + glyph * 0.42);
    context.bezierCurveTo(size / 2 + half * 0.4, top + glyph * 0.58, size / 2 + half * 0.32, bottom - glyph * 0.1, size / 2, bottom - glyph * 0.04);
    context.bezierCurveTo(size / 2 - half * 0.32, bottom - glyph * 0.1, size / 2 - half * 0.4, top + glyph * 0.58, size / 2, top + glyph * 0.42); context.fill();
  } else {
    context.beginPath(); context.arc(size / 2, size / 2 - 8, 8, 0, Math.PI * 2); context.fill();
    context.fillRect(size / 2 - 8, size / 2 - 1, 16, 18);
  }
  const texture = new T.CanvasTexture(canvas); texture.colorSpace = T.SRGBColorSpace;
  return texture;
}

let pinTextures: Partial<Record<'fire' | 'person_fall' | 'fire-ended' | 'person_fall-ended', T.Texture>> | undefined;
let pinUsers = 0;

const QUARTER_TURN = new T.Euler();
const ZERO = new T.Vector3();

export class IncidentFX {
  readonly root = new T.Group();
  readonly ready: Promise<void>;
  private quality: Quality;
  private visuals = new Map<string, Visual>();
  private flames = flameMaterial();
  private flameCore = flameMaterial(true);
  private smoke?: T.ShaderMaterial;
  private smokeLightScratch = new T.Color();
  private plane = new T.PlaneGeometry(1, 1);
  private ringGeometry = new T.PlaneGeometry(2, 2).rotateX(-Math.PI / 2);
  private leaderGeometry = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3(0, 14, 0)]);
  private matrix = new T.Matrix4();
  private quaternion = new T.Quaternion();
  private scale = new T.Vector3();
  private position = new T.Vector3();
  private color = new T.Color();
  private vertical = new T.Vector3(0, 1, 0);
  private shown = new Set<string>();
  private reducedMotion?: MediaQueryList;
  private pinScale = 26 / 900;
  private disposed = false;
  constructor(quality: Quality, private options: AtmosphereOptions = { annotations: true }) {
    this.quality = quality;
    if (options.annotations) pinUsers++;
    this.ready = smokeAtlas().then(texture => {
      if (this.disposed) return;
      this.smoke = smokeMaterial();
      this.smoke.uniforms.uMap.value = texture ?? proceduralSmokeAtlas();
      for (const visual of this.visuals.values()) { visual.smoke.material.dispose(); visual.smoke.material = this.smoke; }
      // The atlas usually lands after the first frames; tell the owner's idle
      // renderer to redraw, or the plume stays invisible until the camera moves.
      options.onReady?.();
    });
  }
  /** NDC pin height; update cancels the camera's vertical projection scale as well as distance. */
  setPinScale(heightCssPx: number, _pixelRatio?: number) { this.pinScale = 26 / Math.max(1, heightCssPx) * 2; }
  setQuality(quality: Quality) {
    this.quality = quality;
    // Low quality drops the PointLight (spec §3.7).
    for (const visual of this.visuals.values()) if (visual.light) visual.light.visible = false;
  }
  private create(row: Row): Visual {
    const unit = new T.Group(); unit.name = `fx-incident-${row.id}`;
    const flameCount = Math.round(FLAME_MIN + fxHash(row.seed, 1) * (FLAME_MAX - FLAME_MIN));
    // Two meshes per fire: the NormalBlending body, then the smaller additive core pass.
    const flames = new T.InstancedMesh(this.plane, this.flames.clone(), flameCount);
    flames.count = flameCount; flames.frustumCulled = false; flames.renderOrder = 4;
    const flameCoreMesh = new T.InstancedMesh(this.plane, this.flameCore.clone(), flameCount);
    flameCoreMesh.count = flameCount; flameCoreMesh.frustumCulled = false; flameCoreMesh.renderOrder = 4;
    // Per-fire puff storage: instance attributes ride a private geometry clone.
    const capacity = SMOKE_HIGH;
    const geometry = new T.InstancedBufferGeometry();
    geometry.index = this.plane.index; geometry.attributes.position = this.plane.attributes.position; geometry.attributes.uv = this.plane.attributes.uv;
    const tint = new T.InstancedBufferAttribute(new Float32Array(capacity * 4), 4);
    tint.setUsage(T.DynamicDrawUsage);
    geometry.setAttribute('puffTint', tint);
    const cells = new T.InstancedBufferAttribute(new Float32Array(capacity), 1);
    cells.setUsage(T.DynamicDrawUsage); geometry.setAttribute('puffCell', cells);
    const smoke = new T.InstancedMesh(geometry, this.smoke ?? new T.MeshBasicMaterial({ visible: false }), capacity);
    smoke.count = 0; smoke.frustumCulled = false; smoke.renderOrder = 2;
    // flameCore after smoke keeps the InstancedMesh order [flames, smoke, core].
    unit.add(flames, smoke, flameCoreMesh);
    const visual: Visual = { unit, seed: row.seed, flames, flameCore: flameCoreMesh, smoke, tint, cells, active: false };
    if (row.kind === 'fire') {
      const glow = new T.Mesh(this.plane, glowMaterial());
      glow.rotation.x = -Math.PI / 2; glow.renderOrder = 3; glow.visible = false;
      unit.add(glow); visual.glow = glow;
    }
    if (this.options.annotations) {
      pinTextures ??= { fire: pinTexture('fire'), person_fall: pinTexture('person_fall'), 'fire-ended': pinTexture('fire', true), 'person_fall-ended': pinTexture('person_fall', true) };
      const ring = new T.Mesh(this.ringGeometry, ringMaterial(false)); ring.renderOrder = 3;
      // Occluded pass: same geometry, GreaterDepth — the boundary reads faintly through buildings.
      const ringOccluded = new T.Mesh(this.ringGeometry, ringMaterial(true)); ringOccluded.renderOrder = 3;
      const pin = new T.Sprite(new T.SpriteMaterial({ map: pinTextures[row.kind], sizeAttenuation: false, depthTest: false, transparent: true, fog: false }));
      pin.renderOrder = 12; pin.center.set(0.5, 0.5); pin.position.y = 14;
      const leader = new T.Line(this.leaderGeometry, new T.LineBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.6, depthTest: false, fog: false }));
      leader.renderOrder = 11; leader.frustumCulled = false;
      const label = this.options.preview ? document.createElement('span') : undefined;
      if (label) { label.className = 'entity-label'; label.dataset.entityId = row.id; this.options.preview!.overlay.append(label); }
      visual.annotation = { ring, ringOccluded, pin, leader, label }; unit.add(ring, ringOccluded, pin, leader);
    }
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
    this.flameRamp = 0;
    if (!rows.length && !this.visuals.size) return;
    const shown = this.shown; shown.clear();
    const reduced = (this.reducedMotion ??= matchMedia('(prefers-reduced-motion: reduce)')).matches;
    const pinScale = this.pinScale / camera.projectionMatrix.elements[5];
    camera.updateMatrixWorld();
    for (const row of rows) {
      // A run's pending incidents allocate nothing. Only the draft shows their grey pins.
      if (row.phase === 'pending' && !this.options.preview) continue;
      shown.add(row.id);
      const visual = this.visuals.get(row.id) ?? this.create({ ...row, seed: fxSeed(row.id) }), seed = visual.seed;
      visual.unit.visible = true;
      const pending = row.phase === 'pending';
      const ended = row.phase === 'ended';
      worldPosition(row.position, 'enu', undefined, this.position).sub(origin ?? ZERO);
      // The unit sits at ground level — ring and fill are ground decals. Only the flames,
      // glow and smoke base ride the roof height.
      visual.unit.position.copy(this.position);
      const roofY = Math.max(roofHeight?.(row.position[0], row.position[1]) ?? -(origin?.y ?? 0), this.position.y);
      const baseY = roofY - visual.unit.position.y;
      const baseHeight = T.MathUtils.clamp(8 * Math.sqrt(row.radiusM / 30), 5, 12);
      // The procedural mask only fills part of its quad. At overview distances its
      // former 2–4 raster pixels disappeared under the screen-space pin. Size the same
      // billboards from projected depth, retaining the physical sizes when they read.
      this.position.copy(visual.unit.position); this.position.y += baseY + baseHeight / 2;
      const depth = -this.position.applyMatrix4(camera.matrixWorldInverse).z;
      // Upright flame quads foreshorten as the operator pitches down; include that
      // projection so the top view keeps the same floor as the oblique overview.
      const verticalProjection = Math.max(0.25, Math.hypot(camera.matrixWorld.elements[8], camera.matrixWorld.elements[10]));
      const displayScale = Math.max(1, depth * pinScale * FLAME_MIN_CSS_PX / 26 / baseHeight / verticalProjection);
      const flameTop = baseHeight * displayScale;
      // Ring draw-in: 0.6 s wall-clock, once per activation (spec §5). Grey tint after ending.
      const start = ringStart.get(row.id), drawIn = !reduced && start !== undefined ? Math.min(1, (performance.now() - start) / 600) : 1;
      if (visual.annotation) {
        const { ring, ringOccluded, pin, label } = visual.annotation, ringMat = ring.material;
        ring.position.y = -(origin?.y ?? 0) - visual.unit.position.y + 0.15;
        if (ringOccluded) ringOccluded.position.y = ring.position.y;
        pin.position.y = baseY + 14;
        visual.annotation.leader.position.y = baseY;
        const ringColor = ended ? ENDED : row.kind === 'fire' ? AREA : '#d55e00';
        const ringOpacity = ended ? 0.5 : 0.85, ringFill = ended ? 0 : drawIn;
        ring.visible = !pending;
        ringMat.uniforms.uColor.value.set(ringColor);
        ringMat.uniforms.uOpacity.value = ringOpacity;
        ringMat.uniforms.uRadius.value = row.radiusM;
        ringMat.uniforms.uFill.value = ringFill;
        ringMat.uniforms.uReveal.value = drawIn;
        const extent = row.radiusM + Math.max(1.5, row.radiusM * 0.15);
        ringMat.uniforms.uExtent.value = extent; ring.scale.setScalar(extent);
        if (ringOccluded) {
          ringOccluded.visible = ring.visible;
          const m = ringOccluded.material;
          m.uniforms.uColor.value.set(ringColor);
          m.uniforms.uOpacity.value = ended ? 0.12 : 0.22;
          m.uniforms.uRadius.value = row.radiusM;
          m.uniforms.uFill.value = 0;
          m.uniforms.uReveal.value = drawIn;
          m.uniforms.uExtent.value = extent; ringOccluded.scale.setScalar(extent);
        }
        const pinKind = pending || ended ? `${row.kind}-ended` as const : row.kind;
        pin.material.map = pinTextures![pinKind]!;
        pin.scale.set(pinScale, pinScale, 1);
        if (label && this.options.preview) {
          const overlay = this.options.preview.overlay;
          label.textContent = incidentLabel(row, this.options.preview.locale().startsWith('zh'));
          this.position.copy(visual.unit.position); this.position.y += pin.position.y + 2; this.position.project(camera);
          label.hidden = this.position.z < -1 || this.position.z > 1 || Math.abs(this.position.x) > 0.96 || Math.abs(this.position.y) > 0.96;
          label.style.left = `${(this.position.x + 1) * overlay.clientWidth / 2}px`;
          label.style.top = `${(1 - this.position.y) * overlay.clientHeight / 2 - 14}px`;
        }
      }
      // Flames: ramp 3 s in sim time after activation, fade 4 s after ending.
      const ramp = pending ? 0 : ended ? Math.max(0, 1 - row.sinceS / 4) : Math.min(1, row.sinceS / 3);
      const burning = row.kind === 'fire' && ramp > 0;
      visual.active = row.kind === 'fire' && row.phase === 'active';
      visual.flames.visible = burning; visual.flameCore.visible = burning;
      if (visual.glow) {
        visual.glow.visible = burning;
        if (burning) {
          visual.glow.material.uniforms.uTime.value = simSeconds;
          visual.glow.material.uniforms.uRamp.value = ramp;
          const glowRadius = Math.max(row.radiusM * 0.35, 3);
          visual.glow.scale.setScalar(glowRadius * 2);
          visual.glow.position.y = baseY + 0.1;
        }
      }
      if (burning) {
        this.flameRamp = Math.max(this.flameRamp, ramp);
        this.setFlameTop(baseY + flameTop);
        for (const mesh of [visual.flames, visual.flameCore] as T.InstancedMesh<T.PlaneGeometry, T.ShaderMaterial>[]) {
          mesh.material.uniforms.uTime.value = simSeconds;
          mesh.material.uniforms.uRamp.value = ramp;
        }
        // Physical height 5–12 m scaled by sqrt(radius/30), with a projected-size floor;
        // cluster radius min(0.2·R, 6 m); width 0.8 × height.
        const cluster = Math.min(0.2 * row.radiusM, 6);
        for (let i = 0; i < visual.flames.count; i++) {
          const angle = fxHash(seed, i * 4 + 2) * Math.PI * 2, radius = fxHash(seed, i * 4 + 3) * cluster;
          // Camera-facing billboards, each rotated about its own vertical axis.
          this.quaternion.copy(camera.quaternion);
          QUARTER_TURN.setFromQuaternion(this.quaternion, 'YXZ');
          this.quaternion.setFromAxisAngle(this.vertical, QUARTER_TURN.y + angle + Math.sin(simSeconds * 0.6 + i) * 0.15);
          const height = T.MathUtils.clamp(baseHeight * (0.8 + 0.4 * fxHash(seed, i * 4 + 4)), 5, 12) * displayScale;
          this.scale.set(height * 0.8, height, 1);
          this.position.set(Math.cos(angle) * radius, roofY - visual.unit.position.y + this.scale.y / 2, Math.sin(angle) * radius);
          this.matrix.compose(this.position, this.quaternion, this.scale);
          visual.flames.setMatrixAt(i, this.matrix);
          this.scale.set(this.scale.x * 0.35, this.scale.y * 0.55, 1);
          this.position.y = baseY + this.scale.y / 2;
          this.matrix.compose(this.position, this.quaternion, this.scale);
          visual.flameCore.setMatrixAt(i, this.matrix);
        }
        visual.flames.instanceMatrix.needsUpdate = true;
        visual.flameCore.instanceMatrix.needsUpdate = true;
      }
      if (row.kind === 'fire' && !pending) this.writeSmoke(visual, seed, simSeconds, camera, row, baseY + flameTop);
      else { visual.smoke.visible = false; visual.smoke.count = 0; }
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
  private writeSmoke(visual: Visual, seed: number, simSeconds: number, camera: T.Camera, row: Conditions['incidents'][number], baseHeight: number) {
    if (!this.smoke) { visual.smoke.visible = false; return; }
    const capacity = visual.smoke.instanceMatrix.count;
    const windEast = this.wind.x, windNorth = this.wind.y;
    const high = this.quality !== 'low';
    const rate = SMOKE_RATE, lifetime = high ? SMOKE_LIFE_HIGH_S : SMOKE_LIFE_LOW_S;
    const limit = high ? SMOKE_HIGH : SMOKE_LOW;
    const ended = row.phase === 'ended';
    const elapsed = row.activeS, window = ended ? row.activeS - row.sinceS : row.activeS;
    let count = 0;
    for (let slot = 0; slot < limit && count < capacity; slot++) {
      const born = slot / rate;
      if (window < born) break;
      // Equivalent to floor((window - born) / (limit / RATE)), without subtractive
      // rounding losing the emission exactly at a low-quality period boundary.
      const k = Math.floor((window * rate - slot) / limit);
      // A period shorter than the lifetime lets both k and k-1 survive.
      for (let cycle = k; cycle >= Math.max(0, k - 1) && count < capacity; cycle--) {
        const age = elapsed - (slot + cycle * limit) / rate;
        if (age < 0 || age >= lifetime) continue;
        this.writePuff(visual, count++, seed, slot, age, lifetime, simSeconds, camera, windEast, windNorth, baseHeight);
      }
    }
    visual.smoke.count = count;
    visual.smoke.geometry.instanceCount = count;
    visual.smoke.instanceMatrix.needsUpdate = true;
    visual.tint.needsUpdate = true; visual.cells.needsUpdate = true;
    visual.smoke.visible = count > 0;
  }
  private writePuff(visual: Visual, count: number, seed: number, slot: number, age: number, lifetime: number, simSeconds: number, camera: T.Camera, windEast: number, windNorth: number, baseHeight: number) {
    // Low quality cuts the plume short; it must not age its colour/opacity 24/9 times
    // faster while rise and size still use seconds on the high-quality clock.
    const life = age / SMOKE_LIFE_HIGH_S;
    const seedA = fxHash(seed, slot * 8 + 1), seedB = fxHash(seed, slot * 8 + 2), seedC = fxHash(seed, slot * 8 + 3);
    // Rise ~2.6 m/s slowing with height: ≥ 15 m before the column bends downwind.
    // Drift (0.55 × wind) ramps in with min(1, age/6), so young puffs climb straight out of the flames
    // and the column stays dense near the fire instead of streaming away as thin wisps.
    const risen = 2.6 * age * (1 - 0.2 * age / SMOKE_LIFE_HIGH_S), drift = 0.55 * Math.min(1, age / 6) * age;
    const jitterX = (seedA - 0.5) * 6 + Math.sin(simSeconds * 0.2 + seedB * 6) * 1.5;
    const jitterZ = (seedB - 0.5) * 6 + Math.cos(simSeconds * 0.17 + seedC * 6) * 1.5;
    // Dense dark root (finding 8): in the first 3 s the puff grows 5 → 12 m just above the flames.
    const size = age < 3 ? 5 + (12 - 5) * (age / 3) : 12 + (30 - 12) * ((age - 3) / (SMOKE_LIFE_HIGH_S - 3));
    this.quaternion.copy(camera.quaternion);
    QUARTER_TURN.setFromQuaternion(this.quaternion, 'YXZ');
    QUARTER_TURN.z = simSeconds * (0.1 + seedC * 0.2) + seedA * Math.PI * 2;
    this.quaternion.setFromEuler(QUARTER_TURN);
    this.position.set(jitterX + windEast * drift, baseHeight + risen, jitterZ - windNorth * drift);
    this.scale.set(size, size, 1);
    this.matrix.compose(this.position, this.quaternion, this.scale);
    visual.smoke.setMatrixAt(count, this.matrix);
    const tint = life < 0.5 ? this.color.copy(SMOKE_BASE).lerp(SMOKE_MID, life * 2) : this.color.copy(SMOKE_MID).lerp(SMOKE_HIGH_TONE, (life - 0.5) * 2);
    // Dense root (0.62 for 3 s), body held at 0.5 to 45 % of life, then a linear fade as it lightens.
    let alpha = age < 3 ? 0.62 - 0.12 * (age / 3) : life < 0.45 ? 0.5 : 0.5 * (1 - (life - 0.45) / 0.55);
    // Keep the dark body until the final 3 s of the shorter low-quality lifetime.
    if (lifetime < SMOKE_LIFE_HIGH_S) alpha *= 1 - T.MathUtils.smoothstep(age, lifetime - 3, lifetime);
    visual.tint.setXYZW(count, tint.r, tint.g, tint.b, alpha);
    visual.cells.setX(count, Math.floor(fxHash(seed, slot * 8 + 4) * 4));
  }
  setWind(east: number, north: number) { this.wind.set(east, north); }
  /** Art-directed smoke lighting: scattered ambient colour, day/dusk scatter mix and the
   *  fire underlight strength (which fades with the flame ramp). No allocation per frame. */
  setSmokeLight(dusk: boolean, rain: number) {
    if (!this.smoke) return;
    const u = this.smoke.uniforms;
    u.uSmokeLight.value.copy(this.smokeLightScratch.copy(dusk ? SMOKE_LIGHT_DUSK : SMOKE_LIGHT_DAY).lerp(SMOKE_LIGHT_RAIN, 0.3 * Math.min(1, Math.max(0, rain))));
    u.uScatter.value = dusk ? 0.55 : 0.25;
    // The underlight fades with the flames: the ramp comes from this frame's fires.
    u.uGlowStrength.value = (dusk ? 0.55 : 0.25) * this.flameRamp;
  }
  setFlameTop(heightM: number) { if (this.smoke) this.smoke.uniforms.uFlameTop.value = heightM; }
  /** Max flame ramp across this frame's burning fires (0 once the last fire ends). */
  private flameRamp = 0;
  private wind = new T.Vector2();
  private disposeVisual(visual: Visual) {
    visual.unit.removeFromParent(); visual.flames.material.dispose();
    visual.flames.dispose(); visual.flameCore.material.dispose(); visual.flameCore.dispose(); visual.smoke.dispose(); visual.glow?.material.dispose();
    visual.smoke.geometry.dispose(); if (visual.smoke.material !== this.smoke) visual.smoke.material.dispose();
    if (visual.annotation) {
      visual.annotation.ring.material.dispose(); visual.annotation.ringOccluded?.material.dispose(); visual.annotation.pin.material.dispose(); visual.annotation.leader.material.dispose(); visual.annotation.label?.remove();
    }
    visual.light?.dispose();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const visual of this.visuals.values()) {
      this.disposeVisual(visual);
    }
    this.visuals.clear();
    this.flames.dispose(); this.flameCore.dispose(); this.smoke?.dispose();
    this.plane.dispose(); this.ringGeometry.dispose(); this.leaderGeometry.dispose();
    if (this.options.annotations && --pinUsers === 0) {
      for (const texture of Object.values(pinTextures ?? {})) texture?.dispose();
      pinTextures = undefined;
    }
  }
}
